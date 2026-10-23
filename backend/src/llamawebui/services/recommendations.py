"""Apply a bundled recommendation, and upgrade profiles added from one."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
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
from llamawebui.services.profile_registry import (
    ProfileRegistry,
    RecommendationLink,
    configuration_payload,
)

_REUSABLE_STATES = frozenset(
    {DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED, DownloadState.COMPLETED}
)


class ProfileUpgradeError(ValueError):
    pass


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


def _is_location(argument: Mapping[str, object], catalog: ArgumentCatalog) -> bool:
    """Whether an argument names a file or folder on this machine, such as a draft model."""
    argument_id = argument.get("id")
    definition = catalog.get(argument_id) if isinstance(argument_id, str) else None
    return definition is not None and "path" in {definition.value.type, definition.value.item_type}


def _settings(arguments: Iterable[Mapping[str, object]], catalog: ArgumentCatalog) -> list[str]:
    """The arguments that decide how a profile runs, in a comparable form.

    File locations differ between machines, so they never count as a change.
    """
    return sorted(
        json.dumps(dict(argument), sort_keys=True)
        for argument in arguments
        if not _is_location(argument, catalog)
    )


def recommended_arguments(
    recommendation: Recommendation, catalog: ArgumentCatalog
) -> list[dict[str, object]]:
    """The stored form of a recommendation's arguments."""
    profile = recommended_profile(recommendation, catalog, Path("model.gguf"))
    return [argument.to_payload() for argument in profile.arguments]


def recommendation_link(
    recommendation: Recommendation, catalog: ArgumentCatalog
) -> RecommendationLink:
    return RecommendationLink(
        recommendation_id=recommendation.id,
        version=recommendation.version,
        baseline=recommended_arguments(recommendation, catalog),
    )


def _current_arguments(
    record: ModelProfileRecord, catalog: ArgumentCatalog
) -> list[dict[str, object]]:
    arguments = configuration_payload(record, catalog).get("arguments")
    if not isinstance(arguments, list):
        return []
    return [item for item in arguments if isinstance(item, dict)]


def is_modified(record: ModelProfileRecord, catalog: ArgumentCatalog) -> bool:
    """Whether a recommended profile's settings changed since it was added or upgraded."""
    baseline = record.recommendation_baseline
    if not isinstance(baseline, list):
        return True
    return _settings(_current_arguments(record, catalog), catalog) != _settings(baseline, catalog)


def _find(
    recommendations: Sequence[Recommendation], recommendation_id: str | None
) -> Recommendation | None:
    return next((item for item in recommendations if item.id == recommendation_id), None)


def recommendation_status(
    record: ModelProfileRecord,
    recommendations: Sequence[Recommendation],
    catalog: ArgumentCatalog,
) -> dict[str, object] | None:
    """How a profile added from a recommendation compares with the bundled version."""
    if record.recommendation_id is None:
        return None
    recommendation = _find(recommendations, record.recommendation_id)
    modified = is_modified(record, catalog)
    latest = recommendation.version if recommendation is not None else None
    return {
        "id": record.recommendation_id,
        "title": recommendation.title if recommendation is not None else None,
        "latest_version": latest,
        "modified": modified,
        "upgradable": latest is not None and latest > record.version and not modified,
    }


def upgrade_recommended_profile(
    record: ModelProfileRecord,
    recommendations: Sequence[Recommendation],
    catalog: ArgumentCatalog,
    profiles: ProfileRegistry,
) -> ModelProfileRecord:
    """Move an unchanged recommended profile to the latest version of its recommendation.

    The alias, enabled state and every file location (model, draft model) are kept.
    """
    recommendation = _find(recommendations, record.recommendation_id)
    if recommendation is None:
        raise ProfileUpgradeError("this profile was not added from a bundled recommendation")
    if recommendation.version <= record.version:
        raise ProfileUpgradeError(f"{record.alias} is already at version {record.version}")
    if is_modified(record, catalog):
        raise ProfileUpgradeError(
            f"{record.alias} was changed after it was added, so it cannot be upgraded"
        )
    locations = [
        item for item in _current_arguments(record, catalog) if _is_location(item, catalog)
    ]
    arguments = [
        item
        for item in recommended_arguments(recommendation, catalog)
        if not _is_location(item, catalog)
    ] + locations
    configuration = normalize_configuration(
        {"schema_version": SCHEMA_VERSION, "arguments": arguments}, catalog
    )
    profile = ModelProfile(
        alias=record.alias,
        model_path=Path(record.model_path),
        arguments=configuration.arguments,
        acknowledged=configuration.acknowledged,
    )
    return profiles.update(
        record.id,
        profile=profile,
        runtime_id=record.runtime_id,
        enabled=record.enabled,
        exclusive=recommendation.exclusive,
        description=recommendation.description,
        link=RecommendationLink(
            recommendation_id=recommendation.id,
            version=recommendation.version,
            baseline=[argument.to_payload() for argument in profile.arguments],
        ),
    )


def link_unchanged_profiles(
    recommendations: Sequence[Recommendation],
    catalog: ArgumentCatalog,
    profiles: ProfileRegistry,
) -> tuple[str, ...]:
    """Link profiles added before links were kept, when they match a recommendation exactly.

    A profile that differs in any setting is left alone, so an upgrade can never
    overwrite changes made to it.
    """
    linked: list[str] = []
    for record in profiles.list():
        if record.recommendation_id is not None:
            continue
        recommendation = next(
            (item for item in recommendations if item.alias == record.alias), None
        )
        if recommendation is None:
            continue
        link = recommendation_link(recommendation, catalog)
        current = _settings(_current_arguments(record, catalog), catalog)
        if current == _settings(link.baseline, catalog):
            profiles.link_recommendation(record.id, link)
            linked.append(record.alias)
    return tuple(linked)


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
            profiles.create(
                profile=profile,
                runtime_id=runtime_id,
                enabled=True,
                exclusive=recommendation.exclusive,
                description=recommendation.description,
                link=recommendation_link(recommendation, catalog),
            )
            if ready
            else profiles.create_pending(
                profile=profile,
                runtime_id=runtime_id,
                download_id=job.id,
                exclusive=recommendation.exclusive,
                description=recommendation.description,
                link=recommendation_link(recommendation, catalog),
            )
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
