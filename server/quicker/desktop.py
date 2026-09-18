"""Serialized desktop delivery, freshness receipts, and conservative recovery."""

import copy
from time import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Body, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from quicker_client.qif import decode_qif, marker, matches, render_entry
from sqlalchemy import select

from .catalog import catalog, parse_qif
from .db import Candidate, DesktopRun, Device, ExportEvent, ReferenceExport, Setting
from .documents import durable_blob
from .reference_exports import MAX_EXPORT_BYTES, activate, reduced_coverage
from .review import audit, entry_eligible
from .units import quicken_tags


def generation(session):
    row = session.get(Setting, "reference_generation")
    return row.value["value"] if row else 0


def capability(session, device_id):
    row = session.get(Setting, "desktop:" + device_id)
    return row.value if row else {}


def describe(run):
    return {"id": run.id, "status": run.status, "created": run.created, **run.payload}


class Start(BaseModel):
    request_id: UUID
    kind: Literal["entry", "refresh"] = "entry"
    background: bool = False


class Owner(BaseModel):
    owner: UUID


class Claim(Owner):
    event_id: UUID


class Failure(Owner):
    message: str = Field(max_length=1000)


class EventInput(BaseModel):
    id: UUID
    run_id: UUID
    owner: UUID
    purpose: Literal["pre", "post", "refresh"]
    file_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_generation: int = Field(ge=0)
    started: float
    completed: float


