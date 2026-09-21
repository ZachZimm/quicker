import hashlib
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from quicker_client.lifecycle import Backoff, SafeFormatter, startup_command
from quicker_client.sync import PairingRevoked
from test_companion import make_engine


def test_backoff_is_bounded_and_resets():
    backoff = Backoff()
    assert [backoff.next() for _ in range(8)] == [5, 10, 20, 40, 60, 60, 60, 60]
    backoff.reset()
    assert backoff.next() == 5


def test_startup_quotes_packaged_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\Example User\Quicker\Quicker.exe")
    assert startup_command() == '"C:\\Users\\Example User\\Quicker\\Quicker.exe" --startup'


def test_logging_redacts_pairing_secrets(tmp_path):
    import logging

    formatter = SafeFormatter(lambda: ["secret-token", "pair-code"])
    record = logging.LogRecord("quicker_client", logging.INFO, "", 0, "secret-token pair-code", (), None)
    result = formatter.format(record)
    assert "secret-token" not in result and "pair-code" not in result


def test_pause_keeps_archives_and_explicit_refresh_available(tmp_path, photo):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/upload":
            return httpx.Response(
                200,
                json={
                    "stored": True,
                    "pages": [
                        {
                            "id": "paused-upload",
                            "name": "invoice.png",
                            "sha256": hashlib.sha256(photo).hexdigest(),
                        }
                    ],
                },
            )
        if request.url.path == "/api/device/archive" and request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=[])

    engine = make_engine(tmp_path, handler)
    engine.protocol = 1
    engine.operations = SimpleNamespace(adapter=SimpleNamespace(ready=Mock()), start=Mock(), process=Mock())
    engine.pause_exports(True)
    source = engine.input_dir / "invoice.png"
    source.write_bytes(photo)
    engine.cycle()
    engine.cycle()
    assert "/api/device/archive" in paths
    assert not source.exists()
    assert (engine.archive_dir / "paused-upload_invoice.png").read_bytes() == photo
    engine.operations.start.assert_not_called()
    # An explicit operation from the server is processed even while periodic work is paused.
    engine.request = Mock(side_effect=[[{"id": "manual", "background": False}], [], []])
    engine.desktop_cycle()
    engine.operations.process.assert_called_once_with({"id": "manual", "background": False})
    engine.http.close()


def test_pause_defers_queued_background_but_local_refresh_overrides(tmp_path):
    engine = make_engine(tmp_path, lambda _: httpx.Response(200, json=[]))
    run = {"id": "overdue", "background": True}
    engine.protocol = 1
    engine.operations = SimpleNamespace(
        adapter=SimpleNamespace(ready=Mock()), start=Mock(return_value=run), process=Mock()
    )
    engine.pause_exports(True)
    engine.request = Mock(side_effect=[[run], [], [run], [run], [], []])
    engine.desktop_cycle()
    engine.operations.process.assert_not_called()
    engine.command("refresh")
    engine.desktop_cycle()
    engine.operations.process.assert_called_once_with(run, manual=True)
    engine.stop.set()
    engine.command("entry")
    assert engine.commands.empty()
    engine.http.close()


def test_revoked_pairing_is_terminal_and_status_is_deduplicated(tmp_path):
    engine = make_engine(tmp_path, lambda _: httpx.Response(401, json={"detail": "secret-server-text"}))
    statuses = []
    engine.status_report = statuses.append
    with pytest.raises(PairingRevoked, match="Pairing no longer accepted"):
        engine.heartbeat()
    engine.run()
    assert engine.stop.is_set() and engine.fatal_reason == "pairing"
    assert sum(bool(s.get("attention")) for s in statuses) == 1
    assert "secret-server-text" not in json.dumps(statuses)


def test_heartbeat_crash_stops_sync_and_closes_resources(tmp_path):
    engine = make_engine(tmp_path, lambda _: httpx.Response(200, json=[]))
    engine.heartbeat = Mock(side_effect=TypeError("unexpected worker defect"))
    with pytest.raises(RuntimeError, match="Heartbeat worker stopped unexpectedly"):
        engine.run()
    assert engine.stop.is_set() and engine.http.is_closed


