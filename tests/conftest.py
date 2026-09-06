import io
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image
from quicker.app import create_app
from quicker.catalog import import_catalog
from quicker.db import Database, User
from quicker.security import password_hash

QIF = """!Type:Tag
NWater
^
NProperty
^
!Type:Cat
NUtilities
E
^
NRepairs
E
^
!Account
N2026 Bell St.
TBank
^
N2027 Bell St.
TBank
^
NR&K Properties 2026
TBank
^
!Type:Memorized
PExample Energy
LUtilities/Water
^
"""


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "data")
    database.migrate()
    with database.write() as session:
        session.add(User(name="admin", password=password_hash("test-password-12345")))
        import_catalog(session, QIF)
    yield database
    database.engine.dispose()


@pytest.fixture
def client(db):
    with TestClient(create_app(db)) as c:
        yield c


@pytest.fixture
def auth(client):
    response = client.post("/api/login", json={"username": "admin", "password": "test-password-12345"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return client


@pytest.fixture
def photo():
    stream = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def browser_url(db):
    if not Path("web/dist/index.html").exists():
        pytest.skip("Build the web application before browser tests")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(db), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
