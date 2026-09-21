import asyncio
from pathlib import Path

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.download_coordinator import DownloadCoordinator
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.download_worker import DownloadWorker
from llamawebui.services.event_broker import EventBroker
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
    job = registry.get(job_id)
    staging = Path(job.destination).parent / f".{Path(job.destination).name}.{job.id}.partial"
    staging.mkdir()
    partial = staging / "model.gguf.incomplete"
    partial.write_bytes(b"partial")

    assert (await coordinator.pause(job_id)).state == DownloadState.PAUSED
    assert registry.get(job_id).state == DownloadState.PAUSED
    assert partial.read_bytes() == b"partial"

    assert coordinator.resume(job_id).state == DownloadState.QUEUED
    await asyncio.sleep(0)
    assert registry.get(job_id).state == DownloadState.DOWNLOADING
    await coordinator.shutdown()


async def test_coordinator_cancels_queued_job(tmp_path: Path) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    coordinator.start(job_id)
    job = registry.get(job_id)
    staging = Path(job.destination).parent / f".{Path(job.destination).name}.{job.id}.partial"
    staging.mkdir()
    (staging / "model.gguf.incomplete").write_bytes(b"partial")

    assert (await coordinator.cancel(job_id)).state == DownloadState.CANCELLED
    await coordinator.shutdown()
    assert registry.get(job_id).state == DownloadState.CANCELLED
    assert not staging.exists()


async def test_startup_reconciles_interrupted_job(tmp_path: Path) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    registry.transition(job_id, DownloadState.DOWNLOADING)

    coordinator.start_pending()

    assert registry.get(job_id).state == DownloadState.PAUSED
    await coordinator.shutdown()


async def test_coordinator_events_include_durable_progress(tmp_path: Path) -> None:
    _, registry, job_id = create_coordinator(tmp_path)
    broker = EventBroker()
    class BlockingTransfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    coordinator = DownloadCoordinator(
        registry, DownloadWorker(registry, BlockingTransfer()), event_broker=broker
    )

    coordinator.start(job_id)
    event = await anext(broker.subscribe(0))
    assert event.type == "download.started"
    assert event.data["job_id"] == job_id
    assert event.data["total_bytes"] == registry.get(job_id).total_bytes
    await coordinator.shutdown()


async def test_coordinator_ignores_duplicate_start_and_rejects_active_restart(
    tmp_path: Path,
) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    coordinator.start(job_id)
    coordinator.start(job_id)
    await asyncio.sleep(0)

    with pytest.raises(ValueError, match="active"):
        coordinator.redownload(job_id)

    await coordinator.shutdown()
    assert registry.get(job_id).state == DownloadState.PAUSED


async def test_coordinator_publishes_completed_and_failed_task_events(tmp_path: Path) -> None:
    coordinator, registry, job_id = create_coordinator(tmp_path)
    broker = EventBroker()

    class ImmediateTransfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return target

    coordinator = DownloadCoordinator(
        registry, DownloadWorker(registry, ImmediateTransfer()), event_broker=broker
    )
    coordinator.start(job_id)
    await asyncio.sleep(0.05)
    subscription = broker.subscribe(0)
    events = [await anext(subscription), await anext(subscription)]
    assert [event.type for event in events] == ["download.started", "download.completed"]
    subscription.close()
    await coordinator.shutdown()