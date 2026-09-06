import hashlib
import json

import httpx
from quicker_client.sync import Companion, archive_path


def make_engine(tmp_path, handler):
    return Companion(
        "http://server",
        "device-token",
        tmp_path / "input",
        tmp_path / "archive",
        tmp_path / "state",
        transport=httpx.MockTransport(handler),
    )


def test_source_is_retained_until_ack_and_retry_reuses_request(tmp_path, photo):
    requests = []
    sha = hashlib.sha256(photo).hexdigest()
    page = {"id": "page1", "sha256": sha, "name": "bill.png", "size": len(photo)}
    fail = True

    def handler(request):
        nonlocal fail
        if request.url.path == "/api/upload":
            requests.append(request.content)
            if fail:
                fail = False
                return httpx.Response(503, json={"detail": "offline"})
            return httpx.Response(200, json={"stored": True, "pages": [page]})
        return httpx.Response(200, json={"ok": True})

    engine = make_engine(tmp_path, handler)
    source = engine.input_dir / "bill.png"
    source.write_bytes(photo)
    try:
        engine.ingest_file(source)
    except RuntimeError:
        pass
    assert source.exists()
    engine.ingest_file(source)
    assert not source.exists()
    assert archive_path(engine.archive_dir, page).read_bytes() == photo
    # Multipart boundaries change; the persisted request ID must not.
    import re

    ids = [re.search(b'name="request_id"\r\n\r\n([^\r]+)', body)[1] for body in requests]
    assert ids[0] == ids[1]


def test_browser_upload_resumes_and_verifies_before_ack(tmp_path, photo):
    page = {
        "id": "page2",
        "name": "phone.png",
        "sha256": hashlib.sha256(photo).hexdigest(),
        "size": len(photo),
    }
    acknowledgements = []

    def handler(request):
        if request.method == "GET":
            assert request.headers["Range"] == "bytes=100-"
            return httpx.Response(206, content=photo[100:])
        acknowledgements.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    engine = make_engine(tmp_path, handler)
    target = archive_path(engine.archive_dir, page)
    target.with_name(target.name + ".part").write_bytes(photo[:100])
    engine.download_page(page)
    assert target.read_bytes() == photo
    assert acknowledgements == [{"page_id": "page2", "sha256": page["sha256"]}]


def test_changed_source_not_deleted_during_upload(tmp_path, photo):
    engine = None

    def handler(request):
        if request.url.path == "/api/upload":
            (engine.input_dir / "bill.png").write_bytes(b"new version still being written")
            return httpx.Response(
                200,
                json={
                    "stored": True,
                    "pages": [
                        {
                            "id": "old",
                            "name": "bill.png",
                            "sha256": hashlib.sha256(photo).hexdigest(),
                            "size": len(photo),
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"ok": True})

    engine = make_engine(tmp_path, handler)
    source = engine.input_dir / "bill.png"
    source.write_bytes(photo)
    engine.ingest_file(source)
    assert source.read_bytes() == b"new version still being written"


def test_corrupt_download_is_not_acknowledged(tmp_path, photo):
    page = {"id": "p", "name": "phone.png", "sha256": hashlib.sha256(photo).hexdigest(), "size": len(photo)}

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, content=b"corrupted")

    engine = make_engine(tmp_path, handler)
    import pytest

    with pytest.raises(RuntimeError, match="checksum"):
        engine.download_page(page)
    assert not archive_path(engine.archive_dir, page).exists()


def test_real_server_companion_sync_roundtrip(browser_url, auth, tmp_path, photo):
    code = auth.post("/api/pair-code").json()["code"]
    paired = httpx.post(
        browser_url + "/api/pair", json={"name": "Acceptance test companion", "code": code}
    ).json()
    engine = Companion(
        browser_url, paired["token"], tmp_path / "input", tmp_path / "archive", tmp_path / "state"
    )
    assert engine.heartbeat()["entry_supported"] is False
    # A folder document is uploaded and moved only after the server receipt.
    source = engine.input_dir / "folder.png"
    source.write_bytes(photo)
    engine.cycle()
    engine.cycle()
    assert not source.exists()
    assert len(list(engine.archive_dir.glob("*.png"))) == 1
    # A later phone upload reaches the Windows archive through the same manifest.
    response = auth.post(
        "/api/upload", data={"request_id": "phone"}, files=[("files", ("phone.png", photo, "image/png"))]
    )
    assert response.status_code == 200
    assert auth.get("/api/devices").json()[0]["pending_archives"] == 1
    engine.cycle()
    assert len(list(engine.archive_dir.glob("*.png"))) == 2
    assert auth.get("/api/devices").json()[0]["pending_archives"] == 0
    assert all(checksum.read_bytes() == photo for checksum in engine.archive_dir.glob("*.png"))
    engine.http.close()
