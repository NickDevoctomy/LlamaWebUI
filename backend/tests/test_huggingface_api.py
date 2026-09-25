from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from huggingface_hub.errors import HfHubHTTPError
from requests import Response

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.huggingface_catalog import ModelSearchResult, RepositoryManifest


def error_response(status_code: int) -> Response:
    response = Response()
    response.status_code = status_code
    response.url = "https://huggingface.co/api/models"
    return response


class FakeCatalog:
    def __init__(self) -> None:
        self.search_call: tuple[str, str | None, int] | None = None
        self.repository_call: tuple[str, str | None] | None = None

    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 25
    ) -> tuple[ModelSearchResult, ...]:
        self.search_call = (query, sort, limit)
        return (
            ModelSearchResult(
                repo_id="owner/model-GGUF",
                downloads=12,
                likes=3,
                last_modified=datetime(2026, 9, 19, tzinfo=UTC),
                gated=False,
                private=False,
                tags=("gguf",),
            ),
        )

    async def repository(
        self, repo_id: str, *, revision: str | None = None
    ) -> RepositoryManifest:
        self.repository_call = (repo_id, revision)
        return RepositoryManifest(
            repo_id=repo_id,
            revision="abc123",
            groups=(
                GgufGroup(
                    key="model-Q4_K_M",
                    quantization="Q4_K_M",
                    files=(HubFile("model-Q4_K_M.gguf", 42),),
                    total_size=42,
                    complete=True,
                ),
            ),
            readme="# Model card\n\nRepository description.",
        )


def test_huggingface_search_and_repository_endpoints(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    with TestClient(create_app(Settings(data_dir=tmp_path), catalog=catalog)) as client:
        search = client.get(
            "/api/huggingface/models", params={"q": "qwen", "sort": "downloads", "limit": 10}
        )
        repository = client.get(
            "/api/huggingface/repositories/owner/model-GGUF", params={"revision": "main"}
        )

    assert search.status_code == 200
    assert search.json()[0]["repo_id"] == "owner/model-GGUF"
    assert search.json()[0]["last_modified"] == "2026-09-19T00:00:00Z"
    assert catalog.search_call == ("qwen", "downloads", 10)
    assert repository.status_code == 200
    assert repository.json()["revision"] == "abc123"
    assert repository.json()["readme"] == "# Model card\n\nRepository description."
    assert repository.json()["groups"][0]["files"] == [
        {"path": "model-Q4_K_M.gguf", "size": 42}
    ]
    assert catalog.repository_call == ("owner/model-GGUF", "main")


def test_huggingface_query_validation(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path), catalog=FakeCatalog())) as client:
        assert client.get("/api/huggingface/models", params={"q": ""}).status_code == 422
        assert client.get(
            "/api/huggingface/models", params={"q": "qwen", "limit": 101}
        ).status_code == 422


def test_huggingface_errors_are_redacted(tmp_path: Path) -> None:
    class FailingCatalog(FakeCatalog):
        async def search(
            self, query: str, *, sort: str | None = None, limit: int = 25
        ) -> tuple[ModelSearchResult, ...]:
            raise HfHubHTTPError(
                "authorization failed for hf_secret", response=error_response(401)
            )

        async def repository(
            self, repo_id: str, *, revision: str | None = None
        ) -> RepositoryManifest:
            raise HfHubHTTPError("private details", response=error_response(404))

    with TestClient(create_app(Settings(data_dir=tmp_path), catalog=FailingCatalog())) as client:
        search = client.get("/api/huggingface/models", params={"q": "qwen"})
        repository = client.get("/api/huggingface/repositories/missing/model")

    assert search.status_code == 502
    assert search.json() == {"detail": "Hugging Face request failed"}
    assert "hf_secret" not in search.text
    assert repository.status_code == 404
    assert repository.json() == {"detail": "Hugging Face request failed"}