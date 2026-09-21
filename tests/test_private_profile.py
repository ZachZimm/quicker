import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from quicker.contracts import ModelConfig
from quicker.private_profile import load_private_profile, profile_path


def test_profile_defaults_to_private_data_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("QUICKER_PROFILE_PATH", raising=False)
    monkeypatch.setenv("QUICKER_DATA_DIR", str(tmp_path))
    assert profile_path() == tmp_path / "private-profile.json"
    assert load_private_profile() == {}
    expected = {"properties": [], "parcels": {}, "unit_mappings": [], "legacy_units": {}}
    profile_path().write_text(json.dumps(expected))
    assert load_private_profile() == expected


def test_explicit_profile_uses_synthetic_fixture(monkeypatch):
    path = Path(__file__).parent / "fixtures" / "private-profile.json"
    monkeypatch.setenv("QUICKER_PROFILE_PATH", str(path))
    assert load_private_profile() == json.loads(path.read_text())


@pytest.mark.parametrize("content", ['private-address-not-json', '["private-address"]', '{"properties":"private-address"}'])
def test_invalid_profile_has_a_sanitized_error(tmp_path, monkeypatch, content):
    path = tmp_path / "private-profile.json"
    path.write_text(content)
    monkeypatch.setenv("QUICKER_PROFILE_PATH", str(path))
    with pytest.raises(ValueError, match="Cannot load the private property profile") as error:
        load_private_profile()
    assert "private-address" not in str(error.value)


def test_bonsai_defaults_and_larger_response_budget():
    config = ModelConfig()
    assert config.model == "bonsai-2-27b"
    assert config.protocol == "chat-completions"
    assert config.output_limit == 32768
    assert ModelConfig(output_limit=65536).output_limit == 65536
    with pytest.raises(ValidationError):
        ModelConfig(output_limit=65537)
