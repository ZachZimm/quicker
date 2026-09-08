"""Durable upload receipts and checksum-verified archive sync, independent of Qt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

import httpx

SUPPORTED = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


def checksum(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def archive_path(directory, page):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", page["name"]).rstrip(" .")[:150] or "photo"
    return directory / f"{page['id']}_{name}"


class Companion:
    def __init__(
        self,
        server,
        token,
        input_dir,
        archive_dir,
        state_dir,
        report=lambda message: None,
        transport=None,
        qif_path=None,
    ):
        self.input_dir, self.archive_dir = Path(input_dir).resolve(), Path(archive_dir).resolve()
        if (
            self.input_dir == self.archive_dir
            or self.input_dir in self.archive_dir.parents
            or self.archive_dir in self.input_dir.parents
        ):
            raise ValueError("Input and archive folders must be separate and cannot contain each other")
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        self.journal_path = Path(state_dir) / "sync.sqlite3"
        with sqlite3.connect(self.journal_path) as journal:
            journal.execute(
                "CREATE TABLE IF NOT EXISTS uploads (source TEXT, sha TEXT, request_id TEXT, receipt TEXT, PRIMARY KEY(source,sha))"
            )
        self.qif_path = Path(qif_path).resolve() if qif_path else None
        self.server_scope = hashlib.sha256((server.rstrip("/") + token).encode()).hexdigest()
        with sqlite3.connect(self.journal_path) as journal:
            journal.execute(
                "CREATE TABLE IF NOT EXISTS exports (scope TEXT, sha TEXT, receipt TEXT, PRIMARY KEY(scope,sha))"
            )
        self.http = httpx.Client(
            base_url=server.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=180,
            transport=transport,
            follow_redirects=False,
        )
        self.report = report
        self.stable = {}
        self.stop = threading.Event()

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.status_code)
            except ValueError:
                detail = response.status_code
            raise RuntimeError(f"Server: {detail}")
        return response.json()

    def heartbeat(self):
        return self.request("POST", "/api/device/heartbeat")

    def ingest_file(self, source):
        source = Path(source)
        content = source.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        with sqlite3.connect(self.journal_path) as journal:
            row = journal.execute(
                "SELECT request_id,receipt FROM uploads WHERE source=? AND sha=?", (str(source), sha)
            ).fetchone()
            request_id, receipt = row if row else (str(uuid4()), None)
            if not row:
                journal.execute("INSERT INTO uploads VALUES (?,?,?,NULL)", (str(source), sha, request_id))
        if receipt:
            result = json.loads(receipt)
        else:
            result = self.request(
                "POST",
                "/api/upload",
                data={"request_id": request_id, "grouped": "false"},
                files=[("files", (source.name, content, "application/octet-stream"))],
            )
            if (
                not result.get("stored")
                or len(result.get("pages", [])) != 1
                or result["pages"][0]["sha256"] != sha
            ):
                raise RuntimeError("Server did not confirm storage of this exact file")
            with sqlite3.connect(self.journal_path) as journal:
                journal.execute(
                    "UPDATE uploads SET receipt=? WHERE source=? AND sha=?",
                    (json.dumps(result), str(source), sha),
                )
        page = result["pages"][0]
        target = archive_path(self.archive_dir, page)
        if not target.exists() or checksum(target) != sha:
            partial = target.with_name(target.name + ".part")
            with partial.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if checksum(partial) != sha:
                raise RuntimeError("Local archive verification failed")
            os.replace(partial, target)
        # Preserve a source that changed while the upload was in flight.
        if source.exists() and checksum(source) == sha:
            source.unlink()
        self.request("POST", "/api/device/archive", json={"page_id": page["id"], "sha256": sha})
        self.report(f"Archived {page['name']}")

    def download_page(self, page):
        target = archive_path(self.archive_dir, page)
        if target.exists() and checksum(target) == page["sha256"]:
            self.request(
                "POST", "/api/device/archive", json={"page_id": page["id"], "sha256": page["sha256"]}
            )
            return
        partial = target.with_name(target.name + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= page["size"]:
            if offset == page["size"] and checksum(partial) == page["sha256"]:
                os.replace(partial, target)
                self.request(
                    "POST", "/api/device/archive", json={"page_id": page["id"], "sha256": page["sha256"]}
                )
                return
            partial.unlink()
            offset = 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with self.http.stream("GET", f"/api/pages/{page['id']}/original", headers=headers) as response:
            response.raise_for_status()
            mode = "ab" if response.status_code == 206 and offset else "wb"
            with partial.open(mode) as handle:
                for chunk in response.iter_bytes(256 * 1024):
                    if self.stop.is_set():
                        return
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if checksum(partial) != page["sha256"]:
            partial.unlink()
            raise RuntimeError("Downloaded file checksum failed; the next sync will retry")
        os.replace(partial, target)
        self.request("POST", "/api/device/archive", json={"page_id": page["id"], "sha256": page["sha256"]})
        self.report(f"Downloaded {page['name']} to the archive")

    def sync_export(self):
        source = self.qif_path
        if not source:
            return
        if source.suffix.lower() != ".qif":
            raise ValueError("Choose a Quicken QIF export file")
        before = source.stat()
        stamp = (before.st_size, before.st_mtime_ns)
        previous = self.stable.get(str(source))
        self.stable[str(source)] = stamp
        if previous != stamp:
            return  # Require an unchanged file across two polling cycles.
        if not 0 < before.st_size <= 10 * 1024 * 1024:
            raise ValueError("QIF export must be between 1 byte and 10 MB")
        content = source.read_bytes()
        after = source.stat()
        if stamp != (after.st_size, after.st_mtime_ns):
            return
        sha = hashlib.sha256(content).hexdigest()
        with sqlite3.connect(self.journal_path) as journal:
            if journal.execute(
                "SELECT 1 FROM exports WHERE scope=? AND sha=?", (self.server_scope, sha)
            ).fetchone():
                return
        current = self.request("GET", "/api/device/reference")
        result = self.request(
            "POST",
            "/api/device/reference",
            data={"expected_digest": current.get("digest") or "empty", "source_modified": str(before.st_mtime_ns)},
            files={"file": (source.name, content, "application/octet-stream")},
        )
        if result.get("sha256") != sha or result.get("status") not in ("active", "archived", "needs_review"):
            raise RuntimeError("Server did not confirm this exact QIF backup")
        with sqlite3.connect(self.journal_path) as journal:
            journal.execute(
                "INSERT OR REPLACE INTO exports VALUES (?,?,?)", (self.server_scope, sha, json.dumps(result))
            )
        self.report(
            f"QIF backed up: {source.name}. "
            + (
                "Review it in browser settings before use."
                if result["status"] == "needs_review"
                else "Reference synchronized."
            )
        )

    def cycle(self):
        try:
            self.sync_export()
        except (OSError, RuntimeError, ValueError, sqlite3.Error, httpx.HTTPError):
            self.report("QIF sync failed. Check the export file and connection; the next cycle will retry.")

        for source in sorted(self.input_dir.iterdir()):
            if self.stop.is_set():
                return
            if (
                not source.is_file()
                or source.is_symlink()
                or source.suffix.lower() not in SUPPORTED
                or source.name.startswith(".")
            ):
                continue
            stat = source.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
            if self.stable.get(str(source)) == stamp:
                try:
                    self.ingest_file(source)
                except (OSError, RuntimeError, ValueError, sqlite3.Error, httpx.HTTPError):
                    self.report(
                        f"Could not archive {source.name}. Check image format and folder access; the input is retained for retry."
                    )
            self.stable[str(source)] = stamp
        for page in self.request("GET", "/api/device/archive"):
            if self.stop.is_set():
                return
            self.download_page(page)

    def run(self):
        def pulse():
            while not self.stop.is_set():
                try:
                    status = self.heartbeat()
                    self.report(
                        f"Connected · {status['approved']} approved · Quicken entry not yet available"
                    )
                except (OSError, RuntimeError, ValueError, sqlite3.Error, httpx.HTTPError):
                    self.report(
                        "Connection unavailable. Check the server address or pair again if access was revoked."
                    )
                self.stop.wait(10)

        heartbeat_thread = threading.Thread(target=pulse, daemon=True)
        heartbeat_thread.start()
        try:
            while not self.stop.is_set():
                try:
                    self.cycle()
                except (OSError, RuntimeError, ValueError, sqlite3.Error, httpx.HTTPError):
                    self.report(
                        "Archive sync interrupted. Check the connection and folder access; originals are retained for retry."
                    )
                self.stop.wait(5)
        finally:
            heartbeat_thread.join(timeout=190)
            self.http.close()
