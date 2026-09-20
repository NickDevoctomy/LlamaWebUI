from pathlib import Path

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.models import ModelProfileRecord
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.huggingface_catalog import RepositoryManifest
from llamawebui.services.model_artifact_registry import ModelArtifactError, ModelArtifactRegistry


def create_completed_artifact(
    tmp_path: Path,
) -> tuple[DownloadRegistry, ModelArtifactRegistry, str]:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    manifest = RepositoryManifest(
        "owner/model-GGUF",
        "a" * 40,
        (GgufGroup("model-Q4", "Q4", (HubFile("model-Q4.gguf", 4),), 4, True),),
    )
    job = registry.create(manifest, "model-Q4")
    destination = Path(job.destination)
    destination.mkdir(parents=True)
    destination.joinpath("model-Q4.gguf").write_bytes(b"gguf")
    registry.transition(job.id, DownloadState.DOWNLOADING)
    registry.update_progress(job.id, 4)
    registry.transition(job.id, DownloadState.COMPLETED)
    return registry, ModelArtifactRegistry(registry, model_root), job.id


def test_artifact_deletion_preserves_provenance_and_marks_profile_broken(tmp_path: Path) -> None:
    registry, artifacts, job_id = create_completed_artifact(tmp_path)
    job = registry.get(job_id)
    profile = ModelProfileRecord(
        id="profile",
        alias="model",
        runtime_id="runtime",
        model_path=str(Path(job.destination) / "model-Q4.gguf"),
        configuration={},
        preset="preset",
        enabled=True,
    )

    assert artifacts.profile_available(profile)
    source = artifacts.source_for_profile(profile)
    assert source is not None and source.download_id == job_id

    artifacts.delete(job_id)

    assert not Path(job.destination).exists()
    assert registry.get(job_id).state == DownloadState.COMPLETED
    assert not artifacts.profile_available(profile)
    assert artifacts.source_for_profile(profile) == source


def test_artifact_deletion_rejects_noncompleted_job(tmp_path: Path) -> None:
    registry, artifacts, job_id = create_completed_artifact(tmp_path)
    registry.reset_for_redownload(job_id)

    with pytest.raises(ModelArtifactError, match="only completed"):
        artifacts.delete(job_id)


def test_deleting_one_group_preserves_other_group_in_shared_destination(
    tmp_path: Path,
) -> None:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    revision = "b" * 40
    manifests = (
        RepositoryManifest(
            "owner/model-GGUF",
            revision,
            (GgufGroup("model-Q4", "Q4", (HubFile("model-Q4.gguf", 4),), 4, True),),
        ),
        RepositoryManifest(
            "owner/model-GGUF",
            revision,
            (GgufGroup("model-Q8", "Q8", (HubFile("model-Q8.gguf", 8),), 8, True),),
        ),
    )
    jobs = tuple(registry.create(manifest, manifest.groups[0].key) for manifest in manifests)
    destination = Path(jobs[0].destination)
    destination.mkdir(parents=True)
    destination.joinpath("model-Q4.gguf").write_bytes(b"four")
    destination.joinpath("model-Q8.gguf").write_bytes(b"eight888")
    for job in jobs:
        registry.transition(job.id, DownloadState.DOWNLOADING)
        registry.update_progress(job.id, job.total_bytes)
        registry.transition(job.id, DownloadState.COMPLETED)
    artifacts = ModelArtifactRegistry(registry, model_root)
    q4_profile = ModelProfileRecord(
        id="q4",
        alias="q4",
        runtime_id="runtime",
        model_path=str(destination / "model-Q4.gguf"),
        configuration={},
        preset="",
        enabled=True,
    )
    q8_profile = ModelProfileRecord(
        id="q8",
        alias="q8",
        runtime_id="runtime",
        model_path=str(destination / "model-Q8.gguf"),
        configuration={},
        preset="",
        enabled=True,
    )

    artifacts.delete(jobs[0].id)

    assert not destination.joinpath("model-Q4.gguf").exists()
    assert destination.joinpath("model-Q8.gguf").read_bytes() == b"eight888"
    assert not artifacts.profile_available(q4_profile)
    assert artifacts.profile_available(q8_profile)


def test_profile_health_uses_valid_duplicate_provenance(tmp_path: Path) -> None:
    registry, artifacts, valid_job_id = create_completed_artifact(tmp_path)
    valid_job = registry.get(valid_job_id)
    duplicate_manifest = RepositoryManifest(
        valid_job.repo_id,
        valid_job.revision,
        (
            GgufGroup(
                valid_job.group_key,
                "Q4",
                (HubFile("model-Q4.gguf", 4),),
                4,
                True,
            ),
        ),
    )
    invalid = registry.create(duplicate_manifest, valid_job.group_key)
    profile = ModelProfileRecord(
        id="profile",
        alias="model",
        runtime_id="runtime",
        model_path=str(Path(valid_job.destination) / "model-Q4.gguf"),
        configuration={},
        preset="",
        enabled=True,
    )

    assert registry.get(invalid.id).state == DownloadState.QUEUED
    assert artifacts.profile_available(profile)
    source = artifacts.source_for_profile(profile)
    assert source is not None
    assert source.download_id == valid_job.id