def install(app, db, authenticated, require_device):
    Auth = Annotated[dict, Depends(authenticated)]

    def owned(session, run_id, auth, owner=None):
        device_id = require_device(auth)
        run = session.get(DesktopRun, run_id)
        if not run or run.device_id != device_id:
            raise HTTPException(404, "Desktop run not found")
        if owner is not None and run.payload.get("owner") != str(owner):
            raise HTTPException(409, "This run belongs to a different companion instance")
        return run

    @app.post("/api/entry")
    def start(auth: Auth, body: Start | None = None):
        # Old clients must never turn an empty legacy request into a desktop action.
        if body is None:
            raise HTTPException(501, "Update the companion to request an entry run")
        with db.write() as session:
            old = session.get(DesktopRun, str(body.request_id))
            if old:
                if auth.get("device_id") and old.device_id != auth["device_id"]:
                    raise HTTPException(403, "Run belongs to a different device")
                return describe(old)
            device = session.scalar(select(Device).where(Device.revoked.is_(False)))
            if not device or device.heartbeat < time() - 35:
                raise HTTPException(409, "Connect the Windows companion first")
            cap = capability(session, device.id)
            if cap.get("protocol") != 1 or not cap.get("file_identity"):
                raise HTTPException(409, "Configure the Quicken data file in the updated companion")
            active = session.scalar(
                select(DesktopRun).where(DesktopRun.status.not_in(["complete", "failed"]))
            )
            if active:
                if active.device_id != device.id or active.payload["file_identity"] != cap["file_identity"]:
                    raise HTTPException(409, "Resolve the previous device/data-file run first")
                if body.kind == "refresh":
                    active.payload = {**active.payload, "requested": True, "background": body.background}
                return describe(active)
            rows = (
                [
                    {"id": c.id, "revision": c.revision}
                    for c in session.scalars(select(Candidate).where(Candidate.status == "approved"))
                ]
                if body.kind == "entry"
                else []
            )
            run = DesktopRun(
                id=str(body.request_id),
                device_id=device.id,
                status="queued",
                created=int(time()),
                payload={
                    "kind": body.kind,
                    "file_identity": cap["file_identity"],
                    "file_name": cap.get("file_name"),
                    "requested": True,
                    "background": body.background,
                    "rows": rows,
                    "items": [],
                    "message": "Waiting for Quicken",
                },
            )
            session.add(run)
            return describe(run)

    @app.get("/api/entry")
    def status(auth: Auth):
        with db.session() as session:
            query = select(DesktopRun).order_by(DesktopRun.created.desc()).limit(20)
            if auth.get("device_id"):
                query = query.where(DesktopRun.device_id == auth["device_id"])
            return [describe(r) for r in session.scalars(query)]

    @app.get("/api/device/operations")
    def pending(auth: Auth):
        device_id = require_device(auth)
        with db.session() as session:
            return [
                describe(r)
                for r in session.scalars(
                    select(DesktopRun).where(
                        DesktopRun.device_id == device_id, DesktopRun.status.not_in(["complete", "failed"])
                    )
                )
                if r.payload.get("requested")
            ]

    @app.post("/api/device/operations/{run_id}/take")
    def take(run_id: str, body: Owner, auth: Auth):
        with db.write() as session:
            run = owned(session, run_id, auth)
            p = copy.deepcopy(run.payload)
            if p.get("owner") and p["owner"] != str(body.owner):
                raise HTTPException(409, "A different companion owns this run")
            if run.status in ("complete", "failed"):
                raise HTTPException(409, "Run is already finished")
            p["owner"] = str(body.owner)
            run.payload = p
            return {**describe(run), "generation": generation(session)}

    @app.post("/api/device/export-events")
    async def exported(auth: Auth, metadata: Annotated[str, Form()], file: Annotated[UploadFile, File()]):
        try:
            meta = EventInput.model_validate_json(metadata)
        except ValueError as exc:
            raise HTTPException(422, "Invalid export metadata") from exc
        content = await file.read(MAX_EXPORT_BYTES + 1)
        if not content or len(content) > MAX_EXPORT_BYTES:
            raise HTTPException(422, "QIF export must be between 1 byte and 10 MB")
        try:
            ref = parse_qif(decode_qif(content))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        sha = durable_blob(db, content)
        data = meta.model_dump(mode="json")
        with db.write() as session:
            run = owned(session, str(meta.run_id), auth, meta.owner)
            previous = session.get(ExportEvent, str(meta.id))
            if previous:
                if (
                    previous.device_id != run.device_id
                    or previous.sha256 != sha
                    or previous.payload["input"] != data
                ):
                    raise HTTPException(409, "Export request ID was reused for different data")
                return previous.payload["receipt"]
            p = copy.deepcopy(run.payload)
            if meta.file_identity != p["file_identity"]:
                raise HTTPException(409, "Export belongs to a different Quicken data file")
            if (
                meta.completed < meta.started
                or meta.completed - meta.started > 300
                or abs(time() - meta.completed) > 600
            ):
                raise HTTPException(409, "Export timestamps are stale or invalid; create a fresh export")
            reasons = []
            if generation(session) != meta.expected_generation:
                reasons.append("Reference changed during export; refresh again")
            if reduced_coverage(catalog(session), ref):
                reasons.append(
                    "Export has less account/history coverage; review its backup before proceeding"
                )
            if ref["coverage"]["invalid_dates"] or ref["coverage"]["invalid_amounts"]:
                reasons.append("Export contains unreadable transaction dates or amounts")
            if not ref["accounts"] or not ref["categories"]:
                reasons.append("Export must include account and category lists")
            export = session.get(ReferenceExport, sha)
            if not export:
                export = ReferenceExport(
                    sha256=sha,
                    name=f"{meta.id}.QIF",
                    created=int(time()),
                    source="device:" + run.device_id,
                    status="needs_review",
                    note="; ".join(reasons),
                    source_modified=None,
                    coverage=ref["coverage"],
                )
                session.add(export)
                session.flush()
            if not reasons:
                try:
                    with session.begin_nested():
                        activate(session, db, export)
                except ValueError as exc:
                    reasons.append(str(exc))
            receipt = {
                "id": str(meta.id),
                "sha256": sha,
                "status": "needs_review" if reasons else "active",
                "generation": generation(session),
                "digest": catalog(session).get("digest"),
                "message": "; ".join(reasons),
            }
            session.add(
                ExportEvent(
                    id=str(meta.id),
                    device_id=run.device_id,
                    sha256=sha,
                    payload={"input": data, "receipt": receipt, "received": time()},
                )
            )
            if not reasons:
                p["latest_event"] = str(meta.id)
                if meta.purpose == "pre" and run.status == "queued":
                    p["pre_event"] = str(meta.id)
                run.payload = p
            return receipt

    def current_event(session, run, event_id, purpose):
        event = session.get(ExportEvent, str(event_id))
        if (
            not event
            or event.device_id != run.device_id
            or event.payload["input"]["run_id"] != run.id
            or event.payload["input"]["purpose"] != purpose
            or event.payload["receipt"]["status"] != "active"
            or event.payload["receipt"]["generation"] != generation(session)
            or event.payload["input"]["file_identity"] != run.payload["file_identity"]
            or time() - event.payload["received"] > 600
        ):
            raise HTTPException(409, "A fresh, current export for this run is required")
        return event

    @app.post("/api/device/operations/{run_id}/claim")
    def claim(run_id: str, body: Claim, auth: Auth):
        with db.write() as session:
            run = owned(session, run_id, auth, body.owner)
            if run.status != "queued" or run.payload["kind"] != "entry":
                raise HTTPException(409, "Run has already been claimed; refresh and reconcile")
            current_event(session, run, body.event_id, "pre")
            p = copy.deepcopy(run.payload)
            items, skipped = [], []
            for row in p["rows"]:
                candidate = session.get(Candidate, row["id"])
                if (
                    not candidate
                    or candidate.revision != row["revision"]
                    or not entry_eligible(session, candidate)
                ):
                    skipped.append(
                        {"id": row["id"], "reason": "Approval or duplicate evidence changed after refresh"}
                    )
                    continue
                kind = next(
                    (
                        a["type"]
                        for a in catalog(session)["accounts"]
                        if a["name"] == candidate.data["account"]
                    ),
                    "",
                )
                item = {
                    "id": str(uuid4()),
                    "candidate_id": candidate.id,
                    "revision": candidate.revision,
                    "data": candidate.data,
                    "tags": quicken_tags(candidate.data),
                    "account_type": kind,
                    "state": "pending",
                }
                try:
                    render_entry(item)
                except (ValueError, UnicodeError) as exc:
                    skipped.append({"id": candidate.id, "reason": str(exc)})
                    candidate.status = "review"
                    candidate.revision += 1
                    candidate.warnings = [*candidate.warnings, str(exc)]
                    audit(session, candidate, "entry_unsupported")
                    continue
                candidate.status = "entering"
                audit(session, candidate, "entry_claimed")
                items.append(item)
            p.update(
                items=items,
                skipped=skipped,
                pre_event=str(body.event_id),
                message="Entry claimed; awaiting verified results",
            )
            run.payload, run.status = p, "working"
            return describe(run)

    @app.post("/api/device/operations/{run_id}/items/{item_id}/attempt")
    def attempt(run_id: str, item_id: str, body: Owner, auth: Auth):
        with db.write() as session:
            run = owned(session, run_id, auth, body.owner)
            current_event(session, run, run.payload.get("pre_event"), "pre")
            p = copy.deepcopy(run.payload)
            item = next((i for i in p["items"] if i["id"] == item_id), None)
            if run.status != "working" or not item or item["state"] != "pending":
                raise HTTPException(
                    409, "Attempt already recorded or run stopped; never resubmit without reconciliation"
                )
            item.update(state="uncertain", attempted=time())
            run.payload = p
            return {"ok": True}

    @app.post("/api/device/operations/{run_id}/reconcile")
    def reconcile(run_id: str, body: Claim, auth: Auth):
        with db.write() as session:
            run = owned(session, run_id, auth, body.owner)
            purpose = "refresh" if run.payload["kind"] == "refresh" else "post"
            event = current_event(session, run, body.event_id, purpose)
            p = copy.deepcopy(run.payload)
            if run.status == "queued" and p["kind"] == "entry":
                # A restart before claim may only cancel this request, never expand its approvals.
                p["message"] = "Interrupted before claim; request entry again after reviewing"
                run.status = "failed"
            else:
                ref = parse_qif(decode_qif((db.blobs / event.sha256).read_bytes()))
                pre = session.get(ExportEvent, p.get("pre_event")) if p.get("pre_event") else None
                before = (
                    parse_qif(decode_qif((db.blobs / pre.sha256).read_bytes())) if pre else {"history": []}
                )
                for item in p["items"]:
                    if item["state"] in ("entered", "released"):
                        continue
                    candidate = session.get(Candidate, item["candidate_id"])
                    if item["state"] == "pending":
                        item["state"] = "released"
                        candidate.status = "review"
                        candidate.revision += 1
                        audit(session, candidate, "entry_not_attempted")
                        continue
                    if event.payload["input"]["started"] < item["attempted"]:
                        raise HTTPException(409, "Post-entry export predates an attempted action")
                    token = marker(item["id"])
                    found = [r for r in ref["history"] if token in r["memo"]]
                    old = [r for r in before["history"] if token in r["memo"]]
                    if len(found) == 1 and not old and matches(item, found[0]):
                        item.update(state="entered", match=found[0], event_id=str(body.event_id))
                        candidate.status = "entered"
                        candidate.data = {**candidate.data, "entry_match": found[0], "entry_run": run.id}
                        candidate.revision += 1
                        audit(session, candidate, "entry_verified")
                    else:
                        item.update(state="uncertain", marker_count=len(found), event_id=str(body.event_id))
                unresolved = any(i["state"] == "uncertain" for i in p["items"])
                run.status = "needs_review" if unresolved else "complete"
                p["message"] = (
                    "Uncertain outcome: inspect Quicken, then refresh and reconcile"
                    if unresolved
                    else "Fresh Quicken export verified"
                )
            p.update(requested=False, latest_event=str(body.event_id))
            run.payload = p
            return describe(run)

    @app.post("/api/device/operations/{run_id}/stop")
    def stop(run_id: str, body: Failure, auth: Auth):
        with db.write() as session:
            run = owned(session, run_id, auth, body.owner)
            if run.status not in ("complete", "failed"):
                run.status = "needs_review" if run.payload["items"] else "failed"
                run.payload = {**run.payload, "requested": False, "message": body.message}
            return describe(run)

    @app.post("/api/entry/{run_id}/resolve/{item_id}")
    def resolve(run_id: str, item_id: str, auth: Auth, body: Annotated[dict, Body()]):
        if auth.get("device_id"):
            raise HTTPException(403, "Resolve uncertain entry in the browser")
        if body.get("confirmed_not_entered") is not True:
            raise HTTPException(
                422, "Confirm that you inspected Quicken and this transaction was not entered"
            )
        with db.write() as session:
            run = session.get(DesktopRun, run_id)
            if not run:
                raise HTTPException(404, "Run not found")
            p = copy.deepcopy(run.payload)
            item = next((i for i in p["items"] if i["id"] == item_id), None)
            if not item or item["state"] != "uncertain" or item.get("marker_count") != 0:
                raise HTTPException(
                    409, "Refresh first; an ambiguous or mismatched marker requires correction in Quicken"
                )
            current_event(session, run, item.get("event_id"), "post")
            candidate = session.get(Candidate, item["candidate_id"])
            candidate.status = "review"
            candidate.revision += 1
            audit(session, candidate, "uncertain_entry_manually_released")
            item["state"] = "released"
            run.payload = p
            if all(i["state"] in ("entered", "released") for i in p["items"]):
                run.status = "complete"
            return describe(run)
