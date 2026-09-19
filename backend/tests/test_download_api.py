from pathlib import Path

from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.huggingface_catalog import ModelSearchResult, RepositoryManifest


class FakeCatalog:
    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 25
    ) -> tuple[ModelSearchResult, ...]:
        return ()

    async def repository(
        self, repo_id: str, *, revision: str | None = None
    ) -> RepositoryManifest:
        return RepositoryManifest(
            repo_id=repo_id,
            revision="a" * 40,
            groups=(
                GgufGroup(
                    key="Q4/model-Q4",
                    quantization="Q4",
                    files=(HubFile("Q4/model-Q4.gguf", 42),),
                    total_size=42,
                    complete=True,
                ),
            ),
        )


def test_create_list_and_cancel_download(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"), catalog=FakeCatalog())
    with TestClient(app) as client:
        created = client.post(
            "/api/downloads",
            json={
                "repo_id": "owner/model-GGUF",
                "revision": "main",
                "group_key": "Q4/model-Q4",
            },
        )
        listed = client.get("/api/downloads")
        cancelled = client.post(f"/api/downloads/{created.json()['id']}/cancel")
        recancelled = client.post(f"/api/downloads/{created.json()['id']}/cancel")
        missing = client.post("/api/downloads/missing/cancel")

    assert created.status_code == 201
    assert created.json()["revision"] == "a" * 40
    assert created.json()["state"] == "queued"
    assert created.json()["total_bytes"] == 42
    assert len(listed.json()) == 1
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert recancelled.status_code == 409
    assert missing.status_code == 404


def test_create_download_reports_invalid_group(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"), catalog=FakeCatalog())
    with TestClient(app) as client:
        response = client.post(
            "/api/downloads",
            json={"repo_id": "owner/model-GGUF", "group_key": "missing"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "GGUF group not found: missing"}