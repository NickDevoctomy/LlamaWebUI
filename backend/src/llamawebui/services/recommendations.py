"""Apply a bundled recommendation: queue its model and add its profile."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_profile import ModelProfile
from llamawebui.domain.profile_arguments import SCHEMA_VERSION, normalize_configuration
from llamawebui.domain.recommendation import Recommendation, select_group
from llamawebui.models import DownloadJobRecord, ModelProfileRecord
from llamawebui.services.download_coordinator import DownloadCoordinator
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.huggingface_catalog import Catalog
from llamawebui.services.model_artifact_registry import ModelArtifactRegistry
from llamawebui.services.profile_registry import ProfileRegistry

_REUSABLE_STATES = frozenset(
    {DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED, DownloadState.COMPLETED}
)


@dataclass(frozen=True, slots=True)
class AppliedRecommendation:
    profile: ModelProfileRecord
    download: DownloadJobRecord
    ready: bool


def recommended_profile(
    recommendation: Recommendation, catalog: ArgumentCatalog, model_path: Path
) -> ModelProfile:
    configuration = normalize_configuration(
        {"schema_version": SCHEMA_VERSION, "arguments": list(recommendation.arguments)}, catalog
    )
    return ModelProfile(
        alias=recommendation.alias,
        model_path=model_path,
        arguments=configuration.arguments,
        acknowledged=configuration.acknowledged,
    )


async def apply_recommendation(
    recommendation: Recommendation,
    *,
    runtime_id: str,
    hub: Catalog,
    catalog: ArgumentCatalog,
    profiles: ProfileRegistry,
    downloads: DownloadRegistry,
    artifacts: ModelArtifactRegistry,
    coordinator: DownloadCoordinator,
) -> AppliedRecommendation:
    """Add the recommended profile, reusing a matching download or starting one.

    The profile is validated before anything is queued. When the model is still
    downloading the profile is saved disabled and switches on when the download completes.
    """
    profiles.check_pending(
        profile=recommended_profile(recommendation, catalog, Path("pending.gguf")),
        runtime_id=runtime_id,
    )
    manifest = await hub.repository(
        recommendation.model.repo_id, revision=recommendation.model.revision
    )
    group = select_group(manifest.groups, recommendation.model.quantization)
    job = next(
        (
            item
            for item in downloads.list(include_hidden=True)
            if item.repo_id == manifest.repo_id
            and item.revision == manifest.revision
            and item.group_key == group.key
            and DownloadState(item.state) in _REUSABLE_STATES
        ),
        None,
    )
    created = job is None
    if job is None:
        job = downloads.create(manifest, group.key)
    ready = artifacts.validation_state(job) != "broken"
    primary = artifacts.primary_path(job)
    if primary is None:
        raise ValueError("the selected download has no model file")
    profile = recommended_profile(recommendation, catalog, primary)
    try:
        record = (
            profiles.create(profile=profile, runtime_id=runtime_id, enabled=True)
            if ready
            else profiles.create_pending(profile=profile, runtime_id=runtime_id, download_id=job.id)
        )
    except Exception:
        if created:
            await coordinator.cancel(job.id)
        raise
    if created:
        coordinator.start(job.id)
    elif DownloadState(job.state) is DownloadState.PAUSED:
        job = coordinator.resume(job.id)
    elif not ready and DownloadState(job.state) is DownloadState.COMPLETED:
        # The files of a finished download have gone missing: fetch them again.
        job = coordinator.redownload(job.id)
    return AppliedRecommendation(profile=record, download=job, ready=ready)
