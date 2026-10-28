from pathlib import Path

import pytest
from pydantic import ValidationError

from llamawebui.config import Settings


def test_settings_load_environment_and_resolve_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "portable-data"
    monkeypatch.setenv("LLAMAWEBUI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LLAMAWEBUI_PORT", "9123")
    # The Hugging Face token now lives in the credential store; a leftover entry is ignored.
    monkeypatch.setenv("HF_TOKEN", "hf_test_secret")

    settings = Settings()

    assert settings.data_dir == data_dir.resolve()
    assert settings.database_path == data_dir.resolve() / "llamawebui.db"
    assert settings.port == 9123
    assert not hasattr(settings, "hf_token")
    assert "hf_test_secret" not in repr(settings)


def test_settings_reject_invalid_port() -> None:
    with pytest.raises(ValidationError):
        Settings(port=70000)


def test_settings_reject_empty_event_history() -> None:
    with pytest.raises(ValidationError):
        Settings(event_history_capacity=0)
