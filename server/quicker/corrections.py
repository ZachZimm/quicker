"""Append missed source rows and reconcile new extractions without replacing review work."""

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, Field
from sqlalchemy import select

from .contracts import ReviewFields
from .db import Audit, Candidate, Document, ExtractionAttempt
from .documents import pages_for
from .matching import scope_compatible
from .profile import merchant_identity
from .review import ReviewError, insert_candidate, resolve_account, serialize


class ManualRow(BaseModel):
    request_id: UUID
    page: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=2000)
    fields: ReviewFields = Field(default_factory=ReviewFields)


class AcceptProposals(BaseModel):
    revision: str
    indices: list[int] = Field(min_length=1, max_length=200)


def add_manual(session, document_id, body):
    if not session.get(Document, document_id):
        raise ReviewError("Document not found", 404)
    if body.page > len(pages_for(session, document_id)):
        raise ReviewError("Choose a page belonging to this document")
    candidate_id = str(uuid5(NAMESPACE_URL, f"quicker:manual:{document_id}:{body.request_id}"))
    existing = session.get(Candidate, candidate_id)
    if existing:
        return serialize(session, existing)
    data = resolve_account(
        session,
        {
            **body.fields.model_dump(mode="json"),
            "page": body.page,
            "source": body.source,
            "kind": "other",
            "document_type": "other",
            "source_index": 10000,
            "manual": True,
            "duplicate_acknowledged": False,
        },
    )
    row = insert_candidate(
        session, document_id, {"data": data, "warnings": []}, "manual_created", candidate_id
    )
    return serialize(session, row)


def represented(proposal, evidence):
    if proposal.get("page") != evidence.get("page") or not scope_compatible(proposal, evidence):
        return False
    # A stable source identifier survives corrections to payee, date and amount.
    for key in ("service_customer_id", "parcel"):
        if proposal.get(key) and evidence.get(key):
            if proposal[key] != evidence[key]:
                return False
            if all(proposal.get(k) == evidence.get(k) for k in ("invoice_number", "date", "amount_minor")):
                return True
    return (
        bool(proposal.get("payee"))
        and proposal.get("amount_minor") is not None
        and merchant_identity(proposal["payee"]) == merchant_identity(evidence.get("payee"))
        and all(proposal.get(k) == evidence.get(k) for k in ("amount_minor", "date"))
    )


def comparison(session, document_id, attempt_id):
    attempt = session.get(ExtractionAttempt, attempt_id)
    if not attempt or attempt.document_id != document_id:
        raise ReviewError("Extraction attempt not found", 404)
    if "comparison" not in attempt.result:
        raise ReviewError("This attempt has no comparison. Run extraction again first.")
    rows = list(
        session.scalars(select(Candidate).where(Candidate.document_id == document_id).order_by(Candidate.id))
    )
    evidence = {row.id: [row.data] for row in rows}
    if rows:
        for event in session.scalars(select(Audit).where(Audit.candidate_id.in_(evidence))):
            evidence[event.candidate_id].append(event.snapshot["data"])
    version = hashlib.sha256(json.dumps([(r.id, r.revision) for r in rows]).encode()).hexdigest()
    used = set()
    proposals = []
    for index, proposal in enumerate(attempt.result["comparison"]):
        matches = [
            r
            for r in rows
            if r.id not in used and any(represented(proposal["data"], d) for d in evidence[r.id])
        ]
        matched = matches[0] if matches else None
        if matched:
            used.add(matched.id)
        proposals.append(
            {
                "index": index,
                **proposal,
                "existing": serialize(session, matched) if matched else None,
                "applied": index in attempt.result.get("applied", []),
            }
        )
    return {
        "attempt_id": attempt.id,
        "revision": version,
        "proposals": proposals,
        "ignored": attempt.result.get("ignored", []),
    }


def accept_proposals(session, document_id, attempt_id, body):
    view = comparison(session, document_id, attempt_id)
    attempt = session.get(ExtractionAttempt, attempt_id)
    indices = set(body.indices)
    if len(indices) != len(body.indices) or any(i < 0 or i >= len(view["proposals"]) for i in indices):
        raise ReviewError("Choose valid, distinct proposal rows")
    applied = set(attempt.result.get("applied", []))
    if indices <= applied:
        return view  # A lost response can be retried without adding rows twice.
    if body.revision != view["revision"]:
        raise ReviewError("Review changed. Reload the comparison before adding rows.", 409)
    for index in sorted(indices - applied):
        proposal = view["proposals"][index]
        if proposal["existing"]:
            raise ReviewError("This source row is already represented, including removed transactions.", 409)
        if not 1 <= proposal["data"].get("page", 0) <= len(pages_for(session, document_id)):
            raise ReviewError("The extraction referenced a page outside this document")
        insert_candidate(session, document_id, proposal, "reextraction_added")
    attempt.result = {**attempt.result, "applied": sorted(applied | indices)}
    session.flush()
    return comparison(session, document_id, attempt_id)
