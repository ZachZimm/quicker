import importlib.util
import os
import subprocess
import sys

import pytest


def test_companion_window_constructs_offscreen(tmp_path):
    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("Install the client extra to validate the desktop window")
    code = """
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QTimer
from quicker_client.app import main
original = QApplication.exec
def inspect_and_run(app):
    def inspect():
        window = next(w for w in app.topLevelWidgets() if w.windowTitle() == 'Quicker companion')
        buttons = window.findChildren(QPushButton)
        entry = next(b for b in buttons if b.text() == 'Enter approved transactions')
        assert not entry.isEnabled()
        print('Companion window constructed; entry explicitly disabled', flush=True)
        app.quit()
    QTimer.singleShot(0, inspect)
    return original()
QApplication.exec = inspect_and_run
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "LOCALAPPDATA": str(tmp_path)},
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Companion window constructed" in result.stdout
