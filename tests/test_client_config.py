import json
import os
import subprocess
import sys

import pytest
from quicker_client.config import ConfigurationError, ConfigurationStore, migrate_legacy_state


@pytest.fixture
def saved():
    return {
        "server": "http://server:8999",
        "token": "saved-test-credential",
        "device_id": "device",
        "input": "C:/Users/Test/Desktop/Quicker Input",
        "archive": "C:/Users/Test/Archive",
        "data_file": "C:/Users/Test/Quicken.QDF",
    }


def test_restart_preserves_pairing_and_makes_recovery_copy(tmp_path, saved, monkeypatch):
    store = ConfigurationStore(tmp_path)
    assert store.load() == {}
    store.save(saved)
    elsewhere = tmp_path / "different-launch-folder"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    reopened = ConfigurationStore(tmp_path)
    assert reopened.load() == saved
    assert json.loads(reopened.backup.read_bytes()) == saved
    assert not reopened.recovered


@pytest.mark.parametrize("damage", [None, b"{", b"{}", b"[]", b'{"token": 42}'])
def test_recovers_saved_pairing_after_config_damage(tmp_path, saved, damage):
    store = ConfigurationStore(tmp_path)
    store.load()
    store.save(saved)
    if damage is None:
        store.path.unlink()
    else:
        store.path.write_bytes(damage)
    reopened = ConfigurationStore(tmp_path)
    assert reopened.load() == saved
    assert reopened.recovered
    reopened.save(saved)
    assert json.loads(reopened.path.read_bytes()) == saved
    assert json.loads(reopened.backup.read_bytes()) == saved


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-8"])
def test_accepts_windows_json_encodings(tmp_path, saved, encoding):
    saved["input"] = "C:/Users/Test/Desktop/café"
    (tmp_path / "config.json").write_bytes(json.dumps(saved, ensure_ascii=False).encode(encoding))
    assert ConfigurationStore(tmp_path).load() == saved


def test_unreadable_config_cannot_be_overwritten(tmp_path, saved):
    store = ConfigurationStore(tmp_path)
    store.path.write_bytes(b"broken original")
    with pytest.raises(ConfigurationError):
        store.load()
    with pytest.raises(ConfigurationError):
        store.save(saved)
    assert store.path.read_bytes() == b"broken original"


def test_stale_empty_window_cannot_replace_restored_settings(tmp_path, saved):
    stale = ConfigurationStore(tmp_path)
    stale.load()
    other = ConfigurationStore(tmp_path)
    other.load()
    other.save(saved)
    with pytest.raises(ConfigurationError, match="changed on disk"):
        stale.save({"server": "http://localhost:8999"})
    assert stale.load() == saved
    with pytest.raises(ConfigurationError, match="empty settings"):
        stale.save({"server": "http://localhost:8999"})
    assert json.loads(stale.path.read_bytes()) == saved


def test_paired_window_restores_and_connects_without_code(tmp_path, saved):
    pytest.importorskip("PySide6")
    directory = tmp_path / "Quicker"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps(saved), encoding="utf-8")
    code = r"""
from threading import Event
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from quicker_client import app as module
module.set_startup = lambda _: None
received = []
class Companion:
    def __init__(self, server, token, *args, **kwargs):
        received.append((server, token))
        self.stop = Event()
        self.fatal_reason = None
    def run(self): self.stop.wait(5)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
window = module.Window(companion_factory=Companion)
def inspect():
    assert window.server.text() == 'http://server:8999'
    assert window.data_file.text() == 'C:/Users/Test/Quicken.QDF'
    assert window.code.text() == ''
    assert window.pairing_status.text().startswith('Paired')
    assert received == [('http://server:8999', 'saved-test-credential')]
    assert window.settings.backup.exists()
    window.quit()
QTimer.singleShot(100, inspect)
QTimer.singleShot(5000, lambda: __import__('os')._exit(17))
assert app.exec() == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={
            **os.environ,
            "LOCALAPPDATA": str(tmp_path),
            "QUICKER_STATE_DIR": str(tmp_path / "Quicker"),
            "QT_QPA_PLATFORM": "offscreen",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_migration_preserves_journals_and_does_not_reimport_on_restart(tmp_path, saved):
    from unittest.mock import Mock

    source, destination = tmp_path / "virtualized", tmp_path / "shared"
    source.mkdir()
    destination.mkdir()
    store = ConfigurationStore(source)
    store.load()
    store.save(saved)
    (source / "sync.sqlite3").write_bytes(b"upload-receipts")
    (source / "desktop" / "owner").mkdir(parents=True)
    journal = source / "desktop" / "owner" / "journal.sqlite3"
    journal.write_bytes(b"existing-owner-and-uncertain-attempt")
    lock = Mock()
    lock.tryLock.return_value = True
    assert migrate_legacy_state(destination, [source, source], lambda _: lock) == source
    assert ConfigurationStore(destination).load() == saved
    assert (destination / "desktop" / "owner" / "journal.sqlite3").read_bytes() == journal.read_bytes()
    assert (destination / "sync.sqlite3").read_bytes() == b"upload-receipts"
    assert source.exists()
    lock.unlock.assert_called_once()
    journal.write_bytes(b"later-legacy-change")
    assert migrate_legacy_state(destination, [source], lambda _: lock) is None
    assert (destination / "desktop" / "owner" / "journal.sqlite3").read_bytes() != journal.read_bytes()


def test_migration_refuses_a_running_or_ambiguous_legacy_installation(tmp_path, saved):
    from unittest.mock import Mock

    source, other, destination = tmp_path / "old", tmp_path / "other", tmp_path / "new"
    for directory in (source, other, destination):
        directory.mkdir()
    for directory in (source, other):
        store = ConfigurationStore(directory)
        store.load()
        store.save(saved)
    lock = Mock()
    lock.tryLock.return_value = False
    with pytest.raises(ConfigurationError, match="Quit the previous"):
        migrate_legacy_state(destination, [source], lambda _: lock)
    with pytest.raises(ConfigurationError, match="Multiple saved"):
        migrate_legacy_state(destination, [source, other], lambda _: lock)
    assert not list(destination.iterdir())
