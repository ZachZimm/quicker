"""Durable upload receipts and checksum-verified archive sync, independent of Qt."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx

from .lifecycle import Backoff

SUPPORTED = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
AUTO_EXPORT_INTERVAL_SECONDS = 15 * 60


class PairingRevoked(RuntimeError):
    pass


class ServerUnavailable(RuntimeError):
    pass


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
        data_file=None,
        status_report=lambda status: None,
        auto_exports_paused=False,
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
        self.status_report = status_report
        self.status_lock = threading.RLock()
        self.status = {
            "connected": False,
            "desktop": False,
            "waiting": "Connecting",
            "paused": auto_exports_paused,
            "last_export": None,
            "attention": None,
        }
        self.auto_exports_paused = threading.Event()
        if auto_exports_paused:
            self.auto_exports_paused.set()
        self.fatal_reason = None
        self.heartbeat_error = None
        self.commands = queue.Queue()
        self.manual_runs = set()
        self.operations = None
        self.protocol = 0
        self.last_refresh = 0
        self.account_retries = {}
        self.last_desktop_message = None
        if data_file:
            from .desktop import WindowsQuicken
            from .operations import Operations

            self.operations = Operations(self, WindowsQuicken(data_file), state_dir)
            self.status["last_export"] = self.operations.get("last_successful_export")

    def update_status(self, **values):
        with self.status_lock:
            changed = any(self.status.get(key) != value for key, value in values.items())
            self.status.update(values)
            if changed:
                self.status_report(dict(self.status))

    def pause_exports(self, paused):
        self.auto_exports_paused.set() if paused else self.auto_exports_paused.clear()
        self.update_status(
            paused=paused, waiting="Automatic exports paused" if paused else "Waiting for idle desktop"
        )

    def export_completed(self, completed):
        self.update_status(last_export=completed, waiting="")

    def attention(self, key, message):
        self.update_status(attention={"key": key, "message": message} if key else None)

    def resolve_attention(self, key):
        with self.status_lock:
            if (self.status.get("attention") or {}).get("key") == key:
                self.update_status(attention=None)

    def command(self, kind):
        if not self.stop.is_set() and self.commands.qsize() < 1:
            self.commands.put((kind, str(uuid4())))

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.status_code in (401, 403):
            raise PairingRevoked("Pairing no longer accepted. Pair this companion again in settings.")
        if response.status_code >= 500 or response.status_code == 429:
            raise ServerUnavailable("Server temporarily unavailable; reconnecting automatically")
        if response.is_error:
            try:
                detail = response.json().get("detail", response.status_code)
            except ValueError:
                detail = response.status_code
            raise RuntimeError(f"Server: {detail}")
        return response.json()

    def heartbeat(self):
        body = {}
        if self.operations:
            adapter = self.operations.adapter
            body = {
                "protocol": 1,
                "file_identity": adapter.identity,
                "file_name": adapter.path.name,
                "account_creation": 1,
            }
            try:
                adapter.ready()
                body.update(ready=True, message="Quicken ready")
            except Exception as exc:  # noqa: BLE001 - COM/Win32 readiness errors must not terminate archive sync
                body.update(ready=False, message=str(exc))
        result = self.request("POST", "/api/device/heartbeat", json=body, timeout=15)
        self.protocol = result.get("protocol", 0)
        status = {"connected": True, "desktop": bool(self.operations) and self.protocol == 1}
        status["readiness"] = (
            "" if body.get("ready") else body.get("message", "Configure the Quicken data file")
        )
        self.update_status(**status)
        return result

    def desktop_cycle(self):
        if self.stop.is_set() or not self.operations or self.protocol != 1:
            return
        ops = self.operations
        while not self.commands.empty() and not self.stop.is_set():
            kind, request_id = self.commands.get_nowait()
            try:
                run = ops.start(kind, request_id=request_id)
                self.manual_runs.add(run["id"])
            except Exception:
                self.commands.put((kind, request_id))
                raise
        pending = self.request("GET", "/api/device/operations")
        self.manual_runs.intersection_update(run["id"] for run in pending)
        for run in pending:
            if self.stop.is_set():
                return
            if run["id"] in self.manual_runs:
                ops.process(run, manual=True)
            elif not (run.get("background") and self.auto_exports_paused.is_set()):
                ops.process(run)
            else:
                continue
            self.last_refresh = time.time()
        accounts = self.request("GET", "/api/device/account-requests")
        active_ids = {account["id"] for account in accounts}
        self.account_retries = {
            key: value for key, value in self.account_retries.items() if key in active_ids
        }
        # Recover an in-flight creation before claiming any later request.
        accounts.sort(key=lambda item: (item["status"] != "creating", item["created"]))
        if accounts and not self.stop.is_set():
            account = accounts[0]
            if time.time() >= self.account_retries.get(account["id"], 0):
                self.account_retries[account["id"]] = time.time() + 60
                ops.process_account(account)
                self.last_refresh = time.time()
        pending = self.request("GET", "/api/device/operations")
        if self.stop.is_set():
            return
        if (
            not pending
            and not accounts
            and not self.auto_exports_paused.is_set()
            and time.time() - self.last_refresh > AUTO_EXPORT_INTERVAL_SECONDS
        ):
            # Background exports wait for an idle desktop without a fullscreen app.
            ops.adapter.ready(background=True)
            run = ops.start("refresh", background=True)
            self.last_refresh = time.time()
            if not self.stop.is_set():
                ops.process(run)
        self.update_status(waiting="Automatic exports paused" if self.auto_exports_paused.is_set() else "")

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
        current = self.request("GET", "/api/device/reference")
        with sqlite3.connect(self.journal_path) as journal:
            acknowledged = journal.execute(
                "SELECT 1 FROM exports WHERE scope=? AND sha=?", (self.server_scope, sha)
            ).fetchone()
        # A server restore can lose a snapshot while retaining the device token.
        # Archived and held versions still count as stored; do not reactivate them.
        if acknowledged and any(export["sha256"] == sha for export in current["exports"]):
            return
        result = self.request(
            "POST",
            "/api/device/reference",
            data={
                "expected_digest": current.get("digest") or "empty",
                "source_modified": str(before.st_mtime_ns),
            },
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
            self.desktop_cycle()
            self.last_desktop_message = None
        except (PairingRevoked, ServerUnavailable, httpx.HTTPError):
            raise
        except Exception as exc:  # noqa: BLE001 - desktop adapter faults are isolated from archival
            message = "Quicken: " + str(exc)
            if message != self.last_desktop_message:
                self.report(message)
                self.last_desktop_message = message
            self.update_status(waiting=message)
        try:
            self.sync_export()
        except (PairingRevoked, ServerUnavailable, httpx.HTTPError):
            raise
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            self.report("QIF sync failed. Check the export file and connection; the next cycle will retry.")

        sources = sorted(self.input_dir.iterdir())
        retained = {str(source) for source in sources}
        if self.qif_path:
            retained.add(str(self.qif_path))
        self.stable = {key: value for key, value in self.stable.items() if key in retained}
        for source in sources:
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
                except (PairingRevoked, ServerUnavailable, httpx.HTTPError):
                    raise
                except (OSError, RuntimeError, ValueError, sqlite3.Error):
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
            backoff = Backoff()
            try:
                while not self.stop.is_set():
                    try:
                        self.heartbeat()
                        backoff.reset()
                        delay = 10
                    except PairingRevoked:
                        self.revoked()
                        return
                    except (ServerUnavailable, httpx.HTTPError, OSError):
                        self.update_status(
                            connected=False, desktop=False, waiting="Server unavailable; retrying"
                        )
                        delay = backoff.next()
                    self.stop.wait(delay)
            except Exception as exc:  # noqa: BLE001 - report heartbeat death to the supervisor
                self.heartbeat_error = type(exc).__name__
                self.stop.set()

        heartbeat_thread = threading.Thread(target=pulse, daemon=True)
        heartbeat_thread.start()
        backoff = Backoff()
        try:
            while not self.stop.is_set():
                try:
                    self.cycle()
                    backoff.reset()
                    delay = 5
                except PairingRevoked:
                    self.revoked()
                    break
                except (ServerUnavailable, httpx.HTTPError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
                        self.revoked()
                        break
                    self.update_status(connected=False, desktop=False, waiting="Server unavailable; retrying")
                    delay = backoff.next()
                except (OSError, RuntimeError, ValueError, sqlite3.Error):
                    self.update_status(waiting="Archive sync interrupted; check folders and connection")
                    delay = backoff.next()
                if not heartbeat_thread.is_alive() and not self.stop.is_set():
                    raise RuntimeError("Heartbeat worker exited unexpectedly")
                self.stop.wait(delay)
        finally:
            self.stop.set()
            # Own the HTTP client until BOTH workers have stopped. The GUI has a
            # separate bounded shutdown deadline and never closes an in-use client.
            heartbeat_thread.join()
            self.http.close()
        if self.heartbeat_error:
            raise RuntimeError("Heartbeat worker stopped unexpectedly: " + self.heartbeat_error)

    def revoked(self):
        self.fatal_reason = "pairing"
        self.update_status(connected=False, desktop=False, waiting="Pairing required")
        self.attention("pairing", "Pairing no longer accepted. Open settings to pair again.")
        self.stop.set()
