import hashlib
import io
import os
import tempfile
from pathlib import Path
from time import time
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy import select

from .contracts import ModelConfig
from .db import Document, Job, Page, Setting, UploadReceipt

register_heif_opener()
Image.MAX_IMAGE_PIXELS = 60_000_000
MAX_FILE_BYTES = 50 * 1024 * 1024


def model_settings(session):
    setting = session.get(Setting, "model")
    return ModelConfig.model_validate(setting.value) if setting else ModelConfig()


def prepare_image(path, limit=2000):
    with Image.open(path) as source:
        im = ImageOps.exif_transpose(source).convert("RGB")
        im.thumbnail((limit, limit))
        output = io.BytesIO()
        im.save(output, format="JPEG", quality=90)
        return output.getvalue()


def image_mime(data):
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("JPEG", "PNG", "HEIF", "HEIC"):
                raise ValueError("Upload a JPEG, PNG, or HEIC image")
            im.verify()
            return {"JPEG": "image/jpeg", "PNG": "image/png"}.get(im.format, "image/heic")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The image is unreadable or exceeds the supported dimensions") from exc


def durable_blob(db, data):
    sha = hashlib.sha256(data).hexdigest()
    target = db.blobs / sha
    if not target.exists():
        fd, temp = tempfile.mkstemp(dir=db.blobs)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            if os.name != "nt":
                directory_fd = os.open(db.blobs, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return sha


def ingest(db, files, request_id, grouped=False):
    if not files or len(files) > 20:
        raise ValueError("Upload between 1 and 20 images at a time")
    prepared = []
    for name, content in files:
        if not content or len(content) > MAX_FILE_BYTES:
            raise ValueError("Each image must be between 1 byte and 50 MB")
        mime = image_mime(content)
        name = Path(name.replace("\\", "/")).name[:200] or "photo"
        sha = durable_blob(db, content)
        prepared.append((name, sha, len(content), mime))
    fingerprint = hashlib.sha256(repr((grouped, prepared)).encode()).hexdigest()
    with db.write() as session:
        receipt = session.get(UploadReceipt, request_id)
        if receipt:
            if receipt.digest != fingerprint:
                raise ValueError("This upload request ID was already used for different files")
            return receipt.document_ids
        config = model_settings(session).model_dump()
        document_ids = []
        groups = [prepared] if grouped else [[item] for item in prepared]
        for group in groups:
            doc = Document(
                id=str(uuid4()),
                name=group[0][0] if len(group) == 1 else f"{group[0][0]} + {len(group) - 1} pages",
                status="queued",
                created=int(time()),
                ignored=[],
            )
            session.add(doc)
            session.flush()
            for ordinal, (name, sha, size, mime) in enumerate(group):
                session.add(
                    Page(
                        id=str(uuid4()),
                        document_id=doc.id,
                        name=name,
                        sha256=sha,
                        size=size,
                        mime=mime,
                        ordinal=ordinal,
                    )
                )
            session.add(
                Job(
                    id=str(uuid4()),
                    document_id=doc.id,
                    status="queued",
                    config=config,
                    created=int(time()),
                    attempts=0,
                    lease_until=0,
                    available=0,
                )
            )
            document_ids.append(doc.id)
        session.add(UploadReceipt(request_id=request_id, digest=fingerprint, document_ids=document_ids))
        return document_ids


def pages_for(session, document_id):
    return list(session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.ordinal)))


def serialize_page(page):
    return {
        "id": page.id,
        "document_id": page.document_id,
        "name": page.name,
        "sha256": page.sha256,
        "size": page.size,
        "mime": page.mime,
        "ordinal": page.ordinal,
    }
