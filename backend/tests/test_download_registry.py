from pathlib import Path
from unittest.mock import patch

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState, require_transition
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.services.download_registry import (
    DownloadJobNotFoundError,
    DownloadPlanError,
    DownloadRegistry,
)
from llamawebui.services.huggingface_catalog import RepositoryManifest


def manifest(*, complete: bool = True, total_size: int | None = 30) -> RepositoryManifest:
    return RepositoryManifest(
        repo_id="owner/model-GGUF",
        revision="a" * 40,
        groups=(
            GgufGroup(
                key="Q4/model-Q4",
                quantization="Q4",
                files=(HubFile("Q4/model-Q4-00001-of-00001.gguf", total_size),),
                total_size=total_size,
                complete=complete,
            ),
        ),
    )


@pytest.fixture
def registry(tmp_path: Path) -> DownloadRegistry:
    database = tmp_path / "app.db"
    upgrade_database(database)
    return DownloadRegistry(create_database_engine(database), tmp_path / "models")


def test_create_persists_revision_pinned_job(registry: DownloadRegistry) -> None:
    job = registry.create(manifest(), "Q4/model-Q4")

    assert job.state == DownloadState.QUEUED
    assert job.total_bytes == 30
    assert job.completed_bytes == 0
    assert job.destination.endswith("owner\\model-GGUF\\" + "a" * 40)
    assert registry.list()[0].id == job.id


@pytest.mark.parametrize(
    ("repository", "revision", "complete", "size", "message"),
    [
        ("../escape", "a" * 40, True, 30, "unsafe repository ID"),
        ("owner/model", "main", True, 30, "full commit SHA"),
        ("owner/model", "a" * 40, False, 30, "incomplete"),
        ("owner/model", "a" * 40, True, None, "size is unknown"),
    ],
)
def test_create_rejects_unsafe_or_incomplete_plans(
    registry: DownloadRegistry,
    repository: str,
    revision: str,
    complete: bool,
    size: int | None,
    message: str,
) -> None:
    source = manifest(complete=complete, total_size=size)
    source = RepositoryManifest(repository, revision, source.groups)

    with pytest.raises(DownloadPlanError, match=message):
        registry.create(source, "Q4/model-Q4")


def test_create_rejects_unknown_group_and_insufficient_space(
    registry: DownloadRegistry,
) -> None:
    with pytest.raises(DownloadPlanError, match="group not found"):
        registry.create(manifest(), "missing")

    with patch("llamawebui.services.download_registry.shutil.disk_usage") as disk_usage:
        disk_usage.return_value.free = 29
        with pytest.raises(DownloadPlanError, match="insufficient disk space"):
            registry.create(manifest(), "Q4/model-Q4")


def test_create_rejects_unsafe_repository_file_path(registry: DownloadRegistry) -> None:
    source = manifest()
    unsafe = GgufGroup(
        key=source.groups[0].key,
        quantization="Q4",
        files=(HubFile("../escape.gguf", 30),),
        total_size=30,
        complete=True,
    )

    with pytest.raises(DownloadPlanError, match="unsafe repository file path"):
        registry.create(RepositoryManifest(source.repo_id, source.revision, (unsafe,)), unsafe.key)


def test_download_state_transitions_are_guarded(registry: DownloadRegistry) -> None:
    job = registry.create(manifest(), "Q4/model-Q4")

    downloading = registry.transition(job.id, DownloadState.DOWNLOADING)
    paused = registry.transition(job.id, DownloadState.PAUSED)
    queued = registry.transition(job.id, DownloadState.QUEUED)
    cancelled = registry.transition(job.id, DownloadState.CANCELLED)

    assert downloading.state == DownloadState.DOWNLOADING
    assert paused.state == DownloadState.PAUSED
    assert queued.state == DownloadState.QUEUED
    assert cancelled.state == DownloadState.CANCELLED
    with pytest.raises(ValueError, match="invalid download state transition"):
        require_transition(DownloadState.CANCELLED, DownloadState.QUEUED)
    with pytest.raises(DownloadJobNotFoundError):
        registry.transition("missing", DownloadState.CANCELLED)


def test_progress_rejects_regression_and_ignores_terminal_job(registry: DownloadRegistry) -> None:
    job = registry.create(manifest(), "Q4/model-Q4")
    registry.transition(job.id, DownloadState.DOWNLOADING)
    registry.update_progress(job.id, 20)

    with pytest.raises(ValueError, match="outside the valid range"):
        registry.update_progress(job.id, 10)
    registry.transition(job.id, DownloadState.CANCELLED)

    assert registry.update_progress(job.id, 30).completed_bytes == 20