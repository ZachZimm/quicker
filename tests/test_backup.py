import sys

from fastapi.testclient import TestClient
from quicker.app import create_app
from quicker.cli import main
from quicker.db import Database, Document
from sqlalchemy import select
from test_workflow import upload


def test_backup_restores_database_and_originals(auth, db, photo, tmp_path, monkeypatch):
    original = upload(auth, photo)
    target = tmp_path / "backup"
    monkeypatch.setenv("QUICKER_DATA_DIR", str(db.directory))
    monkeypatch.setattr(sys, "argv", ["quicker", "backup", str(target)])
    main()
    restored = Database(target)
    restored.migrate()
    with restored.session() as session:
        assert session.scalar(select(Document.id)) == original["document_ids"][0]
    with TestClient(create_app(restored)) as browser:
        session = browser.post("/api/login", json={"username": "admin", "password": "test-password-12345"})
        assert session.status_code == 200
        assert browser.get("/api/pages/" + original["pages"][0]["id"] + "/original").content == photo
    restored.engine.dispose()
