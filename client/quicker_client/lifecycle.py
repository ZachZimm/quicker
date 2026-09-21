"""Per-user startup and bounded diagnostics; no desktop automation."""

import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def startup_command():
    args = [sys.executable]
    if not getattr(sys, "frozen", False):
        executable = Path(sys.executable).with_name("pythonw.exe")
        if executable.exists():
            args[0] = str(executable)
        args += ["-m", "quicker_client.app"]
    return subprocess.list2cmdline(args + ["--startup"])


def set_startup(enabled):
    if os.name != "nt":
        if enabled:
            raise OSError("Automatic sign-in startup is available on Windows only")
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, "Quicker", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, "Quicker")
            except FileNotFoundError:
                pass


class SafeFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.secrets = secrets

    def format(self, record):
        value = super().format(record)
        for secret in self.secrets():
            if secret:
                value = value.replace(secret, "[redacted]")
        return value


def configure_logging(directory, secrets):
    logger = logging.getLogger("quicker_client")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(
        Path(directory) / "companion.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(SafeFormatter(secrets))
    logger.addHandler(handler)
    return logger


class Backoff:
    def __init__(self):
        self.failures = 0

    def reset(self):
        self.failures = 0

    def next(self):
        self.failures = min(self.failures + 1, 5)
        return min(5 * 2 ** (self.failures - 1), 60)
