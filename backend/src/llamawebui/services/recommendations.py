"""Apply a bundled recommendation, and upgrade profiles added from one."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import group_quantization
from llamawebui.domain.model_profile import ModelProfile, launch_hash
from llamawebui.domain.profile_arguments import SCHEMA_VERSION, normalize_configuration
from llamawebui.domain.recommendation import (
    DRAFT_MODEL_ARGUMENT,
    Recommendation,
    select_draft_group,
    select_group,
)
from llamawebui.models import DownloadJobRecord, ModelProfileRecord
from llamawebui.services.download_coordinator import DownloadCoordinator
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.huggingface_catalog import Catalog
from llamawebui.services.model_artifact_registry import ModelArtifactRegistry
from llamawebui.services.profile_registry import (
    ProfileRegistry,
    RecommendationLink,
    configuration_payload,
    profile_from_record,
)

_REUSABLE_STATES = frozenset(
    {DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED, DownloadState.COMPLETED}
)


# Settings are compared on a fixed model and runtime, so only the arguments count.
_SETTINGS_MODEL = Path("model.gguf")
_SETTINGS_RUNTIME = Path("llama-server")


class ProfileUpgradeError(ValueError):
    pass


class RecommendationAlreadyAddedError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AppliedRecommendation:
    profile: ModelProfileRecord | None
    download: DownloadJobRecord
    ready: bool


def recommended_profile(
    recommendation: Recommendation,
    catalog: ArgumentCatalog,
    model_path: Path,
    alias: str | None = None,
    draft_path: Path | None = None,
) -> ModelProfile:
    arguments = list(recommendation.arguments)
    if draft_path is not None:
        arguments.append({"id": DRAFT_MODEL_ARGUMENT, "value": str(draft_path)})
    configuration = normalize_configuration(
        {"schema_version": SCHEMA_VERSION, "arguments": arguments}, catalog
    )
    return ModelProfile(
        alias=alias or recommendation.alias,
        model_path=model_path,
        arguments=configuration.arguments,
        acknowledged=configuration.acknowledged,
    )


def _is_location(argument: Mapping[str, object], catalog: ArgumentCatalog) -> bool:
    """Whether an argument names a file or folder on this machine, such as a draft model."""
    argument_id = argument.get("id")
    definition = catalog.get(argument_id) if isinstance(argument_id, str) else None
    return definition is not None and "path" in {definition.value.type, definition.value.item_type}


def _settings_hash(arguments: Iterable[Mapping[str, object]], catalog: ArgumentCatalog) -> str:
    """The launch hash of a set of stored arguments, on a fixed model and runtime."""
    configuration = normalize_configuration(
        {"schema_version": SCHEMA_VERSION, "arguments": list(arguments)}, catalog
    )
    profile = ModelProfile(
        alias="",
        model_path=_SETTINGS_MODEL,
        arguments=configuration.arguments,
        acknowledged=configuration.acknowledged,
    )
    return launch_hash(profile, catalog, _SETTINGS_RUNTIME)


def _same_settings(
    current: Sequence[Mapping[str, object]],
    baseline: Sequence[Mapping[str, object]],
    catalog: ArgumentCatalog,
) -> bool:
    """Whether a profile still launches with the arguments it was added with.

    File locations differ between machines, so they never count as a change.
    """
    locations = [item for item in current if _is_location(item, catalog)]
    original = [item for item in baseline if not _is_location(item, catalog)] + locations
    return _settings_hash(current, catalog) == _settings_hash(original, catalog)


def profile_hash(
    record: ModelProfileRecord, catalog: ArgumentCatalog, executable: Path
) -> str:
    return launch_hash(profile_from_record(record, catalog), catalog, executable)


def identical_profiles(
    profile: ModelProfile,
    executable: Path,
    records: Iterable[ModelProfileRecord],
    catalog: ArgumentCatalog,
    executables: Mapping[str, Path],
) -> list[ModelProfileRecord]:
    """The saved profiles that launch exactly what ``profile`` would on ``executable``."""
    wanted = launch_hash(profile, catalog, executable)
    return [
        record
        for record in records
        if record.runtime_id in executables
        and profile_hash(record, catalog, executables[record.runtime_id]) == wanted
    ]


def find_download(
    recommendation: Recommendation, jobs: Iterable[DownloadJobRecord]
) -> DownloadJobRecord | None:
    """A local download of the recommended model, found without asking the hub.

    A finished download is preferred. Without a pinned revision any revision counts.
    """
    model = recommendation.model
    wanted = model.quantization.upper()
    candidates = [
        job
        for job in jobs
        if job.repo_id == model.repo_id
        and (model.revision is None or job.revision == model.revision)
        and "mmproj" not in job.group_key.lower()
        and group_quantization(job.group_key) == wanted
        and DownloadState(job.state) in _REUSABLE_STATES
    ]
    return _preferred(candidates)


def find_draft_download(
    recommendation: Recommendation, jobs: Iterable[DownloadJobRecord], revision: str | None = None
) -> DownloadJobRecord | None:
    """A local download of the recommendation's draft model, preferably from ``revision``."""
    model = recommendation.model
    if model.draft_group is None:
        return None
    candidates = [
        job
        for job in jobs
        if job.repo_id == model.repo_id
        and (model.revision is None or job.revision == model.revision)
        and job.group_key.lower() == model.draft_group.lower()
        and DownloadState(job.state) in _REUSABLE_STATES
    ]
    same_revision = [job for job in candidates if job.revision == revision]
    return _preferred(same_revision or candidates)


