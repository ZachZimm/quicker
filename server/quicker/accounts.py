"""Confirmed account requests. Only a fresh Windows export can prove creation."""

import re
from time import time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from quicker_client.qif import MAX_ACCOUNT_NAME
from sqlalchemy import select

from .catalog import catalog
from .db import AccountRequest, DesktopRun, Device, ExportEvent
from .desktop import capability, generation


class NewAccount(BaseModel):
    request_id: UUID
    name: str = Field(min_length=1, max_length=MAX_ACCOUNT_NAME)
    account_type: Literal["Bank"] = "Bank"
    confirmed: Literal[True]

    @field_validator("name")
    @classmethod
    def valid_name(cls, name):
        name = name.strip()
        if not name or any(ord(c) < 32 or ord(c) == 127 or c in "[]^" for c in name):
            raise ValueError("Use a single-line account name without brackets or ^")
        try:
            name.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "Account name contains characters unsupported by Quicken's QIF importer"
            ) from exc
        return name


class Owner(BaseModel):
    owner: UUID


class Proof(Owner):
    event_id: UUID


def describe(session, request):
    supported = capability(session, request.device_id).get("account_creation") == 1
    return {
        "id": request.id,
        "name": request.name,
        "account_type": request.account_type,
        "file_identity": request.file_identity,
        "file_name": request.payload.get("file_name"),
        "status": request.status,
        "created": request.created,
        "attempted": bool(request.payload.get("attempted")),
        "message": "Verified in Quicken"
        if request.status == "complete"
        else "Creation outcome pending verification"
        if request.payload.get("attempted")
        else "Creating account in Quicken"
        if request.status == "creating"
        else "Waiting for Windows"
        if supported
        else "Waiting for a Windows client with account creation support",
    }


def pending_accounts(session):
    return [
        describe(session, r)
        for r in session.scalars(
            select(AccountRequest).where(AccountRequest.status != "complete").order_by(AccountRequest.created)
        )
    ]


def creation_in_progress(session):
    return session.scalar(select(AccountRequest.id).where(AccountRequest.status == "creating")) is not None


