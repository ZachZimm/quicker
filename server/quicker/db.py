from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class User(Base):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    password: Mapped[str] = mapped_column(Text)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    address: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    started: Mapped[int] = mapped_column(Integer)


class BrowserSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    csrf: Mapped[str] = mapped_column(String)
    expires: Mapped[int] = mapped_column(Integer)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    heartbeat: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(default=False)


class PairCode(Base):
    __tablename__ = "pair_codes"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    expires: Mapped[int] = mapped_column(Integer)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    created: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ignored: Mapped[list] = mapped_column(JSON, default=list)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, index=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)


class UploadReceipt(Base):
    __tablename__ = "upload_receipts"
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    digest: Mapped[str] = mapped_column(String)
    document_ids: Mapped[list] = mapped_column(JSON)


class ArchiveReceipt(Base):
    __tablename__ = "archive_receipts"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"), primary_key=True)
    received: Mapped[int] = mapped_column(Integer)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    config: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    claim: Mapped[str | None] = mapped_column(String, nullable=True)
    created: Mapped[int] = mapped_column(Integer)
    available: Mapped[int] = mapped_column(Integer, default=0)


class ExtractionAttempt(Base):
    __tablename__ = "extraction_attempts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    created: Mapped[int] = mapped_column(Integer)
    config_revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="review", index=True)
    data: Mapped[dict] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    created: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    snapshot: Mapped[dict] = mapped_column(JSON)


class Route(Base):
    __tablename__ = "routes"
    property: Mapped[str] = mapped_column(String, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String)


class ReferenceExport(Base):
    __tablename__ = "reference_exports"
    sha256: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    created: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)
    source_modified: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(Text, default="")
    coverage: Mapped[dict] = mapped_column(JSON)


class Database:
    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory or os.environ.get("QUICKER_DATA_DIR", "data")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.blobs = self.directory / "documents"
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.engine = create_engine(
            f"sqlite:///{self.directory / 'quicker.sqlite3'}", connect_args={"timeout": 30}
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")

    def session(self):
        return Session(self.engine, expire_on_commit=False)

    @contextmanager
    def write(self):
        with self.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def migrate(self):
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
        config.set_main_option("sqlalchemy.url", str(self.engine.url))
        command.upgrade(config, "head")