def _preferred(candidates: Sequence[DownloadJobRecord]) -> DownloadJobRecord | None:
    """A finished download first, otherwise the first one still on its way."""
    completed = (job for job in candidates if DownloadState(job.state) is DownloadState.COMPLETED)
    return next(completed, next(iter(candidates), None))


def profile_files(record: ModelProfileRecord, catalog: ArgumentCatalog) -> list[Path]:
    """Every file a profile launches with: its model and any file arguments, such as a draft."""
    files = [Path(record.model_path)]
    for argument in _current_arguments(record, catalog):
        value = argument.get("value")
        if _is_location(argument, catalog) and isinstance(value, str) and value:
            files.append(Path(value))
    return files


def next_free_alias(alias: str, taken: Iterable[str]) -> str:
    """``alias`` when it is free, otherwise the first free ``alias-2``, ``alias-3``…"""
    used = set(taken)
    if alias not in used:
        return alias
    number = 2
    while f"{alias}-{number}" in used:
        number += 1
    return f"{alias}-{number}"


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
    return not _same_settings(_current_arguments(record, catalog), baseline, catalog)


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
        if _same_settings(_current_arguments(record, catalog), link.baseline, catalog):
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
    executables: Mapping[str, Path],
    alias: str | None = None,
    add_profile: bool = True,
) -> AppliedRecommendation:
    """Add the recommended profile, reusing matching downloads or starting them.

    The model and, when the recommendation names one, its draft model are downloaded.
    The profile is validated before anything is queued. While a file is still
    downloading the profile is saved disabled, and it switches on once all of them have
    completed. A profile that launches exactly the same command is never added twice.
    Without ``add_profile`` only the files are downloaded.
    """
    model = recommendation.model
    manifest = await hub.repository(model.repo_id, revision=model.revision)
    groups = [select_group(manifest.groups, model.quantization)]
    if model.draft_group is not None:
        groups.append(select_draft_group(manifest.groups, model.draft_group))
    known = downloads.list(include_hidden=True)
    found = [
        next(
            (
                item
                for item in known
                if item.repo_id == manifest.repo_id
                and item.revision == manifest.revision
                and item.group_key == group.key
                and DownloadState(item.state) in _REUSABLE_STATES
            ),
            None,
        )
        for group in groups
    ]
    existing = [artifacts.primary_path(job) if job is not None else None for job in found]
    if add_profile and None not in existing and runtime_id in executables:
        # A new download has a path no profile points at yet, so only reused ones can match.
        same = identical_profiles(
            _with_files(recommendation, catalog, existing, alias),
            executables[runtime_id],
            profiles.list(),
            catalog,
            executables,
        )
        if same:
            raise RecommendationAlreadyAddedError(
                f"this profile is already added as {same[0].alias}"
            )
    if add_profile:
        profiles.check_pending(
            profile=recommended_profile(recommendation, catalog, Path("pending.gguf"), alias),
            runtime_id=runtime_id,
        )
    created = [job is None for job in found]
    jobs = [
        job if job is not None else downloads.create(manifest, group.key)
        for job, group in zip(found, groups, strict=True)
    ]
    ready = [artifacts.validation_state(job) != "broken" for job in jobs]
    primaries = [artifacts.primary_path(job) for job in jobs]
    if None in primaries:
        raise ValueError("the selected download has no model file")
    profile = _with_files(recommendation, catalog, primaries, alias)
    record: ModelProfileRecord | None = None
    try:
        if add_profile and all(ready):
            record = profiles.create(
                profile=profile,
                runtime_id=runtime_id,
                enabled=True,
                exclusive=recommendation.exclusive,
                description=recommendation.description,
                link=recommendation_link(recommendation, catalog),
            )
        elif add_profile:
            # It waits on one unfinished download, then on any other still under way.
            waiting = next(job for job, done in zip(jobs, ready, strict=True) if not done)
            record = profiles.create_pending(
                profile=profile,
                runtime_id=runtime_id,
                download_id=waiting.id,
                exclusive=recommendation.exclusive,
                description=recommendation.description,
                link=recommendation_link(recommendation, catalog),
            )
    except Exception:
        for job, new in zip(jobs, created, strict=True):
            if new:
                await coordinator.cancel(job.id)
        raise
    started = [
        _start(coordinator, job, new, done)
        for job, new, done in zip(jobs, created, ready, strict=True)
    ]
    return AppliedRecommendation(profile=record, download=started[0], ready=all(ready))


def _with_files(
    recommendation: Recommendation,
    catalog: ArgumentCatalog,
    files: Sequence[Path | None],
    alias: str | None,
) -> ModelProfile:
    """The recommended profile on its model file and, when it has one, its draft model."""
    model, *draft = files
    assert model is not None
    return recommended_profile(recommendation, catalog, model, alias, draft[0] if draft else None)


def _start(
    coordinator: DownloadCoordinator, job: DownloadJobRecord, created: bool, ready: bool
) -> DownloadJobRecord:
    """Set a download going: a new one starts, a paused one resumes."""
    if created:
        coordinator.start(job.id)
    elif DownloadState(job.state) is DownloadState.PAUSED:
        return coordinator.resume(job.id)
    elif not ready and DownloadState(job.state) is DownloadState.COMPLETED:
        # The files of a finished download have gone missing: fetch them again.
        return coordinator.redownload(job.id)
    return job
