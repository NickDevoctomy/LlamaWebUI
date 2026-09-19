from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from llamawebui.app import create_app
from llamawebui.config import Settings


def test_health_creates_data_directory_without_exposing_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "data"
    settings = Settings(data_dir=data_dir, hf_token=SecretStr("hf_private"))

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "data_dir": str(data_dir.resolve()),
        "database_path": str(data_dir.resolve() / "llamawebui.db"),
        "hugging_face_token_configured": True,
    }
    assert "hf_private" not in response.text
    assert data_dir.is_dir()