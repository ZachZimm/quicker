"""Load installation-specific bookkeeping identities from untracked local data."""

import json
import os
from pathlib import Path


def profile_path():
    return Path(os.environ.get(
        "QUICKER_PROFILE_PATH",
        str(Path(os.environ.get("QUICKER_DATA_DIR", "data")) / "private-profile.json"),
    )).expanduser().resolve()


def load_private_profile():
    path = profile_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise TypeError("Expected an object")
        for key, kind in (("properties", list), ("parcels", dict), ("unit_mappings", list), ("legacy_units", dict)):
            if key in data and not isinstance(data[key], kind):
                raise TypeError(f"Invalid {key}")
        return data
    except (ValueError, TypeError, OSError) as exc:
        # Do not echo private configuration values in logs or error responses.
        raise ValueError("Cannot load the private property profile; check its JSON structure and permissions") from exc
