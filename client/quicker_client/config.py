"""Atomic per-user settings with validated recovery and stale-window protection."""

import hashlib
import json
import os
import shutil
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def decode_settings(content):
    # json.loads(bytes) supports UTF-8 (including BOM) and UTF-16 Windows editors.
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ConfigurationError("Settings must be an object")
    for key in ("server", "token", "device_id", "input", "archive", "qif_path", "data_file"):
        if key in value and not isinstance(value[key], str):
            raise ConfigurationError("Invalid saved setting: " + key)
    for key in ("start_with_windows", "start_minimized", "auto_exports_paused"):
        if key in value and not isinstance(value[key], bool):
            raise ConfigurationError("Invalid saved setting: " + key)
    if value.get("token") and not value.get("server"):
        raise ConfigurationError("Paired settings are missing the server address")
    return value


def revision(content):
    return hashlib.sha256(content).digest() if content is not None else None


def atomic_write(path, content):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        if os.name != "nt":
            temporary.chmod(0o600)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class ConfigurationStore:
    def __init__(self, directory):
        self.path = Path(directory).resolve() / "config.json"
        self.backup = self.path.with_name("config.backup.json")
        self.loaded_revision = None
        self.loaded = False
        self.recovered = False

    def read_primary(self):
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def load(self):
        self.loaded = False
        self.recovered = False
        try:
            content = self.read_primary()
            self.loaded_revision = revision(content)
            if content is not None:
                value = decode_settings(content)
                # An empty/truncated-to-object file is not a first run if a
                # previously paired recovery copy exists.
                if value or not self.backup.exists():
                    self.loaded = True
                    return value
            elif not self.backup.exists():
                self.loaded = True
                return {}
        except (OSError, ValueError):
            pass
        try:
            value = decode_settings(self.backup.read_bytes())
            if not value:
                raise ConfigurationError("Recovery settings are empty")
            self.recovered = True
            self.loaded = True
            return value
        except (OSError, ValueError) as exc:
            raise ConfigurationError(
                "Saved settings could not be loaded; existing files were preserved"
            ) from exc

    def save(self, value):
        if not self.loaded:
            raise ConfigurationError("Reload saved settings before saving")
        encoded = json.dumps(value, indent=2, ensure_ascii=True).encode("utf-8")
        decode_settings(encoded)
        current = self.read_primary()
        if revision(current) != self.loaded_revision:
            raise ConfigurationError("Saved settings changed on disk. Reload them before saving")
        previous = None
        if current:
            try:
                previous = decode_settings(current)
            except ValueError:
                pass
        if previous and previous.get("token") and not value.get("token"):
            raise ConfigurationError("Refusing to replace saved pairing with empty settings")
        if previous:
            atomic_write(self.backup, json.dumps(previous, indent=2).encode("utf-8"))
        atomic_write(self.path, encoded)
        self.loaded_revision = revision(encoded)
        # The backup represents the latest successfully saved configuration,
        # including a new pairing or changed folder. Never back up invalid input.
        atomic_write(self.backup, encoded)
        self.recovered = False


def migrate_legacy_state(destination, candidates, lock_factory):
    """Copy an inactive legacy installation, publishing configuration last.

    Preserve the entire journal tree and keep the original available for rollback.
    Never combine identities from different installations.
    """
    destination = Path(destination)
    if (destination / "config.json").exists() or (destination / "config.backup.json").exists():
        return None
    sources = []
    for candidate in candidates:
        candidate = Path(candidate)
        if not candidate.is_dir() or candidate.resolve() == destination.resolve():
            continue
        try:
            value = ConfigurationStore(candidate).load()
        except (OSError, ValueError):
            continue
        if value.get("token") and not any(candidate.samefile(source[0]) for source in sources):
            sources.append((candidate, value))
    if not sources:
        return None
    if len(sources) != 1:
        raise ConfigurationError(
            "Multiple saved companion installations found. Select the intended state directory."
        )
    source, value = sources[0]
    lock = lock_factory(str(source / "companion.lock"))
    if not lock.tryLock(0):
        raise ConfigurationError("Quit the previous Quicker companion before migrating its saved settings.")
    try:
        value = ConfigurationStore(source).load()
        if not value.get("token"):
            raise ConfigurationError(
                "Legacy pairing changed before migration; reload the intended installation."
            )
        marker = destination / "migration.json"
        if marker.exists():
            if json.loads(marker.read_bytes()).get("source") != str(source):
                raise ConfigurationError(
                    "A different state migration is unfinished; preserve both installations."
                )
        elif any(item.name != "companion.lock" for item in destination.iterdir()):
            raise ConfigurationError("Existing companion state has no settings; recover it before migrating.")
        atomic_write(marker, json.dumps({"source": str(source)}).encode("utf-8"))
        # Ignore source locks and diagnostic logs. Keep every receipt and journal.
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("config*.json", "*.tmp", "companion.lock", "companion.log*"),
        )
        encoded = json.dumps(value, indent=2).encode("utf-8")
        atomic_write(destination / "config.backup.json", encoded)
        atomic_write(destination / "config.json", encoded)
        marker.unlink()
        return source
    finally:
        lock.unlock()
