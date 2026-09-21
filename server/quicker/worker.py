import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import and_, or_, select

from . import model_availability
from .analysis_status import heartbeat
from .catalog import catalog
from .contracts import ModelConfig
from .db import Candidate, Database, Document, ExtractionAttempt, Job
from .documents import model_settings, pages_for, prepare_image
from .extraction import ModelError, VisionAdapter
from .model_endpoint import ModelUnavailable, PermanentModelError
from .review import create_candidates, proposals


def process_one(db, adapter_factory=VisionAdapter):
    now = int(time.time())
    with db.write() as session:
        eligible = session.scalars(
            select(Job)
            .where(
                or_(
                    and_(Job.status == "queued", Job.available <= now),
                    and_(Job.status == "running", Job.lease_until < now),
                )
            )
            .order_by(Job.created)
        )
        job = None
        for candidate in eligible:
            config = ModelConfig.model_validate(candidate.config)
            pages = [(db.blobs / p.sha256) for p in pages_for(session, candidate.document_id)]
            claim = str(uuid4())
            # One transcription request, one visual exclusion check per page, plus readiness.
            lease_until = now + config.timeout * (1 + len(pages)) + 120
            generation = model_availability.acquire(session, config, claim, lease_until, now)
            if generation is not None:
                job = candidate
                break
        if not job:
            return False
        job.status, job.claim = "running", claim
        job.attempts += 1
        session.get(Document, job.document_id).status = "extracting"
        job_id, doc_id, claim = job.id, job.document_id, job.claim
        job.lease_until = lease_until
        ref = catalog(session)
    result = None
    error = None
    failure = None
    try:
        adapter = adapter_factory(config)
        if hasattr(adapter, "ready"):
            adapter.ready()
        result = adapter.extract([prepare_image(p, config.image_limit) for p in pages], ref)
        if any(r.page > len(pages) for r in result.transactions):
            raise ModelError("Model referenced a page that is not in this document")
    except Exception as exc:  # noqa: BLE001 - persist failures at the worker job boundary
        failure = exc
        error = (
            str(exc)
            if isinstance(exc, ModelError)
            else "Image processing or extraction failed; check the source and retry."
        )
    with db.write() as session:
        job = session.get(Job, job_id)
        if job.claim != claim:
            return True  # An expired worker must never publish over a newer claim.
        doc = session.get(Document, doc_id)
        if isinstance(failure, ModelUnavailable):
            model_availability.unavailable(session, config, failure, claim)
            if failure.reason == "memory" and failure.document_request:
                job.resource_failures += 1
            if job.resource_failures < 3:
                # An outage is not a failed document attempt. Store only the latest
                # availability error, so an overnight outage cannot grow history forever.
                job.attempts = max(0, job.attempts - 1)
                job.status, doc.status, doc.error = "queued", "queued", error
                job.available = 0  # Shared endpoint cooldown controls the retry.
                return True
            failure = PermanentModelError(
                "Analysis of this document repeatedly ran out of memory. Free memory or reduce pages/context, then Analyze again."
            )
            error = str(failure)
        else:
            model_availability.completed(session, config, generation, claim, success=not error)
        payload = {"error": error} if error else result.model_dump(mode="json")
        has_rows = session.scalar(select(Candidate.id).where(Candidate.document_id == doc_id).limit(1))
        if not error and has_rows:
            proposed, ignored = proposals(session, result)
            payload.update(comparison=proposed, ignored=ignored, applied=[])
        session.add(
            ExtractionAttempt(
                id=str(uuid4()),
                document_id=doc_id,
                created=int(time.time()),
                config_revision=config.revision,
                result=payload,
            )
        )
        if error:
            job.status = (
                "queued" if job.attempts < 3 and not isinstance(failure, PermanentModelError) else "failed"
            )
            job.available = int(time.time()) + 15 * job.attempts
            doc.status = "queued" if job.status == "queued" else "failed"
            doc.error = error
        else:
            # Retried jobs cannot replace reviewed or removed rows.
            if not has_rows:
                doc.ignored = create_candidates(session, doc_id, result)
            doc.status, doc.error, job.status = "ready", None, "done"
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    db = Database()
    db.migrate()
    if args.once:
        process_one(db)
        return
    worker_id = str(uuid4())
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = set()
            while True:
                with db.session() as session:
                    concurrency = model_settings(session).concurrency
                for future in list(futures):
                    if future.done():
                        future.result()
                        futures.remove(future)
                heartbeat(db, worker_id, len(futures), concurrency)
                while len(futures) < concurrency:
                    futures.add(pool.submit(process_one, db))
                time.sleep(2)
    finally:
        heartbeat(db, worker_id, 0, 0)


if __name__ == "__main__":
    main()