def test_sync_crash_stops_heartbeat_before_closing_http(tmp_path):
    engine = make_engine(tmp_path, lambda _: httpx.Response(200, json=[]))
    pulse_started = threading.Event()
    pulse_stopped = threading.Event()

    def heartbeat():
        pulse_started.set()
        assert engine.stop.wait(2)
        assert not engine.http.is_closed
        pulse_stopped.set()

    def cycle():
        assert pulse_started.wait(2)
        raise TypeError("unexpected sync defect")

    engine.heartbeat = heartbeat
    engine.cycle = cycle
    with pytest.raises(TypeError):
        engine.run()
    assert pulse_stopped.is_set() and engine.http.is_closed


def test_rotating_logs_are_bounded(tmp_path):
    from quicker_client.lifecycle import configure_logging

    logger = configure_logging(tmp_path, list)
    handler = logger.handlers[0]
    handler.maxBytes = 150
    try:
        for index in range(100):
            logger.info("Bounded activity message %s", index)
        assert len(list(tmp_path.glob("companion.log*"))) == 4
        assert all(path.stat().st_size < 150 for path in tmp_path.glob("companion.log*"))
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_offscreen_lifecycle_controls_and_recovery(tmp_path):
    pytest.importorskip("PySide6")
    code = r"""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from quicker_client import app as module

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
module.set_startup = Mock()
root = module.state_directory()
root.mkdir(parents=True)
(root / 'config.json').write_text(json.dumps({'server': 'http://server', 'token': 'private-token', 'start_with_windows': False}))
window = module.Window(auto_connect=False)
window.tray.isVisible = lambda: True
window.show_initial()
assert not window.isVisible()
window.tray_activated(QSystemTrayIcon.ActivationReason.Context)
assert not window.isVisible()
window.tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
assert window.isVisible()
window.close()
assert not window.isVisible() and not window.closing
window.tray.isVisible = lambda: False
window.show_initial()
assert window.isVisible()
for i in range(600): window.log_message('event ' + str(i))
assert window.log.document().blockCount() == 500
window.log_message('private-token')
assert 'private-token' not in window.log.toPlainText()
window.tray.isVisible = lambda: True
window.tray.showMessage = Mock()
window.desired = True
window.engine = SimpleNamespace(stop=threading.Event(), fatal_reason=None, pause_exports=Mock())
status = {'connected': True, 'desktop': True, 'attention': {'key': 'uncertain', 'message': 'Review entry'}}
window.connection_status(status)
window.connection_status(status)
assert window.tray.showMessage.call_count == 1
window.connection_status({**status, 'attention': None})
window.connection_status(status)
assert window.tray.showMessage.call_count == 2
window.pause_action.setChecked(True)
window.engine.pause_exports.assert_called_with(True)
assert json.loads((root / 'config.json').read_text())['auto_exports_paused']
window.thread = SimpleNamespace(is_alive=lambda: False)
window.restarts = 3
window.poll_workers()
assert not window.desired and not window.refresh.isEnabled()
window.desired = True
window.thread = SimpleNamespace(is_alive=lambda: True)
real_quit = QApplication.quit
QApplication.quit = Mock()
window.quit()
assert window.closing and window.engine.stop.is_set()
assert not window.centralWidget().isEnabled()
QApplication.quit.assert_not_called()
window.shutdown_deadline = 0
window.poll_workers()
QApplication.quit.assert_called_once()
# Real QApplication.quit sends close events. A stopping window must accept them
# or the event loop stays alive after the shutdown timer has stopped.
QApplication.quit = real_quit
window.hide()
fresh = module.Window(directory=root / 'quit-check', auto_connect=False)
fresh.show()
from PySide6.QtCore import QTimer
QTimer.singleShot(0, fresh.quit)
QTimer.singleShot(3000, lambda: __import__('os')._exit(17))
assert app.exec() == 0
print('Lifecycle controls passed')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "LOCALAPPDATA": str(tmp_path),
            "QUICKER_STATE_DIR": str(tmp_path / "Quicker"),
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Lifecycle controls passed" in result.stdout
