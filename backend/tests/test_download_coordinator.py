import asyncio
from pathlib import Path

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.download_coordinator import DownloadCoordinator
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.download_worker import DownloadWorker
from llamawebui.services.huggingface_catalog import RepositoryManifest

pytestmark = pytest.mark.asyncio


def create_coordinator(tmp_path: Path) -> tuple[DownloadCoordinator, DownloadRegistry, str]:
    database = tmp_path / "app.db"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), tmp_path / "models")
    manifest = RepositoryManifest(
        "owner/model",
        "a" * 40,
        (GgufGroup("model", "Q4", (HubFile("model.gguf", 1),), 1, True),),
    )
    job = registry.create(manifest, "model")

    class BlockingTransfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    worker = DownloadWorker(registry, BlockingTransfer())
    return DownloadCoordinator(registry, worker), registry, job.id


async def test_coordinator_pauses_and_resumes_active_job(tmp_path: Path) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    coordinator.start(job_id)
    await asyncio.sleep(0)

    assert coordinator.pause(job_id).state == DownloadState.PAUSED
    with pytest.raises(ValueError, match="active transfer stops"):
        coordinator.resume(job_id)

    await coordinator.shutdown()
    assert registry.get(job_id).state == DownloadState.PAUSED

    assert coordinator.resume(job_id).state == DownloadState.QUEUED
    await asyncio.sleep(0)
    assert registry.get(job_id).state == DownloadState.DOWNLOADING
    await coordinator.shutdown()


async def test_coordinator_cancels_queued_job(tmp_path: Path) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    coordinator.start(job_id)

    assert coordinator.cancel(job_id).state == DownloadState.CANCELLED
    await coordinator.shutdown()
    assert registry.get(job_id).state == DownloadState.CANCELLED


async def test_startup_reconciles_interrupted_job(tmp_path: Path) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    registry.transition(job_id, DownloadState.DOWNLOADING)

    coordinator.start_pending()

    assert registry.get(job_id).state == DownloadState.PAUSED
    await coordinator.shutdown()