def install(app, db, authenticated, require_device):
    Auth = Annotated[dict, Depends(authenticated)]

    @app.post("/api/account-requests")
    def create(body: NewAccount, auth: Auth):
        with db.write() as session:
            device = session.scalar(select(Device).where(Device.revoked.is_(False)))
            cap = capability(session, device.id) if device else {}
            identity = cap.get("file_identity", "")
            if not device or not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
                raise HTTPException(
                    409, "Pair Windows and configure the target Quicken file before requesting an account"
                )
            previous = session.get(AccountRequest, str(body.request_id))
            if previous:
                if (previous.name, previous.account_type, previous.device_id, previous.file_identity) != (
                    body.name,
                    body.account_type,
                    device.id,
                    identity,
                ):
                    raise HTTPException(409, "Request ID was already used for a different account or file")
                return describe(session, previous)
            existing = [
                a for a in catalog(session)["accounts"] if a["name"].casefold() == body.name.casefold()
            ]
            if existing:
                raise HTTPException(409, "This account already exists. Select it from the list.")
            for previous in session.scalars(
                select(AccountRequest).where(
                    AccountRequest.file_identity == identity, AccountRequest.status != "complete"
                )
            ):
                if previous.name.casefold() == body.name.casefold():
                    if previous.account_type != body.account_type or previous.device_id != device.id:
                        raise HTTPException(
                            409, "An account request with this name has a different type or device"
                        )
                    return describe(session, previous)
            request = AccountRequest(
                id=str(body.request_id),
                device_id=device.id,
                file_identity=identity,
                name=body.name,
                account_type=body.account_type,
                status="queued",
                created=int(time()),
                payload={"file_name": cap.get("file_name")},
            )
            session.add(request)
            session.flush()
            return describe(session, request)

    @app.get("/api/device/account-requests")
    def pending(auth: Auth):
        device_id = require_device(auth)
        with db.session() as session:
            return [
                describe(session, r)
                for r in session.scalars(
                    select(AccountRequest).where(
                        AccountRequest.device_id == device_id, AccountRequest.status != "complete"
                    )
                )
            ]

    def owned(session, request_id, auth, owner):
        device_id = require_device(auth)
        request = session.get(AccountRequest, request_id)
        if not request or request.device_id != device_id:
            raise HTTPException(404, "Account request not found")
        cap = capability(session, device_id)
        if cap.get("account_creation") != 1:
            raise HTTPException(409, "Update Windows to support account creation")
        if cap.get("file_identity") != request.file_identity:
            raise HTTPException(409, "This account request belongs to a different Quicken file")
        if request.account_type != "Bank":
            raise HTTPException(
                409, "Only Bank account creation is supported; resolve this older request manually"
            )
        if request.payload.get("owner") not in (None, str(owner)):
            raise HTTPException(409, "Another companion instance owns this account request")
        return request

    def proof(session, request, event_id, since):
        event = session.get(ExportEvent, str(event_id))
        if not event:
            raise HTTPException(409, "A fresh Windows export is required")
        meta, receipt = event.payload["input"], event.payload["receipt"]
        if (
            event.device_id != request.device_id
            or meta["file_identity"] != request.file_identity
            or meta["started"] < since
            or time() - event.payload["received"] > 600
            or receipt["status"] != "active"
            or receipt["generation"] != generation(session)
        ):
            raise HTTPException(409, "Create a fresh export of this Quicken file and retry verification")
        matches = [a for a in catalog(session)["accounts"] if a["name"].casefold() == request.name.casefold()]
        if matches and (
            len(matches) != 1
            or matches[0]["name"] != request.name
            or matches[0]["type"] != request.account_type
        ):
            raise HTTPException(
                409, "Quicken has a conflicting account name or type; resolve it before continuing"
            )
        return bool(matches)

    @app.post("/api/device/account-requests/{request_id}/claim")
    def claim(request_id: str, body: Proof, auth: Auth):
        with db.write() as session:
            request = owned(session, request_id, auth, body.owner)
            if request.status == "complete" or request.payload.get("attempted"):
                return describe(session, request)
            active = session.scalar(
                select(DesktopRun).where(DesktopRun.status.not_in(["complete", "failed"]))
            )
            other_creation = session.scalar(
                select(AccountRequest.id).where(
                    AccountRequest.status == "creating", AccountRequest.id != request.id
                )
            )
            if active or other_creation:
                raise HTTPException(409, "Finish the active Quicken operation first")
            exists = proof(session, request, body.event_id, request.created)
            request.payload = {**request.payload, "owner": str(body.owner), "pre_event": str(body.event_id)}
            request.status = "complete" if exists else "creating"
            return describe(session, request)

    @app.post("/api/device/account-requests/{request_id}/attempt")
    def attempt(request_id: str, body: Owner, auth: Auth):
        with db.write() as session:
            request = owned(session, request_id, auth, body.owner)
            if request.status != "creating":
                raise HTTPException(409, "Claim this account request first")
            may_create = not request.payload.get("attempted")
            if may_create:
                # The pre-export must still be current immediately before the UI action.
                if proof(session, request, request.payload["pre_event"], request.created):
                    raise HTTPException(409, "Account already exists; verify it instead of creating it")
                if session.scalar(
                    select(DesktopRun.id).where(DesktopRun.status.not_in(["complete", "failed"]))
                ):
                    raise HTTPException(409, "Finish the active Quicken operation first")
                request.payload = {**request.payload, "attempted": time()}
            return {**describe(session, request), "may_create": may_create}

    @app.post("/api/device/account-requests/{request_id}/complete")
    def complete(request_id: str, body: Proof, auth: Auth):
        with db.write() as session:
            request = owned(session, request_id, auth, body.owner)
            if request.status == "complete":
                return describe(session, request)
            if request.status != "creating":
                raise HTTPException(409, "Claim this account request first")
            if not proof(session, request, body.event_id, request.payload.get("attempted", request.created)):
                raise HTTPException(
                    409, "The fresh export does not contain the requested account; creation is unverified"
                )
            request.status = "complete"
            request.payload = {**request.payload, "verified_event": str(body.event_id)}
            return describe(session, request)
