import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import and_, or_, select

from .catalog import catalog
from .contracts import ModelConfig
from .db import Candidate, Database, Document, ExtractionAttempt, Job
from .documents import model_settings, pages_for, prepare_image
from .extraction import ModelError, VisionAdapter
from .review import create_candidates


def process_one(db, adapter_factory=VisionAdapter):
    now = int(time.time())
    with db.write() as session:
        job = session.scalar(
            select(Job)
            .where(
                or_(
                    and_(Job.status == "queued", Job.available <= now),
                    and_(Job.status == "running", Job.lease_until < now),
                )
            )
            .order_by(Job.created)
            .limit(1)
        )
        if not job:
            return False
        config = ModelConfig.model_validate(job.config)
        job.status, job.claim = "running", str(uuid4())
        job.attempts += 1
        job.lease_until = now + config.timeout + 120
        session.get(Document, job.document_id).status = "extracting"
        job_id, doc_id, claim = job.id, job.document_id, job.claim
        pages = [(db.blobs / p.sha256) for p in pages_for(session, doc_id)]
        ref = catalog(session)
    result = None
    error = None
    try:
        result = adapter_factory(config).extract([prepare_image(p, config.image_limit) for p in pages], ref)
        if any(r.page > len(pages) for r in result.transactions):
            raise ModelError("Model referenced a page that is not in this document")
    except Exception as exc:  # noqa: BLE001 - persist failures at the worker job boundary
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
        session.add(
            ExtractionAttempt(
                id=str(uuid4()),
                document_id=doc_id,
                created=int(time.time()),
                config_revision=config.revision,
                result={"error": error} if error else result.model_dump(mode="json"),
            )
        )
        if error:
            job.status = "queued" if job.attempts < 3 else "failed"
            job.available = int(time.time()) + 15 * job.attempts
            doc.status = "queued" if job.status == "queued" else "failed"
            doc.error = error
        else:
            # Retried jobs cannot replace reviewed or removed rows.
            if not session.scalar(select(Candidate.id).where(Candidate.document_id == doc_id).limit(1)):
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
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = set()
        while True:
            with db.session() as session:
                concurrency = model_settings(session).concurrency
            for future in list(futures):
                if future.done():
                    future.result()
                    futures.remove(future)
            while len(futures) < concurrency:
                futures.add(pool.submit(process_one, db))
            time.sleep(2)


if __name__ == "__main__":
    main()
