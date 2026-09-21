import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from quicker.app import SESSION_COOKIE_MAX_AGE, create_app
from quicker.db import BrowserSession
from quicker.security import digest
from sqlalchemy import select

LOGIN = {"username": "admin", "password": "test-password-12345"}


def assert_persistent_cookie(response):
    cookie = response.headers["set-cookie"]
    assert f"Max-Age={SESSION_COOKIE_MAX_AGE}" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie


def test_login_survives_server_restart_and_elapsed_time(client, db, monkeypatch):
    response = client.post("/api/login", json=LOGIN)
    assert_persistent_cookie(response)
    token = client.cookies.get("quicker_session")
    with db.session() as session:
        assert session.get(BrowserSession, digest(token)).expires == 0
    # Test the server clock independently of the browser cookie retention limit.
    monkeypatch.setattr("quicker.app.time", SimpleNamespace(time=lambda: time.time() + 5 * 365 * 86400))
    with TestClient(create_app(db)) as restarted:
        restarted.cookies.set("quicker_session", token)
        response = restarted.get("/api/session")
        assert response.status_code == 200
        assert_persistent_cookie(response)


def test_legacy_login_is_upgraded_but_expired_login_is_rejected(auth, db):
    token = auth.cookies.get("quicker_session")
    with db.write() as session:
        session.get(BrowserSession, digest(token)).expires = int(time.time()) + 43200
    response = auth.get("/api/session")
    assert response.status_code == 200
    assert_persistent_cookie(response)
    with db.write() as session:
        stored = session.get(BrowserSession, digest(token))
        assert stored.expires == 0
        stored.expires = int(time.time()) - 10
    response = auth.get("/api/session")
    assert response.status_code == 401 and "set-cookie" not in response.headers


def test_login_cleanup_preserves_other_permanent_sessions(auth, db):
    old_token = auth.cookies.get("quicker_session")
    with db.write() as session:
        session.add(BrowserSession(token_hash="expired", csrf="old", expires=int(time.time()) - 1))
    assert auth.post("/api/login", json=LOGIN).status_code == 200
    with db.session() as session:
        assert session.get(BrowserSession, digest(old_token)) is not None
        assert session.get(BrowserSession, "expired") is None
        assert len(list(session.scalars(select(BrowserSession)))) == 2


def test_logout_revokes_token_without_renewing_cookie(auth, db):
    token = auth.cookies.get("quicker_session")
    response = auth.post("/api/logout")
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1 and "Max-Age=0" in cookies[0]
    with db.session() as session:
        assert session.get(BrowserSession, digest(token)) is None
    assert auth.get("/api/session", headers={"Cookie": f"quicker_session={token}"}).status_code == 401


def test_password_reset_revokes_persistent_sessions(auth, db, monkeypatch):
    from quicker.cli import main

    monkeypatch.setenv("QUICKER_DATA_DIR", str(db.directory))
    monkeypatch.setattr("sys.argv", ["quicker", "setup", "--username", "admin"])
    monkeypatch.setattr("quicker.cli.getpass.getpass", lambda prompt: "new-pass")
    main()
    assert auth.get("/api/session").status_code == 401


def test_cookie_security_setting_and_failed_csrf(auth, monkeypatch):
    monkeypatch.setenv("QUICKER_COOKIE_SECURE", "true")
    response = auth.get("/api/session")
    assert "Secure" in response.headers["set-cookie"]
    # Secure cookies aren't sent over TestClient's HTTP URL; supply the token to test CSRF.
    token = auth.cookies.get("quicker_session")
    response = auth.post("/api/logout", headers={"Cookie": f"quicker_session={token}", "X-CSRF-Token": ""})
    assert response.status_code == 403 and "set-cookie" not in response.headers
