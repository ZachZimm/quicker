import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .catalog import catalog, import_catalog, route_list
from .contracts import ModelConfig, ReviewAction
from .db import (
    ArchiveReceipt,
    Audit,
    BrowserSession,
    Candidate,
    Database,
    Device,
    Document,
    ExtractionAttempt,
    Job,
    LoginAttempt,
    Page,
    PairCode,
    Route,
    Setting,
    User,
)
from .documents import MAX_FILE_BYTES, ingest, model_settings, pages_for, prepare_image, serialize_page
from .extraction import ModelError, VisionAdapter
from .review import ReviewError, apply_action, serialize
from .security import digest, password_hash, password_matches


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1024)


class Pairing(BaseModel):
    code: str = Field(max_length=100)
    name: str = Field(min_length=1, max_length=100)


class ArchiveAck(BaseModel):
    page_id: str
    sha256: str


class RouteInput(BaseModel):
    property: str = Field(min_length=1, max_length=200)
    year: int = Field(ge=1900, le=2200)
    account: str


DUMMY_HASH = password_hash("unused timing reference")


def create_app(database=None):
    db = database or Database()

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        yield

    app = FastAPI(title="Quicker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db

    @app.exception_handler(ReviewError)
    async def review_error(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authenticated(request: Request):
        bearer = request.headers.get("authorization", "")
        with db.session() as session:
            if bearer.startswith("Bearer "):
                device = session.scalar(
                    select(Device).where(Device.token_hash == digest(bearer[7:]), Device.revoked.is_(False))
                )
                if not device:
                    raise HTTPException(401, "Device credential is invalid or revoked")
                path = request.url.path
                if not (
                    path.startswith("/api/device/")
                    or path == "/api/upload"
                    or path == "/api/entry"
                    or (path.startswith("/api/pages/") and path.endswith("/original"))
                ):
                    raise HTTPException(403, "This operation requires a browser login")
                return {"device_id": device.id}
            token = request.cookies.get("quicker_session", "")
            stored = session.get(BrowserSession, digest(token)) if token else None
            if not stored or stored.expires < int(time.time()):
                raise HTTPException(401, "Sign in to continue")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and origin != str(request.base_url).rstrip("/"):
                    raise HTTPException(403, "Request origin does not match")
                if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), stored.csrf):
                    raise HTTPException(403, "Session verification failed. Reload and try again.")
            return {"csrf": stored.csrf}

    Auth = Annotated[dict, Depends(authenticated)]

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/login")
    def login(body: Login, request: Request):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Request origin does not match")
        address = request.client.host if request.client else "unknown"
        now = int(time.time())
        with db.write() as session:
            attempt = session.get(LoginAttempt, address)
            if attempt and now - attempt.started < 900 and attempt.count >= 10:
                raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
            user = session.get(User, body.username)
            valid = (
                password_matches(body.password, user.password if user else DUMMY_HASH) and user is not None
            )
            if not valid:
                if not attempt:
                    session.add(LoginAttempt(address=address, count=1, started=now))
                elif now - attempt.started >= 900:
                    attempt.count, attempt.started = 1, now
                else:
                    attempt.count += 1
            else:
                if attempt:
                    session.delete(attempt)
                session.execute(delete(BrowserSession).where(BrowserSession.expires < now))
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                session.add(BrowserSession(token_hash=digest(token), csrf=csrf, expires=now + 43200))
        if not valid:
            raise HTTPException(401, "Username or password is incorrect")
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "quicker_session",
            token,
            httponly=True,
            samesite="strict",
            max_age=43200,
            secure=os.environ.get("QUICKER_COOKIE_SECURE", "false").lower() == "true",
        )
        return response

    @app.get("/api/session")
    def whoami(auth: Auth):
        return auth

    @app.post("/api/logout")
    def logout(request: Request, auth: Auth):
        with db.write() as session:
            session.execute(
                delete(BrowserSession).where(
                    BrowserSession.token_hash == digest(request.cookies.get("quicker_session", ""))
                )
            )
        response = JSONResponse({"ok": True})
        response.delete_cookie("quicker_session")
        return response

    @app.get("/api/settings")
    def settings(auth: Auth):
        with db.session() as session:
            cfg = model_settings(session).model_dump()
        has_key = bool(cfg.pop("api_key", None))
        return {**cfg, "has_api_key": has_key}

    @app.put("/api/settings")
    def save_settings(body: ModelConfig, auth: Auth):
        with db.write() as session:
            previous = model_settings(session)
            if body.revision != previous.revision:
                raise HTTPException(409, "Settings changed. Reload before saving.")
            if body.api_key is None:
                body.api_key = previous.api_key
            body.revision = previous.revision + 1
            current = session.get(Setting, "model")
            if current:
                current.value = body.model_dump()
            else:
                session.add(Setting(key="model", value=body.model_dump()))
        return {"ok": True, "revision": body.revision}

    @app.post("/api/settings/check")
    def check_settings(auth: Auth):
        with db.session() as session:
            config = model_settings(session)
        try:
            return VisionAdapter(config).check()
        except ModelError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/catalog")
    def get_catalog(auth: Auth):
        with db.session() as session:
            return {**catalog(session), "routes": route_list(session)}

    @app.post("/api/catalog")
    async def upload_catalog(auth: Auth, file: Annotated[UploadFile, File()]):
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "QIF export exceeds 10 MB")
        try:
            with db.write() as session:
                ref = import_catalog(session, content.decode("utf-8-sig", errors="replace"))
                return {"counts": {key: len(value) for key, value in ref.items()}}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/routes")
    def save_routes(body: list[RouteInput], auth: Auth):
        if len({(r.property, r.year) for r in body}) != len(body):
            raise HTTPException(422, "Each property/year can have only one account")
        with db.write() as session:
            names = {a["name"] for a in catalog(session)["accounts"]}
            if any(r.account not in names for r in body):
                raise HTTPException(422, "Choose existing accounts from the catalog")
            session.execute(delete(Route))
            session.add_all(Route(**r.model_dump()) for r in body)
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(
        auth: Auth,
        files: Annotated[list[UploadFile], File()],
        request_id: str = Form(),
        grouped: bool = Form(False),
    ):
        if not 1 <= len(request_id) <= 100:
            raise HTTPException(422, "An upload request ID is required")
        if len(files) > 20:
            raise HTTPException(413, "Upload at most 20 images at a time")
        content = []
        total = 0
        for file in files:
            data = await file.read(MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > 150 * 1024 * 1024:
                raise HTTPException(413, "Limit uploads to 50 MB per image and 150 MB per batch")
            content.append((file.filename or "photo", data))
        try:
            ids = ingest(db, content, request_id, grouped)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with db.session() as session:
            return {
                "document_ids": ids,
                "pages": [serialize_page(p) for doc in ids for p in pages_for(session, doc)],
                "stored": True,
            }

    @app.get("/api/documents")
    def documents(auth: Auth):
        with db.session() as session:
            all_pages = list(session.scalars(select(Page)))
            counts = {}
            for page in all_pages:
                counts[page.sha256] = counts.get(page.sha256, 0) + 1
            return [
                {
                    "id": d.id,
                    "name": d.name,
                    "status": d.status,
                    "created": d.created,
                    "error": d.error,
                    "ignored": d.ignored,
                    "pages": [serialize_page(p) for p in all_pages if p.document_id == d.id],
                    "repeated_content": any(counts[p.sha256] > 1 for p in all_pages if p.document_id == d.id),
                }
                for d in session.scalars(select(Document).order_by(Document.created.desc()))
            ]

    @app.get("/api/documents/{document_id}/attempts")
    def attempts(document_id: str, auth: Auth):
        with db.session() as session:
            return [
                {"id": a.id, "created": a.created, "config_revision": a.config_revision, "result": a.result}
                for a in session.scalars(
                    select(ExtractionAttempt)
                    .where(ExtractionAttempt.document_id == document_id)
                    .order_by(ExtractionAttempt.created)
                )
            ]

    @app.post("/api/documents/{document_id}/retry")
    def retry(document_id: str, auth: Auth):
        with db.write() as session:
            doc = session.get(Document, document_id)
            if not doc:
                raise HTTPException(404, "Document not found")
            if doc.status not in ("failed", "ready"):
                raise HTTPException(409, "Document is already queued or processing")
            if session.scalar(select(Candidate.id).where(Candidate.document_id == document_id).limit(1)):
                raise HTTPException(
                    409,
                    "This document has proposed transactions. Edit them in review; extraction cannot overwrite your work.",
                )
            doc.status, doc.error = "queued", None
            session.add(
                Job(
                    id=str(uuid4()),
                    document_id=doc.id,
                    status="queued",
                    created=int(time.time()),
                    config=model_settings(session).model_dump(),
                    attempts=0,
                    lease_until=0,
                    available=0,
                )
            )
        return {"ok": True}

    @app.get("/api/pages/{page_id}/original")
    def original(page_id: str, auth: Auth):
        with db.session() as session:
            page = session.get(Page, page_id)
            if not page:
                raise HTTPException(404, "Page not found")
            return FileResponse(
                db.blobs / page.sha256,
                filename=page.name,
                media_type=page.mime,
                headers={"ETag": f'"{page.sha256}"'},
            )

    @app.get("/api/pages/{page_id}/preview")
    def preview(page_id: str, auth: Auth):
        with db.session() as session:
            page = session.get(Page, page_id)
            if not page:
                raise HTTPException(404, "Page not found")
            try:
                return Response(prepare_image(db.blobs / page.sha256), media_type="image/jpeg")
            except Exception as exc:
                raise HTTPException(422, "Preview could not be generated; download the original") from exc

    @app.get("/api/transactions")
    def transactions(auth: Auth):
        with db.session() as session:
            return [
                serialize(session, c)
                for c in session.scalars(
                    select(Candidate)
                    .join(Document)
                    .order_by(
                        Document.created.desc(),
                        Document.id,
                        Candidate.data["source_index"].as_integer(),
                        Candidate.id,
                    )
                )
            ]

    @app.post("/api/review")
    def review(body: ReviewAction, auth: Auth):
        with db.write() as session:
            return apply_action(session, body)

    @app.get("/api/transactions/{candidate_id}/history")
    def history(candidate_id: str, auth: Auth):
        with db.session() as session:
            return [
                {"created": a.created, "action": a.action, "snapshot": a.snapshot}
                for a in session.scalars(
                    select(Audit).where(Audit.candidate_id == candidate_id).order_by(Audit.created)
                )
            ]

    @app.get("/api/devices")
    def devices(auth: Auth):
        with db.session() as session:
            return [
                {
                    "id": d.id,
                    "name": d.name,
                    "connected": d.heartbeat > time.time() - 30,
                    "heartbeat": d.heartbeat,
                    "entry_supported": False,
                    "pending_archives": len(list(session.scalars(select(Page.id))))
                    - len(
                        list(
                            session.scalars(
                                select(ArchiveReceipt.page_id).where(ArchiveReceipt.device_id == d.id)
                            )
                        )
                    ),
                }
                for d in session.scalars(select(Device).where(Device.revoked.is_(False)))
            ]

    @app.post("/api/pair-code")
    def pair_code(auth: Auth):
        code = secrets.token_urlsafe(12)
        with db.write() as session:
            session.execute(delete(PairCode).where(PairCode.expires < time.time()))
            session.add(PairCode(token_hash=digest(code), expires=int(time.time()) + 600))
        return {"code": code, "expires_in": 600}

    @app.post("/api/pair")
    def pair(body: Pairing):
        with db.write() as session:
            code = session.get(PairCode, digest(body.code))
            if not code or code.expires < time.time():
                raise HTTPException(401, "Pairing code is invalid or expired")
            if session.scalar(select(Device.id).where(Device.revoked.is_(False)).limit(1)):
                raise HTTPException(409, "Revoke the existing companion before pairing another")
            session.delete(code)
            token, device_id = secrets.token_urlsafe(32), str(uuid4())
            session.add(
                Device(
                    id=device_id,
                    name=body.name,
                    token_hash=digest(token),
                    heartbeat=int(time.time()),
                    revoked=False,
                )
            )
        return {"device_id": device_id, "token": token}

    @app.delete("/api/devices/{device_id}")
    def revoke(device_id: str, auth: Auth):
        with db.write() as session:
            device = session.get(Device, device_id)
            if not device:
                raise HTTPException(404, "Device not found")
            device.revoked = True
        return {"ok": True}

    def require_device(auth):
        if not auth.get("device_id"):
            raise HTTPException(403, "Device credential required")
        return auth["device_id"]

    @app.post("/api/device/heartbeat")
    def heartbeat(auth: Auth):
        device_id = require_device(auth)
        with db.write() as session:
            session.get(Device, device_id).heartbeat = int(time.time())
            count = len(list(session.scalars(select(Candidate.id).where(Candidate.status == "approved"))))
        return {
            "approved": count,
            "entry_supported": False,
            "entry_message": "Quicken entry is not implemented yet. Approved transactions remain in review storage.",
        }

    @app.get("/api/device/archive")
    def archive_manifest(auth: Auth):
        device_id = require_device(auth)
        with db.session() as session:
            archived = select(ArchiveReceipt.page_id).where(ArchiveReceipt.device_id == device_id)
            return [
                serialize_page(p)
                for p in session.scalars(select(Page).where(Page.id.not_in(archived)).limit(100))
            ]

    @app.post("/api/device/archive")
    def archive_ack(body: ArchiveAck, auth: Auth):
        device_id = require_device(auth)
        with db.write() as session:
            page = session.get(Page, body.page_id)
            if not page or page.sha256 != body.sha256:
                raise HTTPException(422, "Archive checksum does not match the server document")
            if not session.get(ArchiveReceipt, (device_id, page.id)):
                session.add(ArchiveReceipt(device_id=device_id, page_id=page.id, received=int(time.time())))
        return {"ok": True}

    @app.post("/api/entry")
    def enter(auth: Auth):
        # Deliberately no delivery state is mutated until a real Quicken adapter exists.
        raise HTTPException(
            501,
            "Quicken entry is not implemented yet. Approved transactions have not been entered or changed.",
        )

    web_dist = Path(os.environ.get("QUICKER_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app
