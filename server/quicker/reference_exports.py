"""Keep immutable export bytes and activate only a complete, current reference."""

from pathlib import Path
from time import time

from sqlalchemy import select

from .catalog import catalog, import_catalog, parse_qif
from .db import ReferenceExport
from .documents import durable_blob

MAX_EXPORT_BYTES = 10 * 1024 * 1024


def describe(export):
    return {
        key: getattr(export, key)
        for key in ("sha256", "name", "created", "source", "source_modified", "status", "note", "coverage")
    }


def reference_status(session):
    exports = list(session.scalars(select(ReferenceExport).order_by(ReferenceExport.created.desc())))
    return {"digest": catalog(session).get("digest") or "empty", "exports": [describe(e) for e in exports]}


def reduced_coverage(previous, current):
    for old in previous.get("coverage", {}).get("accounts", []):
        if not old["count"]:
            continue
        new = next((r for r in current["coverage"]["accounts"] if r["account"] == old["account"]), None)
        if (
            not new
            or new["count"] < old["count"]
            or (old["first_date"] and (not new["first_date"] or new["first_date"] > old["first_date"]))
            or (old["last_date"] and (not new["last_date"] or new["last_date"] < old["last_date"]))
        ):
            return True
    return False


def activate(session, db, export):
    content = (db.blobs / export.sha256).read_bytes().decode("utf-8-sig", errors="replace")
    ref = import_catalog(session, content)
    for other in session.scalars(select(ReferenceExport).where(ReferenceExport.status == "active")):
        other.status = "archived"
    export.status, export.note = "active", ""
    session.flush()
    return ref


def store_export(db, content, name, source="browser", expected_digest=None, source_modified=None):
    if not content or len(content) > MAX_EXPORT_BYTES:
        raise ValueError("QIF export must be between 1 byte and 10 MB")
    if source_modified is not None and (not source_modified.isdigit() or len(source_modified) > 30):
        raise ValueError("Source modification time must be a nonnegative integer")
    ref = parse_qif(content.decode("utf-8-sig", errors="replace"))
    sha = durable_blob(db, content)
    with db.write() as session:
        existing = session.get(ReferenceExport, sha)
        if existing:
            # Retrying a transfer never reactivates an older snapshot.
            return describe(existing)
        previous = catalog(session)
        reasons = []
        if expected_digest is not None and expected_digest != (previous.get("digest") or "empty"):
            reasons.append("The active reference changed during this transfer.")
        if reduced_coverage(previous, ref):
            reasons.append("This export has less account/history coverage than the active reference.")
        if ref["coverage"]["invalid_dates"] or ref["coverage"]["invalid_amounts"]:
            reasons.append("Some exported transaction dates or amounts are unreadable.")
        if source_modified is not None:
            older = list(session.scalars(select(ReferenceExport).where(ReferenceExport.source == source)))
            if any(e.source_modified and int(e.source_modified) >= int(source_modified) for e in older):
                reasons.append("This file is older than an export already received from this source.")
        export = ReferenceExport(
            sha256=sha,
            name=Path(name.replace("\\", "/")).name[:200] or "reference.QIF",
            created=int(time()),
            source=source,
            source_modified=source_modified,
            status="needs_review",
            note=" ".join(reasons),
            coverage=ref["coverage"],
        )
        session.add(export)
        session.flush()
        if not reasons:
            try:
                with session.begin_nested():
                    activate(session, db, export)
            except ValueError as exc:
                export.status, export.note = "needs_review", str(exc)
        return describe(export)
