from pathlib import Path

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.download_worker import DownloadWorker, HuggingFaceFileTransfer
from llamawebui.services.huggingface_catalog import RepositoryManifest

pytestmark = pytest.mark.asyncio


def create_registry(tmp_path: Path, sizes: tuple[int, ...]) -> tuple[DownloadRegistry, str]:
    database = tmp_path / "app.db"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), tmp_path / "models")
    files = tuple(HubFile(f"model-{index}.gguf", size) for index, size in enumerate(sizes))
    manifest = RepositoryManifest(
        repo_id="owner/model",
        revision="a" * 40,
        groups=(GgufGroup("model", "Q4", files, sum(sizes), True),),
    )
    return registry, registry.create(manifest, "model").id


async def test_worker_downloads_verifies_and_completes(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2, 3))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            assert repo_id == "owner/model"
            assert revision == "a" * 40
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * (2 if filename == "model-0.gguf" else 3))
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.COMPLETED
    assert job.completed_bytes == 5
    assert job.error is None
    assert (Path(job.destination) / "model-0.gguf").read_bytes() == b"xx"
    assert not list(Path(job.destination).parent.glob("*.partial"))


async def test_worker_records_size_failure(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2,))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.FAILED
    assert "size mismatch" in (job.error or "")
    assert not Path(job.destination).exists()


async def test_worker_does_not_override_cancelled_state(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (1, 1))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            registry.transition(job_id, DownloadState.CANCELLED)
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.CANCELLED
    assert job.completed_bytes == 0
    assert not Path(job.destination).exists()


async def test_worker_resumes_verified_staged_files(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2, 3))
    job = registry.get(job_id)
    staging = Path(job.destination).parent / f".{Path(job.destination).name}.{job.id}.partial"
    staging.mkdir(parents=True)
    (staging / "model-0.gguf").write_bytes(b"xx")
    calls: list[str] = []

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            calls.append(filename)
            target = destination / filename
            target.write_bytes(b"xxx")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert calls == ["model-1.gguf"]
    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_worker_rejects_transfer_path_outside_staging(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (1,))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = tmp_path / "outside.gguf"
            target.write_bytes(b"x")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.FAILED
    assert "outside the staging directory" in (job.error or "")
    assert not Path(job.destination).exists()


async def test_huggingface_transfer_forwards_pinned_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_download(**kwargs: object) -> str:
        captured.update(kwargs)
        target = tmp_path / "model.gguf"
        target.touch()
        return str(target)

    monkeypatch.setattr("llamawebui.services.download_worker.hf_hub_download", fake_download)

    result = await HuggingFaceFileTransfer("hf_secret").download(
        repo_id="owner/model",
        filename="model.gguf",
        revision="a" * 40,
        destination=tmp_path,
    )

    assert result == tmp_path / "model.gguf"
    assert captured == {
        "repo_id": "owner/model",
        "filename": "model.gguf",
        "revision": "a" * 40,
        "local_dir": tmp_path,
        "token": "hf_secret",
    }