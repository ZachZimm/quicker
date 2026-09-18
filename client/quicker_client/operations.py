"""Recoverable desktop controller; no UI action is retried after a claim/attempt."""

import json
import os
import sqlite3
from pathlib import Path
from time import time
from uuid import uuid4

from .desktop import DesktopUnavailable


class Operations:
    def __init__(self, companion, adapter, directory):
        self.client, self.adapter = companion, adapter
        self.directory = Path(directory) / "desktop" / companion.server_scope
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = self.directory / "journal.sqlite3"
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.owner = self.get("owner") or str(uuid4())
        self.put("owner", self.owner)

    def db(self):
        db = sqlite3.connect(self.journal)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def get(self, key):
        with self.db() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def call(self, path, **body):
        return self.client.request("POST", path, json={"owner": self.owner, **body})

    def start(self, kind, background=False, request_id=None):
        return self.client.request(
            "POST",
            "/api/entry",
            json={"request_id": request_id or str(uuid4()), "kind": kind, "background": background},
        )

    def export(self, run, purpose):
        key = run["id"] + ":" + purpose
        saved = self.get(key)
        if saved and time() - saved["metadata"]["completed"] > 540:
            saved = None
        if not saved:
            current = self.client.request("GET", "/api/device/reference")
            event_id = str(uuid4())
            path = self.directory / (event_id + ".qif")
            started = time()
            self.put("export_attempt", {"id": event_id, "path": str(path), "started": started})
            content = self.adapter.export(path)
            # Adapter returns only a completed export. Flush the exact captured bytes before receipt upload.
            with path.open("wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            saved = {
                "path": str(path),
                "metadata": {
                    "id": event_id,
                    "run_id": run["id"],
                    "owner": self.owner,
                    "purpose": purpose,
                    "file_identity": self.adapter.identity,
                    "expected_generation": current["generation"],
                    "started": started,
                    "completed": time(),
                },
            }
            self.put(key, saved)
        content = Path(saved["path"]).read_bytes()
        receipt = self.client.request(
            "POST",
            "/api/device/export-events",
            data={"metadata": json.dumps(saved["metadata"])},
            files={"file": (Path(saved["path"]).name, content, "application/octet-stream")},
        )
        import hashlib

        if (
            receipt.get("sha256") != hashlib.sha256(content).hexdigest()
            or receipt.get("id") != saved["metadata"]["id"]
        ):
            raise RuntimeError("Server did not acknowledge the exact fresh export")
        if receipt["status"] != "active":
            raise RuntimeError(receipt.get("message") or "Export requires review before entry")
        return receipt

    def process(self, run):
        if run["file_identity"] != self.adapter.identity:
            raise DesktopUnavailable("This run requires a different configured Quicken file")
        # Readiness failures leave the request queued, allowing a closed dialog or idle desktop to recover.
        self.adapter.ready(background=run.get("background", False))
        run = self.call(f"/api/device/operations/{run['id']}/take")
        base = f"/api/device/operations/{run['id']}"
        key = run["id"] + ":phase"
        phase = self.get(key)
        try:
            with self.adapter.session(background=run.get("background", False)):
                if self.client.stop.is_set():
                    return
                if run["kind"] == "refresh":
                    receipt = self.export(run, "refresh")
                elif run["status"] == "queued" and phase not in ("claiming", "claimed", "attempting", "post"):
                    self.client.report("Creating a fresh Quicken reference before entry…")
                    receipt = self.export(run, "pre")
                    self.put(key, "claiming")
                    run = self.call(base + "/claim", event_id=receipt["id"])
                    self.put(key, "claimed")
                    for item in run["items"]:
                        if self.client.stop.is_set():
                            raise DesktopUnavailable("Disconnected before next transaction")

                        def before_submit(item=item):
                            if self.client.stop.is_set():
                                raise DesktopUnavailable("Disconnected before submission")
                            self.put(key, "attempting")
                            self.put("attempt:" + item["id"], {"started": time(), "item": item})
                            self.call(base + "/items/" + item["id"] + "/attempt")

                        self.adapter.enter(item, self.directory / "imports", before_submit)
                    self.put(key, "post")
                    receipt = self.export(run, "post")
                else:
                    self.client.report("Recovering: exporting Quicken to reconcile previous attempts…")
                    # A previous post export may predate manual corrections. A user-requested recovery needs a new event.
                    self.put(run["id"] + ":post", None)
                    receipt = self.export(run, "post")
                result = self.call(base + "/reconcile", event_id=receipt["id"])
                self.put(key, "finished")
                self.client.report(result["message"])
                for skipped in result.get("skipped", []):
                    self.client.report("Not entered: " + skipped["reason"])
                return result
        except Exception as exc:
            self.client.report("Quicken operation stopped: " + str(exc))
            try:
                self.call(base + "/stop", message=str(exc)[:1000])
            except Exception:  # noqa: BLE001 - preserve the original desktop failure when reporting also fails
                self.client.report(
                    "Server unavailable; durable journal retained. Reconnect to reconcile, never re-import manually."
                )
            raise
