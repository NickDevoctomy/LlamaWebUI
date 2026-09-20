from pathlib import Path

from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.huggingface_catalog import RepositoryManifest
from llamawebui.services.model_library import ModelLibrary


def test_library_projects_only_complete_valid_downloads(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    manifest = RepositoryManifest(
        repo_id="owner/model-GGUF",
        revision="a" * 40,
        groups=(
            GgufGroup(
                key="Q4/model-Q4",
                quantization="Q4",
                files=(
                    HubFile("Q4/model-00001-of-00002.gguf", 3),
                    HubFile("Q4/model-00002-of-00002.gguf", 4),
                ),
                total_size=7,
                complete=True,
            ),
        ),
    )
    job = registry.create(manifest, "Q4/model-Q4")
    destination = Path(job.destination)
    destination.joinpath("Q4").mkdir(parents=True)
    destination.joinpath("Q4/model-00001-of-00002.gguf").write_bytes(b"one")
    destination.joinpath("Q4/model-00002-of-00002.gguf").write_bytes(b"four")
    registry.transition(job.id, DownloadState.DOWNLOADING)
    registry.update_progress(job.id, 7)
    registry.transition(job.id, DownloadState.COMPLETED)

    models = ModelLibrary(registry, model_root).list()

    assert len(models) == 1
    assert models[0].download_id == job.id
    assert models[0].primary_path == destination / "Q4/model-00001-of-00002.gguf"
    assert models[0].file_count == 2
    assert models[0].total_bytes == 7

    destination.joinpath("Q4/model-00002-of-00002.gguf").unlink()
    assert ModelLibrary(registry, model_root).list() == ()


def test_library_endpoint_returns_primary_profile_path(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        registry = app.state.download_registry
        manifest = RepositoryManifest(
            repo_id="owner/model-GGUF",
            revision="b" * 40,
            groups=(
                GgufGroup(
                    key="model-Q8_0",
                    quantization="Q8_0",
                    files=(HubFile("model-Q8_0.gguf", 4),),
                    total_size=4,
                    complete=True,
                ),
            ),
        )
        job = registry.create(manifest, "model-Q8_0")
        destination = Path(job.destination)
        destination.mkdir(parents=True)
        destination.joinpath("model-Q8_0.gguf").write_bytes(b"gguf")
        registry.transition(job.id, DownloadState.DOWNLOADING)
        registry.update_progress(job.id, 4)
        registry.transition(job.id, DownloadState.COMPLETED)

        response = client.get("/api/library")

    assert response.status_code == 200
    assert response.json() == [
        {
            "download_id": job.id,
            "repo_id": "owner/model-GGUF",
            "revision": "b" * 40,
            "group_key": "model-Q8_0",
            "primary_path": str(destination / "model-Q8_0.gguf"),
            "file_count": 1,
            "total_bytes": 4,
        }
    ]