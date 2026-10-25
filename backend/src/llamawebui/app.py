"""FastAPI application factory."""

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel, Field, field_validator

from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.argument_catalog import ArgumentCatalog, default_catalog
from llamawebui.domain.benchmark import (
    Benchmark,
    SystemPrompt,
    load_benchmarks,
    load_system_prompts,
)
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.effective_catalog import build_effective_catalog
from llamawebui.domain.model_profile import (
    ModelProfile,
    ParsedCommand,
    ProfileValidationError,
    launch_vector,
    parse_command,
    redact_preset,
    render_command,
    render_preset_unchecked,
    validate_profile,
    write_combined_preset_atomic,
)
from llamawebui.domain.profile_arguments import (
    LEGACY_FIELDS,
    ArgumentIssue,
    ArgumentValueError,
    build_arguments,
    errors_of,
    migrate_legacy_configuration,
    normalize_configuration,
    warnings_of,
)
from llamawebui.domain.recommendation import (
    Recommendation,
    RecommendationError,
    assess,
    load_recommendations,
)
from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState
from llamawebui.models import (
    AccessTokenRecord,
    BenchmarkRunRecord,
    DownloadJobRecord,
    ModelProfileRecord,
    RuntimeRecord,
    ServerRunRecord,
)
from llamawebui.services.auth_service import (
    CSRF_HEADER,
    CSRF_HEADER_VALUE,
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    AuthenticationError,
    AuthService,
    InvalidPrivilegesError,
    LastAdministratorError,
    ProtectedUserError,
    RoleAlreadyExistsError,
    RoleInUseError,
    RoleNotFoundError,
    RoleProtectedError,
    SessionNotFoundError,
    UserAlreadyExistsError,
)
from llamawebui.services.authorization import privilege_for_request
from llamawebui.services.benchmark_linter import BenchmarkLinter, LinterError
from llamawebui.services.benchmark_runner import (
    DEFAULT_MAX_TOKENS,
    REASONING_LEVELS,
    TOOL_NAMES,
    TOOL_SUMMARIES,
    BenchmarkBusyError,
    BenchmarkClient,
    BenchmarkRunner,
    BenchmarkRunNotFoundError,
    BenchmarkRunRegistry,
)
from llamawebui.services.benchmark_workspace import WorkspaceError
from llamawebui.services.database_backup import backup_database
from llamawebui.services.diagnostics import DiagnosticsExporter
from llamawebui.services.download_coordinator import DownloadCoordinator
from llamawebui.services.download_registry import (
    DownloadJobNotFoundError,
    DownloadPlanError,
    DownloadRegistry,
)
from llamawebui.services.download_worker import (
    DownloadWorker,
    FileTransfer,
    HuggingFaceFileTransfer,
)
from llamawebui.services.event_broker import (
    ControlEvent,
    EventBroker,
    EventCursorError,
)
from llamawebui.services.hardware_profile import HardwareProfile, detect_hardware
from llamawebui.services.huggingface_catalog import (
    Catalog,
    CatalogUnavailableError,
    HuggingFaceCatalog,
)
from llamawebui.services.llama_release_installer import (
    GitHubReleaseClient,
    ReleaseInstallError,
    RuntimeInstaller,
    group_runtime_assets,
)
from llamawebui.services.logical_model_registry import LogicalModelRegistry
from llamawebui.services.login_throttle import LoginRateLimitedError, LoginThrottle
from llamawebui.services.model_artifact_registry import ModelArtifactError, ModelArtifactRegistry
from llamawebui.services.model_event_stream import stream_model_events
from llamawebui.services.model_library import ModelLibrary
from llamawebui.services.model_locator import ModelLocation, local_models, locate_model
from llamawebui.services.process_memory import ProcessMemorySampler
from llamawebui.services.profile_registry import (
    ProfileAliasExistsError,
    ProfileNotFoundError,
    ProfileRegistry,
    configuration_payload,
    profile_from_record,
)
from llamawebui.services.recommendations import (
    ProfileUpgradeError,
    RecommendationAlreadyAddedError,
    apply_recommendation,
    find_download,
    find_draft_download,
    identical_profiles,
    link_unchanged_profiles,
    next_free_alias,
    profile_files,
    profile_hash,
    recommendation_status,
    recommended_profile,
    upgrade_recommended_profile,
)
from llamawebui.services.router_client import (
    HttpRouterClient,
    RouterAPIError,
    RouterClient,
    RouterModel,
    RouterModelEvent,
)
from llamawebui.services.router_event_sync import RouterEventSynchronizer
from llamawebui.services.router_port import RouterPortProbe, probe_router_port
from llamawebui.services.router_supervisor import RouterRestartPolicy, RouterSupervisor
from llamawebui.services.runtime_probe import RuntimeProber, probe_runtime
from llamawebui.services.runtime_registry import (
    RuntimeAlreadyRegisteredError,
    RuntimeInUseError,
    RuntimeNotFoundError,
    RuntimeRegistry,
    runtime_capabilities,
)
from llamawebui.services.server_run_registry import ServerRunRegistry
from llamawebui.services.system_metrics import collect_system_metrics
from llamawebui.services.token_registry import AccessTokenNotFoundError, TokenRegistry
from llamawebui.version import __version__

MODEL_EVENT_KEEPALIVE_SECONDS = 15.0
DEFAULT_SYSTEM_PROMPT_ID = "coding-agent"
PROFILE_DESCRIPTION_LIMIT = 500
_ACTIVE_DOWNLOAD_STATES = frozenset(
    {DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED}
)
PROFILE_EXPORT_FORMAT = "llamawebui-profile-v2"
PROFILE_IMPORT_FORMATS = frozenset({"llamawebui-profile-v1", PROFILE_EXPORT_FORMAT})
LOGGER = logging.getLogger("llamawebui.app")


class RuntimeRegistrationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    executable_path: str
    backend: str | None = Field(default=None, max_length=50)


class RuntimeInstallRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=100)
    asset_name: str = Field(min_length=1, max_length=300)
    backend: str | None = Field(default=None, max_length=50)


class AdvancedOptionRequest(BaseModel):
    """Legacy ``advanced`` entry, accepted for backwards compatibility."""

    name: str
    value: str | int | bool = True


ArgumentValueInput = str | int | float | bool | list[str | int | float]


class ProfileArgumentRequest(BaseModel):
    """A catalog argument (``id``) or a raw custom/runtime parameter (``flag``)."""

    id: str | None = Field(default=None, max_length=100)
    flag: str | None = Field(default=None, max_length=200)
    value: ArgumentValueInput | None = True
    origin: str | None = Field(default=None, max_length=40)


class ProfileCreateRequest(BaseModel):
    alias: str
    runtime_id: str
    model_path: str
    enabled: bool = True
    exclusive: bool = False
    description: str | None = Field(default=None, max_length=PROFILE_DESCRIPTION_LIMIT)
    # Set by hand; left out, a new profile starts at 1 and an edited one moves up on change.
    version: int | None = Field(default=None, ge=1, le=1_000_000)
    arguments: list[ProfileArgumentRequest] | None = None
    acknowledged: list[str] = Field(default_factory=list, max_length=300)
    # Legacy flat fields remain accepted and are migrated onto catalog arguments.
    no_reasoning_preserve: bool = False
    n_gpu_layers: int | None = None
    ctx_size: int | None = None
    flash_attn: str | None = None
    load_mode: str | None = None
    lazy_mode: str | None = None
    cache_ram: int | None = None
    fit: str | None = None
    override_tensor: tuple[str, ...] = ()
    cache_type_k: str | None = None
    cache_type_v: str | None = None
    threads: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    advanced: tuple[AdvancedOptionRequest, ...] = ()

    @field_validator("description")
    @classmethod
    def blank_description_is_none(cls, value: str | None) -> str | None:
        return _profile_description(value)

    def to_domain(self, catalog: ArgumentCatalog) -> ModelProfile:
        """Build the domain profile, migrating any legacy fields before structured ones."""
        legacy = {
            key: value
            for key, value in self.model_dump(include=set(LEGACY_FIELDS)).items()
            # Identity checks: a legacy value of 0 is meaningful and must not compare as False.
            if value is not None and value is not False and value != () and value != []
        }
        migrated = migrate_legacy_configuration(legacy, catalog).arguments if legacy else ()
        try:
            structured = build_arguments(
                [entry.model_dump(exclude_none=True) for entry in self.arguments or []], catalog
            )
        except ArgumentValueError as error:
            raise ProfileValidationError(tuple(str(error).split("; "))) from error
        return ModelProfile(
            alias=self.alias,
            model_path=Path(self.model_path),
            arguments=(*migrated, *structured),
            acknowledged=frozenset(self.acknowledged),
        )


class ProfileImportRequest(BaseModel):
    document: dict[str, object]
    alias: str | None = Field(default=None, min_length=1, max_length=64)


class ProfileCommandImportRequest(BaseModel):
    command: str = Field(min_length=1, max_length=10000)
    alias: str = Field(min_length=1, max_length=64)
    runtime_id: str
    enabled: bool = False


class ProfileCommandParseRequest(BaseModel):
    command: str = Field(min_length=1, max_length=10000)
    runtime_id: str


class ExternalModelImportRequest(BaseModel):
    primary_path: str
    runtime_id: str
    alias: str


class DownloadCreateRequest(BaseModel):
    repo_id: str = Field(min_length=3, max_length=400)
    group_key: str = Field(min_length=1)
    revision: str | None = Field(default=None, max_length=100)
    include_projector: bool = False


class ServerStartRequest(BaseModel):
    runtime_id: str


class ServerRestartRequest(BaseModel):
    runtime_id: str | None = None


class RouterModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=400)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model ID must not be empty")
        return value


class AccessTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expiry_note: str | None = Field(default=None, max_length=500)


class ProfileCloneRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=64)


class ProfileEnabledRequest(BaseModel):
    enabled: bool


class BenchmarkRunRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=36)
    benchmark_id: str = Field(min_length=1, max_length=64)
    # None generates until the model stops or the profile's context is full.
    max_tokens: int | None = Field(default=DEFAULT_MAX_TOKENS, ge=16, le=1048576)
    # Let the model read and write files, confined to the run's own folder.
    tools: bool = True
    # Which tools it gets, by name; None gives every tool.
    tool_names: list[str] | None = Field(default=None, max_length=32)
    # A bundled system prompt to send first, by id; none sends the prompt on its own.
    system_prompt_id: str | None = Field(default=None, max_length=64)
    # "off" or an effort level for the chat template; none keeps the model's own default.
    reasoning_level: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    # Keep each tool call's arguments and whole result in its log, not a short excerpt.
    verbose_log: bool = False


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    description: str | None = Field(default=None, max_length=500)
    role_id: str | None = None


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    privileges: tuple[str, ...] = ()


class RecommendationApplyRequest(BaseModel):
    runtime_id: str = Field(min_length=1, max_length=36)
    # A name for another copy when the recommendation's own alias is taken.
    alias: str | None = Field(default=None, min_length=1, max_length=64)
    # False downloads the model without adding a profile.
    add_profile: bool = True


class UserUpdateRequest(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    role_id: str


def _runtime_payload(runtime: RuntimeRecord) -> dict[str, object]:
    probe_errors = tuple(runtime.probe_error.splitlines()) if runtime.probe_error else ()
    version_errors = tuple(
        error for error in probe_errors if error.startswith(("version probe", "help probe"))
    )
    device_errors = tuple(error for error in probe_errors if error.startswith("devices probe"))
    missing_router_options = () if "models-preset" in runtime.options else ("models-preset",)
    if device_errors:
        device_status = "unavailable"
    elif runtime.devices:
        device_status = "available"
    else:
        device_status = "none"
    diagnostics = list(probe_errors)
    diagnostics.extend(f"runtime does not support --{option}" for option in missing_router_options)
    return {
        "id": runtime.id,
        "name": runtime.name,
        "executable_path": runtime.executable_path,
        "build": runtime.build,
        "commit": runtime.commit,
        "backend": runtime.backend,
        "devices": runtime.devices,
        "options": runtime.options,
        "usable": bool(runtime.options) and not version_errors,
        "probe_error": runtime.probe_error,
        "probe_errors": list(probe_errors),
        "device_status": device_status,
        "router_compatible": not missing_router_options and not version_errors,
        "missing_router_options": list(missing_router_options),
        "diagnostics": diagnostics,
        "help_sha256": runtime.help_sha256,
        "flags_sha256": runtime.flags_sha256,
        "option_details_count": len(runtime.option_details or []),
    }


def _router_diagnostics(runtime: RuntimeRecord) -> tuple[str, ...]:
    diagnostics: list[str] = []
    if runtime.probe_error:
        diagnostics.extend(
            error
            for error in runtime.probe_error.splitlines()
            if error.startswith(("version probe", "help probe"))
        )
    if "models-preset" not in runtime.options:
        diagnostics.append("runtime does not support --models-preset")
    return tuple(diagnostics)


def _issues_payload(issues: tuple[ArgumentIssue, ...]) -> dict[str, object]:
    errors = errors_of(issues)
    return {
        "valid": not errors,
        "errors": list(errors),
        "warnings": list(warnings_of(issues)),
        "issues": [issue.to_payload() for issue in issues],
    }


def _argument_catalog(request: Request) -> ArgumentCatalog:
    return cast(ArgumentCatalog, request.app.state.argument_catalog)


def _registered_runtime(request: Request, runtime_id: str) -> RuntimeRecord:
    registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
    try:
        return registry.get(runtime_id)
    except RuntimeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


def _profile_issues(
    request: Request, profile: ModelProfile, runtime: RuntimeRecord
) -> tuple[ArgumentIssue, ...]:
    issues = validate_profile(profile, _argument_catalog(request), runtime_capabilities(runtime))
    router = tuple(
        ArgumentIssue("error", "router-incompatible", message)
        for message in _router_diagnostics(runtime)
    )
    return (*issues, *router)


def _locate_command_model(request: Request, parsed: ParsedCommand) -> ModelLocation:
    candidates = local_models(
        cast(DownloadRegistry, request.app.state.download_registry),
        cast(ModelArtifactRegistry, request.app.state.model_artifact_registry),
        cast(ModelLibrary, request.app.state.model_library),
    )
    return locate_model(
        parsed.model_path, candidates, hf_repo=parsed.hf_repo, hf_file=parsed.hf_file
    )


def _primary_path(artifacts: ModelArtifactRegistry, job: DownloadJobRecord | None) -> Path | None:
    if job is None:
        return None
    with suppress(ModelArtifactError):
        return artifacts.primary_path(job)
    return None


def _download_progress(job: DownloadJobRecord | None) -> dict[str, object] | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "state": job.state,
        "completed_bytes": job.completed_bytes,
        "total_bytes": job.total_bytes,
    }


def _unfinished_download(
    record: ModelProfileRecord,
    catalog: ArgumentCatalog,
    downloads: DownloadRegistry,
    artifacts: ModelArtifactRegistry,
) -> str | None:
    """A download still under way for one of the profile's files, such as its draft model."""
    files = {path.resolve() for path in profile_files(record, catalog)}
    for job in downloads.list(include_hidden=True):
        if DownloadState(job.state) not in _ACTIVE_DOWNLOAD_STATES:
            continue
        with suppress(ModelArtifactError):
            if artifacts.primary_path(job) in files:
                return job.id
    return None


def _runtime_executables(request: Request) -> dict[str, Path]:
    """Each registered runtime's llama-server executable, by runtime id."""
    registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
    return {runtime.id: Path(runtime.executable_path) for runtime in registry.list()}


def _saved_profile_payload(
    request: Request, record: ModelProfileRecord, profile: ModelProfile
) -> dict[str, object]:
    """Profile payload plus the non-blocking warnings that applied when it was saved."""
    catalog = _argument_catalog(request)
    artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
    payload = _profile_payload(
        record,
        artifacts,
        catalog=catalog,
        recommendations=request.app.state.recommendations,
        executables=_runtime_executables(request),
    )
    registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
    runtime = registry.get(record.runtime_id)
    issues = validate_profile(profile, catalog, runtime_capabilities(runtime))
    payload["warnings"] = list(warnings_of(issues))
    return payload


def _profile_payload(
    profile: ModelProfileRecord,
    artifacts: ModelArtifactRegistry | None = None,
    *,
    catalog: ArgumentCatalog,
    recommendations: Sequence[Recommendation] = (),
    executables: Mapping[str, Path] | None = None,
) -> dict[str, object]:
    source = artifacts.source_for_profile(profile) if artifacts is not None else None
    executable = (executables or {}).get(profile.runtime_id)
    validation_state = "available"
    download: dict[str, object] | None = None
    if artifacts is not None:
        matches = artifacts.matching_jobs(profile)
        validation_state = (
            "available"
            if not matches and Path(profile.model_path).is_file()
            else "broken"
            if not matches
            else max(
                (artifacts.validation_state(job) for job in matches),
                key=("broken", "unverified", "available").index,
            )
        )
        active = next(
            (job for job in matches if DownloadState(job.state) in _ACTIVE_DOWNLOAD_STATES), None
        )
        if validation_state == "broken" and active is not None:
            # Not broken: the model is on its way. The router still treats it as unavailable.
            validation_state = "pending"
            download = {
                "id": active.id,
                "state": active.state,
                "completed_bytes": active.completed_bytes,
                "total_bytes": active.total_bytes,
            }
    return {
        "id": profile.id,
        "alias": profile.alias,
        "runtime_id": profile.runtime_id,
        "model_path": profile.model_path,
        "configuration": configuration_payload(profile, catalog),
        "preset": redact_preset(profile.preset, catalog),
        "enabled": profile.enabled,
        "exclusive": profile.exclusive,
        "description": profile.description,
        "version": profile.version,
        # Equal for profiles that launch the same llama-server command under any alias.
        "hash": profile_hash(profile, catalog, executable) if executable is not None else None,
        "recommendation": recommendation_status(profile, recommendations, catalog),
        "benchmark_results": profile.benchmark_results or {},
        "validation_state": validation_state,
        "auto_enable": profile.enable_on_download is not None,
        "download": download,
        "source_download": (
            {
                "id": source.download_id,
                "repo_id": source.repo_id,
                "revision": source.revision,
                "group_key": source.group_key,
                "file_count": source.file_count,
                "total_bytes": source.total_bytes,
            }
            if source is not None
            else None
        ),
    }


def _download_payload(job: DownloadJobRecord) -> dict[str, object]:
    return {
        "id": job.id,
        "repo_id": job.repo_id,
        "revision": job.revision,
        "group_key": job.group_key,
        "files": job.files,
        "destination": job.destination,
        "total_bytes": job.total_bytes,
        "completed_bytes": job.completed_bytes,
        "state": job.state,
        "error": job.error,
    }


def _server_payload(
    supervisor: RouterSupervisor,
    settings: Settings,
    memory: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "state": supervisor.state,
        "pid": supervisor.pid,
        "last_exit_code": supervisor.last_exit_code,
        "endpoint": f"http://{settings.router_host}:{settings.router_port}",
        "logs": supervisor.logs,
        "timing": supervisor.timing,
        "memory": memory or [],
        "context": supervisor.context,
        "arguments": supervisor.launch_arguments,
        "system": _system_metrics(settings.data_dir),
    }


def _next_version(
    current: ModelProfileRecord,
    profile: ModelProfile,
    profile_request: ProfileCreateRequest,
    catalog: ArgumentCatalog,
) -> int | None:
    """The version to save, or ``None`` to keep the current one.

    A recommended profile keeps the version of its recommendation. Any other profile
    takes a version typed in by hand, or moves up one when its settings change.
    """
    if current.recommendation_id is not None:
        return None
    if profile_request.version is not None and profile_request.version != current.version:
        return profile_request.version
    settings_changed = (
        profile.configuration.to_payload(catalog)["arguments"]
        != configuration_payload(current, catalog).get("arguments")
        or str(profile.model_path.resolve()) != current.model_path
        or profile_request.runtime_id != current.runtime_id
        or profile_request.exclusive != current.exclusive
    )
    return current.version + 1 if settings_changed else None


def _profile_description(value: str | None) -> str | None:
    """Trimmed description text; an empty one means no description."""
    if value is None:
        return None
    return value.strip() or None


def _exclusive_conflict(
    model: str, models: Sequence[RouterModel], exclusive: set[str]
) -> str | None:
    """Why loading ``model`` would put an exclusive profile beside another model."""
    occupied = [
        item.id
        for item in models
        if item.id != model and item.status.get("value") in {"loaded", "loading"}
    ]
    if not occupied:
        return None
    if model in exclusive:
        return f"{model} is exclusive: unload {', '.join(occupied)} first"
    blocker = next((item for item in occupied if item in exclusive), None)
    if blocker is not None:
        return f"{blocker} is exclusive: unload it before loading another model"
    return None


def _system_metrics(data_dir: Path) -> dict[str, object]:
    return collect_system_metrics(data_dir)


def _server_run_payload(run: ServerRunRecord) -> dict[str, object]:
    return {
        "id": run.id,
        "runtime_id": run.runtime_id,
        "endpoint": run.endpoint,
        "state": run.state,
        "pid": run.pid,
        "exit_code": run.exit_code,
        "error": run.error,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
    }


def _router_model_payload(model: RouterModel) -> dict[str, object]:
    return {
        "id": model.id,
        "path": model.path,
        "status": model.status,
        "metadata": model.metadata,
    }


def _access_token_payload(token: AccessTokenRecord) -> dict[str, object]:
    return {
        "id": token.id,
        "name": token.name,
        "last_four": token.last_four,
        "expiry_note": token.expiry_note,
        "enabled": token.enabled,
        "created_at": token.created_at,
        "revoked_at": token.revoked_at,
    }


def _router_model_event_sse(event: RouterModelEvent) -> str:
    payload = json.dumps(
        {"model": event.model, "event": event.event, "data": event.data},
        separators=(",", ":"),
    )
    return f"event: {event.event}\ndata: {payload}\n\n"


def _control_event_sse(event: ControlEvent) -> str:
    payload = json.dumps(event.data, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {payload}\n\n"


def _event_cursor(request: Request, after: int | None) -> int | None:
    header = request.headers.get("last-event-id")
    if header is None:
        return after
    try:
        header_cursor = int(header)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative integer",
        ) from error
    if header_cursor < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative integer",
        )
    if after is not None and after != header_cursor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="after and Last-Event-ID cursors must match",
        )
    return header_cursor


def _require_running_router(supervisor: RouterSupervisor) -> None:
    if supervisor.state not in {RouterState.READY, RouterState.DEGRADED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"router model operations are unavailable while {supervisor.state}",
        )


def _router_api_error(error: RouterAPIError) -> HTTPException:
    status_code = error.status_code if 400 <= error.status_code < 500 else 502
    return HTTPException(status_code=status_code, detail=str(error))


def _hub_error(error: HfHubHTTPError) -> HTTPException:
    response_status = error.response.status_code if error.response is not None else None
    status_code = (
        status.HTTP_404_NOT_FOUND if response_status == 404 else status.HTTP_502_BAD_GATEWAY
    )
    return HTTPException(status_code=status_code, detail="Hugging Face request failed")


def _auth_service(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth_service)


def _session_id(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def _require_session(request: Request) -> AuthenticatedUser:
    session_id = _session_id(request)
    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        return _auth_service(request).resolve_session(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
        ) from error


def slot_context(models: list[dict[str, object]], alias: str) -> tuple[int, int] | None:
    """The busiest slot's tokens in use for a model, and the context size it can grow to.

    A unified KV cache shares one size between all slots; otherwise each slot has its own.
    """
    model = next((item for item in models if item.get("model") in (alias, None)), None)
    if model is None:
        return None
    slots = cast(list[dict[str, object]], model.get("slots") or [])
    used = max((cast(int, slot.get("used") or 0) for slot in slots), default=0)
    shared = model.get("shared_capacity")
    sizes = [cast(int, slot["total"]) for slot in slots if isinstance(slot.get("total"), int)]
    size = shared if isinstance(shared, int) else max(sizes, default=0)
    return (used, size) if size else None


def _require_privilege(
    request: Request, user: AuthenticatedUser | None = None
) -> AuthenticatedUser:
    current_user = user or _require_session(request)
    privilege = privilege_for_request(request.url.path, request.method)
    if privilege is not None and privilege not in current_user.privileges:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient privileges",
        )
    return current_user


def _managed_role_payload(role: Any) -> dict[str, object]:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "protected": role.protected,
        "privileges": list(role.privileges),
        "user_count": role.user_count,
    }


def _managed_user_payload(user: Any) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "description": user.description,
        "role_id": user.role_id,
        "role": user.role_name,
        "default_credentials": user.default_credentials,
    }


def _session_cookie(session_id: str, secure: bool) -> str:
    attributes = [
        f"{SESSION_COOKIE_NAME}={session_id}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def _clear_session_cookie(secure: bool) -> str:
    attributes = [
        f"{SESSION_COOKIE_NAME}=",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def create_app(
    settings: Settings | None = None,
    *,
    runtime_prober: RuntimeProber = probe_runtime,
    catalog: Catalog | None = None,
    file_transfer: FileTransfer | None = None,
    router_supervisor: RouterSupervisor | None = None,
    router_port_probe: RouterPortProbe = probe_router_port,
    router_client: RouterClient | None = None,
    process_memory: ProcessMemorySampler | None = None,
    static_dir: Path | None = None,
    argument_catalog: ArgumentCatalog | None = None,
    hardware_detector: Callable[[], HardwareProfile] = detect_hardware,
    recommendations: tuple[Recommendation, ...] | None = None,
    benchmarks: tuple[Benchmark, ...] | None = None,
    system_prompts: tuple[SystemPrompt, ...] | None = None,
    benchmark_linter: BenchmarkLinter | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    packaged_static_dir = static_dir or Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        LOGGER.info(
            "application_starting",
            extra={
                "component": "application",
                "event": "startup",
                "host": app_settings.host,
                "port": app_settings.port,
                "hf_token_configured": app_settings.hf_token is not None,
            },
        )
        # Load and validate the bundled catalog before touching any state, so an invalid
        # catalog stops startup instead of surfacing later as broken profile editing.
        app.state.argument_catalog = argument_catalog or default_catalog()
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        backup_database(app_settings.database_path, app_settings.database_backup_dir)
        upgrade_database(app_settings.database_path)
        engine = create_database_engine(app_settings.database_path)
        app.state.runtime_registry = RuntimeRegistry(engine, prober=runtime_prober)
        app.state.release_client = GitHubReleaseClient()
        app.state.runtime_installer = RuntimeInstaller(
            app_settings.data_dir,
            prober=runtime_prober,
            releases=app.state.release_client,
        )
        app.state.profile_registry = ProfileRegistry(engine, app.state.argument_catalog)
        migration = app.state.profile_registry.migrate_legacy_configurations()
        if migration.migrated:
            LOGGER.info(
                "profile_configurations_migrated",
                extra={
                    "component": "profiles",
                    "event": "migrated",
                    "count": migration.migrated,
                    "preset_mismatches": len(migration.preset_mismatches),
                },
            )
        # Saved presets are what the router launches; bring them in line with this catalog.
        app.state.profile_registry.refresh_presets()
        app.state.token_registry = TokenRegistry(engine, app_settings.data_dir)
        app.state.token_registry.reconcile_legacy_materialized_tokens()
        app.state.token_registry.remove_materialized_tokens()
        app.state.auth_service = AuthService(engine)
        app.state.auth_service.ensure_default_admin()
        app.state.login_throttle = LoginThrottle(
            failure_threshold=app_settings.login_failure_threshold,
            lockout_seconds=app_settings.login_lockout_seconds,
            backoff_base_seconds=app_settings.login_backoff_base_seconds,
            backoff_max_seconds=app_settings.login_backoff_max_seconds,
        )
        app.state.download_registry = DownloadRegistry(engine, app_settings.data_dir / "models")
        app.state.model_library = ModelLibrary(
            app.state.download_registry, app_settings.data_dir / "models"
        )
        app.state.logical_model_registry = LogicalModelRegistry(engine)
        app.state.model_artifact_registry = ModelArtifactRegistry(
            app.state.download_registry, app_settings.data_dir / "models"
        )
        app.state.event_broker = EventBroker(app_settings.event_history_capacity)
        token = app_settings.hf_token.get_secret_value() if app_settings.hf_token else None
        app.state.huggingface_catalog = catalog or HuggingFaceCatalog(token)
        transfer = file_transfer or HuggingFaceFileTransfer(token)

        def enable_ready(download_id: str) -> object:
            return app.state.profile_registry.enable_ready(
                download_id,
                lambda record: _unfinished_download(
                    record,
                    app.state.argument_catalog,
                    app.state.download_registry,
                    app.state.model_artifact_registry,
                ),
            )

        app.state.download_coordinator = DownloadCoordinator(
            app.state.download_registry,
            DownloadWorker(
                app.state.download_registry,
                transfer,
                stall_timeout_seconds=app_settings.download_stall_timeout_seconds,
            ),
            event_broker=app.state.event_broker,
            on_completed=enable_ready,
        )
        app.state.download_coordinator.start_pending()
        # Catch up on profiles whose download finished while the app was not watching.
        for download_id in app.state.profile_registry.awaiting_downloads():
            with suppress(DownloadJobNotFoundError):
                job = app.state.download_registry.get(download_id)
                if DownloadState(job.state) is DownloadState.COMPLETED:
                    enable_ready(download_id)
        app.state.hardware_detector = hardware_detector
        app.state.recommendations = (
            recommendations if recommendations is not None else load_recommendations()
        )
        linked = link_unchanged_profiles(
            app.state.recommendations, app.state.argument_catalog, app.state.profile_registry
        )
        if linked:
            LOGGER.info(
                "recommended_profiles_linked",
                extra={"component": "profiles", "event": "linked", "aliases": list(linked)},
            )
        app.state.router_supervisor = router_supervisor or RouterSupervisor(
            restart_policy=RouterRestartPolicy(
                max_attempts=app_settings.router_restart_max_attempts,
                window_seconds=app_settings.router_restart_window_seconds,
                delay_seconds=app_settings.router_restart_delay_seconds,
                ready_timeout_seconds=app_settings.router_ready_timeout_seconds,
            )
        )
        app.state.process_memory = process_memory or ProcessMemorySampler()
        app.state.router_client = router_client or HttpRouterClient(
            app_settings.router_host,
            app_settings.router_port,
            api_key_provider=app.state.token_registry.control_token,
        )
        app.state.router_event_synchronizer = RouterEventSynchronizer(
            app.state.router_client, app.state.event_broker
        )
        app.state.server_run_registry = ServerRunRegistry(engine)
        app.state.diagnostics_exporter = DiagnosticsExporter(
            app_settings, engine, app.state.argument_catalog
        )
        app.state.active_server_run_id = None
        app.state.router_lifecycle_lock = asyncio.Lock()
        app.state.benchmarks = benchmarks if benchmarks is not None else load_benchmarks()
        app.state.system_prompts = (
            system_prompts if system_prompts is not None else load_system_prompts()
        )
        app.state.benchmark_runs = BenchmarkRunRegistry(
            engine, app_settings.data_dir / "benchmark-runs"
        )
        app.state.benchmark_runs.fail_interrupted()
        app.state.benchmark_runs.prune_folders()

        async def start_router_for_benchmark(runtime_id: str) -> None:
            async with app.state.router_lifecycle_lock:
                await start_router(runtime_id, app)

        async def stop_router_after_benchmark() -> None:
            async with app.state.router_lifecycle_lock:
                await app.state.router_supervisor.stop()
                app.state.token_registry.remove_materialized_tokens()

        app.state.benchmark_linter = benchmark_linter or BenchmarkLinter(app_settings.data_dir)
        app.state.benchmark_runner = BenchmarkRunner(
            app.state.benchmark_runs,
            cast(BenchmarkClient, app.state.router_client),
            start_router=start_router_for_benchmark,
            stop_router=stop_router_after_benchmark,
            router_stopped=lambda: app.state.router_supervisor.state
            in {RouterState.STOPPED, RouterState.CRASHED},
            linter=app.state.benchmark_linter,
            context=lambda alias: slot_context(
                cast(RouterSupervisor, app.state.router_supervisor).context, alias
            ),
        )

        def record_router_state(
            state: RouterState, pid: int | None, exit_code: int | None
        ) -> None:
            LOGGER.info(
                "router_state_changed",
                extra={
                    "component": "router",
                    "event": "state_changed",
                    "state": state.value,
                    "pid": pid,
                    "exit_code": exit_code,
                },
            )
            run_id = cast(str | None, app.state.active_server_run_id)
            if run_id is not None:
                previous_run = app.state.server_run_registry.get(run_id)
                if state is RouterState.STARTING and previous_run.state in {
                    RouterState.STOPPED,
                    RouterState.CRASHED,
                }:
                    retry = app.state.server_run_registry.create(
                        previous_run.runtime_id, previous_run.endpoint
                    )
                    app.state.active_server_run_id = retry.id
                    run_id = retry.id
                app.state.server_run_registry.update(
                    run_id, state, pid=pid, exit_code=exit_code
                )
            app.state.event_broker.publish(
                "router.state",
                {"state": state, "pid": pid, "exit_code": exit_code},
            )
            if state is RouterState.READY:
                app.state.router_event_synchronizer.start()
            elif state is not RouterState.DEGRADED:
                app.state.router_event_synchronizer.deactivate()

        app.state.router_supervisor.set_state_observer(record_router_state)
        LOGGER.info(
            "application_started",
            extra={"component": "application", "event": "started"},
        )
        try:
            yield
        finally:
            LOGGER.info(
                "application_stopping",
                extra={"component": "application", "event": "shutdown"},
            )
            runner = cast(BenchmarkRunner, app.state.benchmark_runner)
            if runner.active_run_id is not None:
                await runner.cancel(runner.active_run_id)
            async with app.state.router_lifecycle_lock:
                await app.state.router_supervisor.stop()
            app.state.token_registry.remove_materialized_tokens()
            app.state.router_supervisor.set_state_observer(None)
            await app.state.router_event_synchronizer.shutdown()
            await app.state.download_coordinator.shutdown()
            engine.dispose()
            LOGGER.info(
                "application_stopped",
                extra={"component": "application", "event": "stopped"},
            )

    app = FastAPI(title="LlamaWebUI", version=__version__, lifespan=lifespan)

    PUBLIC_API_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout"}
    BOOTSTRAP_ALLOWED_PATHS = {
        "/api/auth/me",
        "/api/auth/password",
        "/api/auth/logout",
    }

    @app.middleware("http")
    async def require_control_plane_auth(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API_PATHS:
            session_id = _session_id(request)
            if session_id is None:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "authentication required"},
                )
            try:
                user = _auth_service(request).resolve_session(session_id)
            except SessionNotFoundError as error:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": str(error)},
                )
            if user.default_credentials and path not in BOOTSTRAP_ALLOWED_PATHS:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "detail": "change the bootstrap password before using the control plane"
                    },
                )
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE
            ):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "missing CSRF protection header"},
                )
            privilege = privilege_for_request(path, request.method)
            if privilege is not None and privilege not in user.privileges:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "insufficient privileges"},
                )
        return await call_next(request)

    def _generated_preset_path() -> Path:
        return app_settings.data_dir / "generated" / "llama-models.ini"

    def _router_preview(request: Request, runtime: RuntimeRecord) -> list[str]:
        """The router-level arguments LlamaWebUI adds when it launches llama-server."""
        tokens = cast(TokenRegistry, request.app.state.token_registry)
        arguments = [
            runtime.executable_path,
            "--models-preset",
            str(_generated_preset_path()),
            "--host",
            app_settings.router_host,
            "--port",
            str(app_settings.router_port),
        ]
        if tokens.has_enabled():
            arguments.extend(("--api-key-file", str(tokens.key_file)))
        return arguments

    async def start_router(runtime_id: str, owner: FastAPI) -> dict[str, object]:
        runtimes = cast(RuntimeRegistry, owner.state.runtime_registry)
        profiles = cast(ProfileRegistry, owner.state.profile_registry)
        tokens = cast(TokenRegistry, owner.state.token_registry)
        supervisor = cast(RouterSupervisor, owner.state.router_supervisor)
        run_registry = cast(ServerRunRegistry, owner.state.server_run_registry)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"router cannot start while {supervisor.state}",
            )
        try:
            runtime = runtimes.get(runtime_id)
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        router_diagnostics = _router_diagnostics(runtime)
        if router_diagnostics:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="; ".join(router_diagnostics),
            )
        enabled_profiles = profiles.list_enabled(runtime.id)
        if not enabled_profiles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="runtime has no enabled model profiles",
            )
        artifacts = cast(ModelArtifactRegistry, owner.state.model_artifact_registry)
        broken = tuple(
            profile.alias
            for profile in enabled_profiles
            if not artifacts.profile_available(profile)
        )
        if broken:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"enabled model profiles are broken: {', '.join(broken)}",
            )
        catalog = cast(ArgumentCatalog, owner.state.argument_catalog)
        capabilities = runtime_capabilities(runtime)
        launch_profiles = tuple(
            profile_from_record(profile, catalog) for profile in enabled_profiles
        )
        launch_issues = {
            profile.alias: validate_profile(profile, catalog, capabilities)
            for profile in launch_profiles
        }
        blockers = [
            f"{alias}: {message}"
            for alias, issues in launch_issues.items()
            for message in errors_of(issues)
        ]
        if blockers:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="enabled model profiles cannot launch: " + "; ".join(blockers),
            )

        preset_path = _generated_preset_path()
        write_combined_preset_atomic(
            preset_path, tuple(profile.preset for profile in enabled_profiles)
        )
        tokens.materialize_enabled_tokens()
        launch = RouterLaunch(
            executable=Path(runtime.executable_path),
            preset_path=preset_path,
            host=app_settings.router_host,
            port=app_settings.router_port,
            api_key_file=tokens.key_file if tokens.has_enabled() else None,
        )
        supervisor.record_log("llama-server router command: " + " ".join(launch.arguments()))
        for launch_profile in launch_profiles:
            supervisor.record_log(
                "llama-server model command (preset equivalent): "
                + render_command(launch_profile, catalog, Path(runtime.executable_path))
            )
            for warning in warnings_of(launch_issues[launch_profile.alias]):
                supervisor.record_log(f"profile {launch_profile.alias} warning: {warning}")
        endpoint = f"http://{app_settings.router_host}:{app_settings.router_port}"
        run = run_registry.create(runtime.id, endpoint)
        owner.state.active_server_run_id = run.id
        if not await router_port_probe(app_settings.router_host, app_settings.router_port):
            detail = (
                "router port is already in use: "
                f"{app_settings.router_host}:{app_settings.router_port}"
            )
            run_registry.update(
                run.id,
                RouterState.CRASHED,
                pid=None,
                exit_code=None,
                error=detail,
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

        try:
            await supervisor.start(launch)
            await supervisor.wait_until_ready(
                launch, timeout_seconds=app_settings.router_ready_timeout_seconds
            )
        except TimeoutError as error:
            tokens.remove_materialized_tokens()
            run_registry.update(
                run.id,
                supervisor.state,
                pid=supervisor.pid,
                exit_code=supervisor.last_exit_code,
                error=str(error),
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(error)
            ) from error
        except (OSError, RuntimeError) as error:
            tokens.remove_materialized_tokens()
            run_registry.update(
                run.id,
                supervisor.state,
                pid=supervisor.pid,
                exit_code=supervisor.last_exit_code,
                error=str(error),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        except ValueError as error:
            tokens.remove_materialized_tokens()
            run_registry.update(
                run.id,
                supervisor.state,
                pid=supervisor.pid,
                exit_code=supervisor.last_exit_code,
                error=str(error),
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _server_payload(supervisor, app_settings)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
        }

    @app.post("/api/auth/login")
    async def login(login_request: LoginRequest, request: Request) -> JSONResponse:
        auth = _auth_service(request)
        throttle = cast(LoginThrottle, request.app.state.login_throttle)
        client_ip = request.client.host if request.client is not None else "unknown"
        try:
            attempt = throttle.begin(login_request.username, client_ip)
        except LoginRateLimitedError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(error),
                headers={"Retry-After": str(error.retry_after)},
            ) from error
        try:
            if attempt.delay_seconds:
                await asyncio.sleep(attempt.delay_seconds)
            user = auth.authenticate(login_request.username, login_request.password)
        except AuthenticationError as error:
            throttle.failure(attempt)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error
        except BaseException:
            throttle.abort(attempt)
            raise
        throttle.success(attempt)
        session = auth.create_session(user.id)
        authenticated = auth.resolve_session(session.id)
        secure = request.url.scheme == "https"
        response = JSONResponse(
            {
                "username": user.username,
                "default_credentials": auth.is_default_credentials(user.id),
                "description": authenticated.description,
                "role": authenticated.role_name,
                "privileges": sorted(authenticated.privileges),
            }
        )
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.id,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        session_id = _session_id(request)
        if session_id is not None:
            with suppress(SessionNotFoundError):
                _auth_service(request).revoke_session(session_id)
        secure = request.url.scheme == "https"
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/auth/me")
    async def current_user(request: Request) -> dict[str, object]:
        user = _require_session(request)
        return {
            "username": user.username,
            "default_credentials": user.default_credentials,
            "description": user.description,
            "role": user.role_name,
            "privileges": sorted(user.privileges),
        }

    @app.post("/api/auth/password")
    async def change_password(
        password_request: PasswordChangeRequest, request: Request
    ) -> dict[str, object]:
        user = _require_session(request)
        auth = _auth_service(request)
        session_id = _session_id(request)
        try:
            auth.change_password(
                user.id,
                password_request.current_password,
                password_request.new_password,
                keep_session_id=session_id,
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error
        return {"changed": True}

    @app.get("/api/auth/users")
    async def list_users(request: Request) -> list[dict[str, object]]:
        return [_managed_user_payload(user) for user in _auth_service(request).list_managed_users()]

    @app.post("/api/auth/users", status_code=status.HTTP_201_CREATED)
    async def create_user(
        user_request: UserCreateRequest, request: Request
    ) -> dict[str, object]:
        try:
            user = _auth_service(request).create_managed_user(
                user_request.username,
                user_request.password,
                description=user_request.description,
                role_id=user_request.role_id,
            )
        except UserAlreadyExistsError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except RoleNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return _managed_user_payload(user)

    @app.get("/api/auth/roles")
    async def list_roles(request: Request) -> list[dict[str, object]]:
        return [_managed_role_payload(role) for role in _auth_service(request).list_roles()]

    @app.post("/api/auth/roles", status_code=status.HTTP_201_CREATED)
    async def create_role(role_request: RoleCreateRequest, request: Request) -> dict[str, object]:
        try:
            role = _auth_service(request).create_role(
                role_request.name, role_request.description, role_request.privileges
            )
        except RoleAlreadyExistsError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except InvalidPrivilegesError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return _managed_role_payload(role)

    @app.put("/api/auth/roles/{role_id}")
    async def update_role(
        role_id: str, role_request: RoleCreateRequest, request: Request
    ) -> dict[str, object]:
        try:
            role = _auth_service(request).update_role(
                role_id, role_request.name, role_request.description, role_request.privileges
            )
        except RoleNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except RoleProtectedError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except (RoleAlreadyExistsError, LastAdministratorError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except InvalidPrivilegesError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return _managed_role_payload(role)

    @app.delete("/api/auth/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_role(role_id: str, request: Request) -> None:
        try:
            _auth_service(request).delete_role(role_id)
        except RoleNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except RoleProtectedError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except RoleInUseError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.put("/api/auth/users/{user_id}")
    async def update_user(
        user_id: str, user_request: UserUpdateRequest, request: Request
    ) -> dict[str, object]:
        try:
            user = _auth_service(request).update_user(
                user_id, description=user_request.description, role_id=user_request.role_id
            )
        except AuthenticationError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except RoleNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except LastAdministratorError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _managed_user_payload(user)

    @app.delete("/api/auth/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_user(user_id: str, request: Request) -> None:
        current_user = _require_session(request)
        try:
            _auth_service(request).delete_user(
                user_id, requesting_user_id=current_user.id
            )
        except AuthenticationError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProtectedUserError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except LastAdministratorError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/diagnostics/export")
    async def export_diagnostics(request: Request) -> dict[str, object]:
        exporter = cast(DiagnosticsExporter, request.app.state.diagnostics_exporter)
        path = exporter.export(
            runtimes=cast(RuntimeRegistry, request.app.state.runtime_registry),
            profiles=cast(ProfileRegistry, request.app.state.profile_registry),
            artifacts=cast(ModelArtifactRegistry, request.app.state.model_artifact_registry),
            supervisor=cast(RouterSupervisor, request.app.state.router_supervisor),
            server_runs=cast(ServerRunRegistry, request.app.state.server_run_registry),
            downloads=cast(DownloadRegistry, request.app.state.download_registry),
        )
        LOGGER.info(
            "diagnostics_exported",
            extra={"component": "diagnostics", "event": "exported"},
        )
        return {"path": str(path), "format": 1}

    @app.get("/api/events")
    async def stream_events(
        request: Request,
        after: int | None = Query(default=None, ge=0),
    ) -> StreamingResponse:
        broker = cast(EventBroker, request.app.state.event_broker)
        cursor = _event_cursor(request, after)
        try:
            subscription = broker.subscribe(cursor)
        except EventCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": str(error),
                    "reconcile": "/api/server/status",
                    "oldest_event_id": error.oldest,
                    "latest_event_id": error.latest,
                },
            ) from error

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for event in subscription:
                    yield _control_event_sse(event)
            except EventCursorError as error:
                payload = json.dumps(
                    {
                        "message": str(error),
                        "reconcile": "/api/server/status",
                        "oldest_event_id": error.oldest,
                        "latest_event_id": error.latest,
                    },
                    separators=(",", ":"),
                )
                yield f"event: reconcile\ndata: {payload}\n\n"
            finally:
                subscription.close()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/server/status")
    async def server_status(request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        sampler = cast(ProcessMemorySampler, request.app.state.process_memory)
        memory = await asyncio.to_thread(sampler.sample, supervisor.pid)
        return _server_payload(supervisor, app_settings, memory)

    @app.get("/api/server/runs")
    async def list_server_runs(request: Request) -> list[dict[str, object]]:
        registry = cast(ServerRunRegistry, request.app.state.server_run_registry)
        return [_server_run_payload(run) for run in registry.list()]

    @app.post("/api/server/start")
    async def start_server(
        start_request: ServerStartRequest, request: Request
    ) -> dict[str, object]:
        lock = cast(asyncio.Lock, request.app.state.router_lifecycle_lock)
        async with lock:
            return await start_router(start_request.runtime_id, request.app)

    @app.post("/api/server/stop")
    async def stop_server(request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        tokens = cast(TokenRegistry, request.app.state.token_registry)
        lock = cast(asyncio.Lock, request.app.state.router_lifecycle_lock)
        async with lock:
            try:
                await supervisor.stop()
            except (RuntimeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from error
            tokens.remove_materialized_tokens()
        return _server_payload(supervisor, app_settings)

    @app.post("/api/server/restart")
    async def restart_server(
        request: Request, restart_request: ServerRestartRequest | None = None
    ) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        run_registry = cast(ServerRunRegistry, request.app.state.server_run_registry)
        lock = cast(asyncio.Lock, request.app.state.router_lifecycle_lock)
        async with lock:
            run_id = cast(str | None, request.app.state.active_server_run_id)
            if run_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="router has no previous runtime selection",
                )
            runtime_id = restart_request.runtime_id if restart_request else None
            runtime_id = runtime_id or run_registry.get(run_id).runtime_id
            if runtime_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="previous router runtime is no longer registered",
                )
            try:
                await supervisor.stop()
            except (RuntimeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from error
            return await start_router(runtime_id, request.app)

    @app.post("/api/server/rollback")
    async def rollback_server(request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        run_registry = cast(ServerRunRegistry, request.app.state.server_run_registry)
        lock = cast(asyncio.Lock, request.app.state.router_lifecycle_lock)
        async with lock:
            if supervisor.state not in {RouterState.READY, RouterState.DEGRADED}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="router must be running before rollback",
                )
            run_id = cast(str | None, request.app.state.active_server_run_id)
            if run_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="router has no active runtime selection",
                )
            current_runtime_id = run_registry.get(run_id).runtime_id
            previous_runtime_id = run_registry.previous_runtime_id(run_id)
            if not current_runtime_id or not previous_runtime_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="no previous runtime is available for rollback",
                )
            runtimes = cast(RuntimeRegistry, request.app.state.runtime_registry)
            profiles = cast(ProfileRegistry, request.app.state.profile_registry)
            artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
            try:
                candidate = runtimes.get(previous_runtime_id)
            except RuntimeNotFoundError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from error
            router_diagnostics = _router_diagnostics(candidate)
            if router_diagnostics:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="; ".join(router_diagnostics),
                )
            candidate_profiles = profiles.list_enabled(previous_runtime_id)
            broken = tuple(
                profile.alias
                for profile in candidate_profiles
                if not artifacts.profile_available(profile)
            )
            if not candidate_profiles:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="previous runtime has no enabled model profiles",
                )
            if broken:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"previous runtime profiles are broken: {', '.join(broken)}",
                )
            try:
                await supervisor.stop()
                return await start_router(previous_runtime_id, request.app)
            except HTTPException as error:
                try:
                    await start_router(current_runtime_id, request.app)
                except HTTPException as restore_error:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=(
                            f"rollback failed: {error.detail}; "
                            f"restoring current runtime failed: {restore_error.detail}"
                        ),
                    ) from restore_error
                raise error

    @app.get("/api/server/models")
    async def list_router_models(
        request: Request, reload: bool = False
    ) -> list[dict[str, object]]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        client = cast(RouterClient, request.app.state.router_client)
        _require_running_router(supervisor)
        try:
            models = await client.list_models(reload=reload)
        except RouterAPIError as error:
            raise _router_api_error(error) from error
        return [_router_model_payload(model) for model in models]

    @app.get("/api/server/models/events")
    async def stream_router_model_events(request: Request) -> StreamingResponse:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        client = cast(RouterClient, request.app.state.router_client)
        _require_running_router(supervisor)

        return StreamingResponse(
            stream_model_events(client, keepalive_seconds=MODEL_EVENT_KEEPALIVE_SECONDS),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/server/models/load")
    async def load_router_model(
        model_request: RouterModelRequest, request: Request
    ) -> dict[str, bool]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        client = cast(RouterClient, request.app.state.router_client)
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        _require_running_router(supervisor)
        try:
            conflict = _exclusive_conflict(
                model_request.model,
                await client.list_models(),
                {profile.alias for profile in registry.list() if profile.exclusive},
            )
            if conflict is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)
            await client.load_model(model_request.model)
        except RouterAPIError as error:
            raise _router_api_error(error) from error
        return {"success": True}

    @app.post("/api/server/models/unload")
    async def unload_router_model(
        model_request: RouterModelRequest, request: Request
    ) -> dict[str, bool]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        client = cast(RouterClient, request.app.state.router_client)
        _require_running_router(supervisor)
        try:
            await client.unload_model(model_request.model)
        except RouterAPIError as error:
            raise _router_api_error(error) from error
        return {"success": True}

    def benchmark_run_payload(
        runner: BenchmarkRunner, record: BenchmarkRunRecord, *, detail: bool
    ) -> dict[str, object]:
        progress = runner.progress(record.id)
        payload: dict[str, object] = {
            "id": record.id,
            "profile_id": record.profile_id,
            "profile_alias": record.profile_alias,
            "profile_version": record.profile_version,
            "benchmark_id": record.benchmark_id,
            "benchmark_title": record.benchmark_title,
            "max_tokens": record.max_tokens,
            "tools": record.tools,
            "tool_names": record.tool_names,
            "system_prompt_id": record.system_prompt_id,
            "reasoning_level": record.reasoning_level,
            "typescript_strict": record.typescript_strict,
            "verbose_log": record.verbose_log,
            "state": record.state,
            "stage": progress["stage"] if progress else record.stage,
            "error": record.error,
            "stats": dict(progress["stats"]) if progress else record.stats,  # type: ignore[call-overload]
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        }
        if progress:
            payload["live"] = {
                "prefill": progress.get("prefill"),
                "decode": progress.get("decode"),
                # The tool call being written: its name and bytes of arguments so far.
                "tool_call": progress.get("tool_call"),
                # Tokens in the model's context now, and its size.
                "context": progress.get("context"),
                # Lets open timeline steps show how long they have been going.
                "elapsed": runner.generation_elapsed(record.id),
            }
        if detail:
            payload["prompt"] = record.prompt
            payload["system_prompt"] = record.system_prompt
            payload["samples"] = list(progress["samples"]) if progress else record.samples  # type: ignore[call-overload]
            payload["output"] = progress["output"] if progress else record.output
            payload["reasoning"] = progress["reasoning"] if progress else record.reasoning
            payload["tool_calls"] = (
                list(cast(list[dict[str, object]], progress["tool_calls"]))
                if progress
                else record.tool_calls
            )
            payload["tasks"] = (
                [dict(task) for task in cast(list[dict[str, object]], progress["tasks"])]
                if progress
                else record.tasks
            )
            payload["timeline"] = (
                [dict(entry) for entry in cast(list[dict[str, object]], progress["timeline"])]
                if progress
                else record.timeline
            )
            runs = cast(BenchmarkRunRegistry, app.state.benchmark_runs)
            payload["artifacts"] = runs.workspace(record.id).files()
        return payload

    @app.get("/api/settings/benchmark-tools")
    async def benchmark_tools(request: Request) -> dict[str, object]:
        linter = cast(BenchmarkLinter, request.app.state.benchmark_linter)
        return {"eslint": linter.status()}

    @app.post("/api/settings/benchmark-tools/eslint", status_code=status.HTTP_202_ACCEPTED)
    async def install_eslint(request: Request) -> dict[str, object]:
        linter = cast(BenchmarkLinter, request.app.state.benchmark_linter)
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        if linter.installed and runner.active_run_id is not None:
            # Updating replaces the install, which the running benchmark may be using.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="wait for the running benchmark to finish before updating ESLint",
            )
        try:
            linter.start_install()
        except LinterError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return {"eslint": linter.status()}

    @app.delete("/api/settings/benchmark-tools/eslint")
    async def remove_eslint(request: Request) -> dict[str, object]:
        linter = cast(BenchmarkLinter, request.app.state.benchmark_linter)
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        if runner.active_run_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="wait for the running benchmark to finish before removing ESLint",
            )
        try:
            linter.remove()
        except LinterError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return {"eslint": linter.status()}

    @app.get("/api/benchmarks")
    async def list_benchmarks(request: Request) -> dict[str, object]:
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        benchmarks = cast(tuple[Benchmark, ...], request.app.state.benchmarks)
        system_prompts = cast(tuple[SystemPrompt, ...], request.app.state.system_prompts)
        available = runner.available_tools()
        return {
            "benchmarks": [item.to_payload() for item in benchmarks],
            "system_prompts": [item.to_payload() for item in system_prompts],
            # Runs default to the coding agent framing a client like OpenCode sends.
            "default_system_prompt_id": next(
                (item.id for item in system_prompts if item.id == DEFAULT_SYSTEM_PROMPT_ID),
                None,
            ),
            "default_max_tokens": DEFAULT_MAX_TOKENS,
            "reasoning_levels": list(REASONING_LEVELS),
            "tools": [
                {
                    "name": name,
                    "summary": TOOL_SUMMARIES[name],
                    "available": name in available,
                }
                for name in TOOL_NAMES
            ],
            "active_run_id": runner.active_run_id,
        }

    @app.get("/api/benchmarks/runs")
    async def list_benchmark_runs(
        request: Request, profile_id: str | None = None
    ) -> list[dict[str, object]]:
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        runs = cast(BenchmarkRunRegistry, request.app.state.benchmark_runs)
        return [
            benchmark_run_payload(runner, record, detail=False)
            for record in runs.list(profile_id)
        ]

    @app.post("/api/benchmarks/runs", status_code=status.HTTP_201_CREATED)
    async def start_benchmark_run(
        run_request: BenchmarkRunRequest, request: Request
    ) -> dict[str, object]:
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        benchmark = next(
            (
                item
                for item in cast(tuple[Benchmark, ...], request.app.state.benchmarks)
                if item.id == run_request.benchmark_id
            ),
            None,
        )
        if benchmark is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="benchmark not found")
        system_prompt = None
        if run_request.system_prompt_id is not None:
            system_prompt = next(
                (
                    item
                    for item in cast(tuple[SystemPrompt, ...], request.app.state.system_prompts)
                    if item.id == run_request.system_prompt_id
                ),
                None,
            )
            if system_prompt is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="system prompt not found"
                )
        try:
            profile = cast(ProfileRegistry, request.app.state.profile_registry).get(
                run_request.profile_id
            )
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        try:
            record = runner.start(
                profile,
                benchmark,
                run_request.max_tokens,
                tools=run_request.tools,
                tool_names=run_request.tool_names,
                system_prompt=system_prompt,
                reasoning_level=run_request.reasoning_level,
                verbose_log=run_request.verbose_log,
            )
        except BenchmarkBusyError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return benchmark_run_payload(runner, record, detail=True)

    @app.get("/api/benchmarks/runs/{run_id}")
    async def get_benchmark_run(run_id: str, request: Request) -> dict[str, object]:
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        runs = cast(BenchmarkRunRegistry, request.app.state.benchmark_runs)
        try:
            record = runs.get(run_id)
        except BenchmarkRunNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return benchmark_run_payload(runner, record, detail=True)

    @app.post("/api/benchmarks/runs/{run_id}/cancel")
    async def cancel_benchmark_run(run_id: str, request: Request) -> dict[str, object]:
        runner = cast(BenchmarkRunner, request.app.state.benchmark_runner)
        if not await runner.cancel(run_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="this benchmark is not running"
            )
        record = cast(BenchmarkRunRegistry, request.app.state.benchmark_runs).get(run_id)
        return benchmark_run_payload(runner, record, detail=False)

    @app.get("/api/benchmarks/runs/{run_id}/artifacts/{path:path}")
    async def read_benchmark_artifact(
        run_id: str, path: str, request: Request
    ) -> dict[str, object]:
        """A file the run's model wrote, as text in JSON.

        Never served as a page: generated HTML must not run with this app's session.
        """
        runs = cast(BenchmarkRunRegistry, request.app.state.benchmark_runs)
        try:
            runs.get(run_id)
            content = runs.workspace(run_id).read(path)
        except BenchmarkRunNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except WorkspaceError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return {"path": path, "content": content}

    @app.delete("/api/benchmarks/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_benchmark_run(run_id: str, request: Request) -> None:
        runs = cast(BenchmarkRunRegistry, request.app.state.benchmark_runs)
        try:
            runs.delete(run_id)
        except BenchmarkRunNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except BenchmarkBusyError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.get("/api/tokens")
    async def list_access_tokens(request: Request) -> list[dict[str, object]]:
        registry = cast(TokenRegistry, request.app.state.token_registry)
        return [_access_token_payload(token) for token in registry.list()]

    @app.post("/api/tokens", status_code=status.HTTP_201_CREATED)
    async def create_access_token(
        token_request: AccessTokenCreateRequest, request: Request
    ) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before changing access tokens",
            )
        registry = cast(TokenRegistry, request.app.state.token_registry)
        try:
            created = registry.create(token_request.name, token_request.expiry_note)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return {**_access_token_payload(created.record), "token": created.token}

    @app.delete("/api/tokens/{token_id}")
    async def revoke_access_token(token_id: str, request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before changing access tokens",
            )
        registry = cast(TokenRegistry, request.app.state.token_registry)
        try:
            return _access_token_payload(registry.revoke(token_id))
        except AccessTokenNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.get("/api/integrations/opencode")
    async def opencode_configuration(request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        client = cast(RouterClient, request.app.state.router_client)
        _require_running_router(supervisor)
        try:
            models = await client.list_models()
        except RouterAPIError as error:
            raise _router_api_error(error) from error
        host = app_settings.router_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        formatted_host = f"[{host}]" if ":" in host else host
        return {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "llama-web-ui": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Local llama.cpp",
                    "options": {
                        "baseURL": f"http://{formatted_host}:{app_settings.router_port}/v1",
                        "apiKey": "{env:LLAMA_WEB_UI_API_KEY}",
                    },
                    "models": {model.id: {"name": model.id} for model in models},
                }
            },
        }

    @app.get("/api/runtimes")
    async def list_runtimes(request: Request) -> list[dict[str, object]]:
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        return [_runtime_payload(runtime) for runtime in registry.list()]

    @app.get("/api/runtimes/releases/{tag}")
    async def get_runtime_release(tag: str, request: Request) -> dict[str, object]:
        client = cast(GitHubReleaseClient, request.app.state.release_client)
        try:
            release = await client.release(tag)
        except ReleaseInstallError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        return {
            "tag": release.tag,
            "stable_tag": release.stable_tag,
            "assets": [
                {"name": asset.name, "url": asset.url, "size": asset.size, "digest": asset.digest}
                for asset in release.assets
            ],
            "groups": [
                {
                    "key": group.key,
                    "primary": group.primary.name,
                    "companions": [asset.name for asset in group.companions],
                }
                for group in group_runtime_assets(release.assets)
            ],
        }

    @app.post("/api/runtimes/install", status_code=status.HTTP_201_CREATED)
    async def install_runtime(
        install_request: RuntimeInstallRequest, request: Request
    ) -> dict[str, object]:
        installer = cast(RuntimeInstaller, request.app.state.runtime_installer)
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        try:
            destination = await installer.install(
                tag=install_request.tag,
                asset_name=install_request.asset_name,
                backend=install_request.backend,
            )
            executable_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
            executable = next(destination.rglob(executable_name), None)
            if executable is None:
                raise ReleaseInstallError("installed runtime executable was not found")
            runtime = await registry.register(
                name=f"{install_request.tag} {install_request.backend or 'auto'}",
                executable_path=executable,
                backend=install_request.backend,
            )
        except ReleaseInstallError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        except (FileNotFoundError, RuntimeAlreadyRegisteredError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _runtime_payload(runtime)

    @app.get("/api/runtimes/{runtime_id}")
    async def get_runtime(runtime_id: str, request: Request) -> dict[str, object]:
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        try:
            return _runtime_payload(registry.get(runtime_id))
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.post("/api/runtimes", status_code=status.HTTP_201_CREATED)
    async def register_runtime(
        registration: RuntimeRegistrationRequest, request: Request
    ) -> dict[str, object]:
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        try:
            runtime = await registry.register(
                name=registration.name,
                executable_path=registration.executable_path,
                backend=registration.backend,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except RuntimeAlreadyRegisteredError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _runtime_payload(runtime)

    @app.post("/api/runtimes/{runtime_id}/probe")
    async def reprobe_runtime(runtime_id: str, request: Request) -> dict[str, object]:
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        try:
            return _runtime_payload(await registry.reprobe(runtime_id))
        except (FileNotFoundError, RuntimeNotFoundError) as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.delete("/api/runtimes/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_runtime(runtime_id: str, request: Request) -> None:
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        run_registry = cast(ServerRunRegistry, request.app.state.server_run_registry)
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        active_run_id = cast(str | None, request.app.state.active_server_run_id)
        active_runtime_id = None
        if active_run_id is not None:
            active_runtime_id = run_registry.get(active_run_id).runtime_id
        guarded_active_runtime = (
            active_runtime_id
            if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}
            else None
        )
        try:
            registry.remove(runtime_id, active_runtime_id=guarded_active_runtime)
        except RuntimeInUseError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.get("/api/argument-catalog")
    async def get_argument_catalog(request: Request) -> dict[str, object]:
        return _argument_catalog(request).to_payload()

    @app.get("/api/runtimes/{runtime_id}/argument-catalog")
    async def get_runtime_argument_catalog(
        runtime_id: str, request: Request
    ) -> dict[str, object]:
        runtime = _registered_runtime(request, runtime_id)
        return build_effective_catalog(
            _argument_catalog(request),
            runtime_capabilities(runtime),
            {
                "id": runtime.id,
                "name": runtime.name,
                "build": runtime.build,
                "helpSha256": runtime.help_sha256,
                "flagsSha256": runtime.flags_sha256,
            },
        )

    @app.get("/api/profiles/recommendations")
    async def list_recommendations(request: Request) -> dict[str, object]:
        detector = cast(Callable[[], HardwareProfile], request.app.state.hardware_detector)
        hardware = await asyncio.to_thread(detector)
        runtimes = [
            runtime
            for runtime in cast(RuntimeRegistry, request.app.state.runtime_registry).list()
            if _runtime_payload(runtime)["usable"]
        ]
        profiles = cast(ProfileRegistry, request.app.state.profile_registry).list()
        jobs = cast(DownloadRegistry, request.app.state.download_registry).list(include_hidden=True)
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        catalog = _argument_catalog(request)
        executables = _runtime_executables(request)
        items: list[dict[str, object]] = []
        for recommendation in cast(
            tuple[Recommendation, ...], request.app.state.recommendations
        ):
            assessment = assess(
                recommendation,
                cpu=hardware.cpu,
                memory_bytes=hardware.memory_bytes,
                discrete_vram_bytes=hardware.discrete_vram_bytes,
                gpus=hardware.gpus,
            )
            job = find_download(recommendation, jobs)
            draft_job = find_draft_download(
                recommendation, jobs, job.revision if job is not None else None
            )
            primary = _primary_path(artifacts, job)
            draft = _primary_path(artifacts, draft_job)
            # Per runtime, the profiles that launch exactly what this recommendation would.
            matches: dict[str, list[str]] = {}
            if primary is not None and (recommendation.model.draft_group is None or draft):
                wanted = recommended_profile(recommendation, catalog, primary, draft_path=draft)
                for runtime in runtimes:
                    same = identical_profiles(
                        wanted, Path(runtime.executable_path), profiles, catalog, executables
                    )
                    if same:
                        matches[runtime.id] = [profile.id for profile in same]
            items.append(
                {
                    **recommendation.to_payload(),
                    "assessment": {
                        "fit": assessment.fit,
                        "hardware_match": assessment.hardware_match,
                        "available_memory_gb": round(assessment.available_memory_gb, 1),
                    },
                    "compatible_runtime_ids": [
                        runtime.id
                        for runtime in runtimes
                        if (runtime.backend or "").lower() in recommendation.backends
                    ],
                    "download": _download_progress(job),
                    "draft_download": _download_progress(draft_job),
                    "matches": matches,
                    # Profiles added from this recommendation, under any alias.
                    "profile_ids": [
                        profile.id
                        for profile in profiles
                        if profile.recommendation_id == recommendation.id
                    ],
                    "suggested_alias": next_free_alias(
                        recommendation.alias, (profile.alias for profile in profiles)
                    ),
                }
            )
        return {"host": hardware.to_payload(), "recommendations": items}

    @app.post(
        "/api/profiles/recommendations/{recommendation_id}/apply",
        status_code=status.HTTP_201_CREATED,
    )
    async def apply_recommended_profile(
        recommendation_id: str, apply_request: RecommendationApplyRequest, request: Request
    ) -> dict[str, object]:
        # Applying also queues a model download, so it needs that privilege too.
        if "downloads.write" not in _require_session(request).privileges:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient privileges"
            )
        recommendation = next(
            (
                item
                for item in cast(tuple[Recommendation, ...], request.app.state.recommendations)
                if item.id == recommendation_id
            ),
            None,
        )
        if recommendation is None:
            raise HTTPException(status_code=404, detail="recommended profile not found")
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        catalog = _argument_catalog(request)
        try:
            applied = await apply_recommendation(
                recommendation,
                runtime_id=apply_request.runtime_id,
                hub=cast(Catalog, request.app.state.huggingface_catalog),
                catalog=catalog,
                profiles=cast(ProfileRegistry, request.app.state.profile_registry),
                downloads=cast(DownloadRegistry, request.app.state.download_registry),
                artifacts=artifacts,
                coordinator=cast(DownloadCoordinator, request.app.state.download_coordinator),
                executables=_runtime_executables(request),
                alias=apply_request.alias,
                add_profile=apply_request.add_profile,
            )
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ProfileAliasExistsError, RecommendationAlreadyAddedError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(error.errors)
            ) from error
        except (RecommendationError, DownloadPlanError, ArgumentValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except HfHubHTTPError as error:
            raise _hub_error(error) from error
        except CatalogUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error
        return {
            "profile": (
                _profile_payload(
                    applied.profile,
                    artifacts,
                    catalog=catalog,
                    recommendations=request.app.state.recommendations,
                    executables=_runtime_executables(request),
                )
                if applied.profile is not None
                else None
            ),
            "download": _download_payload(applied.download),
            "ready": applied.ready,
        }

    @app.get("/api/profiles")
    async def list_profiles(request: Request) -> list[dict[str, object]]:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        catalog = _argument_catalog(request)
        executables = _runtime_executables(request)
        return [
            _profile_payload(
                profile,
                artifacts,
                catalog=catalog,
                recommendations=request.app.state.recommendations,
                executables=executables,
            )
            for profile in registry.list()
        ]

    @app.post("/api/profiles/preview")
    async def preview_profile(
        profile_request: ProfileCreateRequest, request: Request
    ) -> dict[str, object]:
        """Validate an unsaved profile and show the exact launch vector it would produce."""
        catalog = _argument_catalog(request)
        runtime = _registered_runtime(request, profile_request.runtime_id)
        try:
            profile = profile_request.to_domain(catalog)
        except ProfileValidationError as error:
            issues = tuple(
                ArgumentIssue("error", "invalid-value", message) for message in error.errors
            )
            return {
                **_issues_payload(issues),
                "arguments": [],
                "command": "",
                "preset": "",
                "router_arguments": _router_preview(request, runtime),
            }
        issues = _profile_issues(request, profile, runtime)
        executable = Path(runtime.executable_path)
        return {
            **_issues_payload(issues),
            "arguments": list(launch_vector(profile, catalog, executable)),
            "command": render_command(profile, catalog, executable),
            "preset": render_preset_unchecked(profile, catalog, redact=True),
            "router_arguments": _router_preview(request, runtime),
        }

    @app.post("/api/profiles", status_code=status.HTTP_201_CREATED)
    async def create_profile(
        profile_request: ProfileCreateRequest, request: Request
    ) -> dict[str, object]:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        catalog = _argument_catalog(request)
        try:
            profile = profile_request.to_domain(catalog)
            record = registry.create(
                profile=profile,
                runtime_id=profile_request.runtime_id,
                enabled=profile_request.enabled,
                exclusive=profile_request.exclusive,
                description=profile_request.description,
                version=profile_request.version or 1,
            )
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(error.errors)
            ) from error
        return _saved_profile_payload(request, record, profile)

    @app.put("/api/profiles/{profile_id}")
    async def update_profile(
        profile_id: str, profile_request: ProfileCreateRequest, request: Request
    ) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before editing a profile",
            )
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        catalog = _argument_catalog(request)
        try:
            profile = profile_request.to_domain(catalog)
            current = registry.get(profile_id)
            record = registry.update(
                profile_id,
                profile=profile,
                runtime_id=profile_request.runtime_id,
                enabled=profile_request.enabled,
                exclusive=profile_request.exclusive,
                description=profile_request.description,
                version=_next_version(current, profile, profile_request, catalog),
            )
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(error.errors)
            ) from error
        return _saved_profile_payload(request, record, profile)

    @app.put("/api/profiles/{profile_id}/enabled")
    async def set_profile_enabled(
        profile_id: str, enabled_request: ProfileEnabledRequest, request: Request
    ) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before enabling or disabling a profile",
            )
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            record = registry.set_enabled(profile_id, enabled_request.enabled)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        return _profile_payload(
            record,
            artifacts,
            catalog=_argument_catalog(request),
            recommendations=request.app.state.recommendations,
            executables=_runtime_executables(request),
        )

    @app.post("/api/profiles/{profile_id}/upgrade")
    async def upgrade_profile(profile_id: str, request: Request) -> dict[str, object]:
        """Move an unchanged recommended profile to the latest bundled version."""
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before upgrading a profile",
            )
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        catalog = _argument_catalog(request)
        try:
            record = upgrade_recommended_profile(
                registry.get(profile_id), request.app.state.recommendations, catalog, registry
            )
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProfileUpgradeError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(error.errors)
            ) from error
        return _saved_profile_payload(request, record, profile_from_record(record, catalog))

    @app.post("/api/profiles/{profile_id}/clone", status_code=status.HTTP_201_CREATED)
    async def clone_profile(
        profile_id: str, clone_request: ProfileCloneRequest, request: Request
    ) -> dict[str, object]:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            profile = registry.clone(profile_id, clone_request.alias)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        return _profile_payload(
            profile,
            artifacts,
            catalog=_argument_catalog(request),
            executables=_runtime_executables(request),
        )

    @app.delete("/api/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_profile(profile_id: str, request: Request) -> None:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before deleting a model profile",
            )
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            registry.remove(profile_id)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.post("/api/profiles/{profile_id}/validate")
    async def validate_profile_configuration(
        profile_id: str, request: Request
    ) -> dict[str, object]:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            record = registry.get(profile_id)
        except ProfileNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="model profile not found"
            ) from error
        runtime = _registered_runtime(request, record.runtime_id)
        catalog = _argument_catalog(request)
        profile = profile_from_record(record, catalog)
        return {
            **_issues_payload(_profile_issues(request, profile, runtime)),
            "preset": redact_preset(record.preset, catalog),
            "arguments": list(launch_vector(profile, catalog, Path(runtime.executable_path))),
        }

    @app.post("/api/profiles/{profile_id}/reset")
    async def reset_profile_configuration(profile_id: str, request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=409, detail="stop the router before resetting a profile"
            )
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            profile = registry.reset(profile_id)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(status_code=422, detail=list(error.errors)) from error
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        return _profile_payload(
            profile,
            artifacts,
            catalog=_argument_catalog(request),
            executables=_runtime_executables(request),
        )

    @app.get("/api/profiles/{profile_id}/export", response_class=PlainTextResponse)
    async def export_profile(profile_id: str, request: Request) -> PlainTextResponse:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            profile = registry.get(profile_id)
        except ProfileNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="model profile not found"
            ) from error
        payload = json.dumps(
            {
                "format": PROFILE_EXPORT_FORMAT,
                "alias": profile.alias,
                "runtime_id": profile.runtime_id,
                "enabled": profile.enabled,
                "exclusive": profile.exclusive,
                "description": profile.description,
                "version": profile.version,
                "configuration": configuration_payload(profile, _argument_catalog(request)),
                "preset": profile.preset,
            },
            indent=2,
            sort_keys=True,
        )
        return PlainTextResponse(
            payload,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{profile.alias}.json"'},
        )

    @app.get("/api/profiles/{profile_id}/command", response_class=PlainTextResponse)
    async def export_profile_command(profile_id: str, request: Request) -> PlainTextResponse:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            record = registry.get(profile_id)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=404, detail="model profile not found") from error
        runtime = _registered_runtime(request, record.runtime_id)
        catalog = _argument_catalog(request)
        command = render_command(
            profile_from_record(record, catalog), catalog, Path(runtime.executable_path)
        )
        return PlainTextResponse(command, media_type="text/plain")

    @app.post("/api/profiles/import", status_code=status.HTTP_201_CREATED)
    async def import_profile(
        import_request: ProfileImportRequest, request: Request
    ) -> dict[str, object]:
        document = import_request.document
        if document.get("format") not in PROFILE_IMPORT_FORMATS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="unsupported profile export format",
            )
        configuration = document.get("configuration")
        if not isinstance(configuration, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="profile export configuration is invalid",
            )
        runtime_id = document.get("runtime_id")
        alias = import_request.alias or document.get("alias")
        enabled = document.get("enabled", False)
        exclusive = document.get("exclusive", False)
        description = document.get("description")
        version = document.get("version", 1)
        if not isinstance(runtime_id, str) or not isinstance(alias, str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="profile export is missing runtime_id or alias",
            )
        if not isinstance(enabled, bool) or not isinstance(exclusive, bool):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="profile export enabled or exclusive value is invalid",
            )
        if description is not None and (
            not isinstance(description, str) or len(description) > PROFILE_DESCRIPTION_LIMIT
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="profile export description is invalid",
            )
        description = _profile_description(description)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="profile export version is invalid",
            )
        catalog = _argument_catalog(request)
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        normalized = normalize_configuration(configuration, catalog)
        imported_model_path = configuration.get("model_path")
        try:
            if not isinstance(imported_model_path, str) or not Path(imported_model_path).is_file():
                record = registry.create_unresolved_import(
                    alias=alias,
                    runtime_id=runtime_id,
                    configuration=normalized,
                    exclusive=exclusive,
                    description=description,
                    version=version,
                )
                artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
                return _profile_payload(
                    record,
                    artifacts,
                    catalog=catalog,
                    executables=_runtime_executables(request),
                )
            profile = ModelProfile(
                alias=alias,
                model_path=Path(imported_model_path),
                arguments=normalized.arguments,
                acknowledged=normalized.acknowledged,
            )
            record = registry.create(
                profile=profile,
                runtime_id=runtime_id,
                enabled=enabled,
                exclusive=exclusive,
                description=description,
                version=version,
            )
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(error.errors)
            ) from error
        return _saved_profile_payload(request, record, profile)

    @app.post("/api/profiles/parse-command")
    async def parse_profile_command(
        parse_request: ProfileCommandParseRequest, request: Request
    ) -> dict[str, object]:
        """Parse a pasted command into editor arguments without saving anything."""
        runtime = _registered_runtime(request, parse_request.runtime_id)
        catalog = _argument_catalog(request)
        try:
            parsed = parse_command(
                parse_request.command, catalog, capabilities=runtime_capabilities(runtime)
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        location = _locate_command_model(request, parsed)
        return {
            "model": location.to_payload(),
            "arguments": [argument.to_payload() for argument in parsed.arguments],
            "ignored_arguments": list(parsed.ignored),
        }

    @app.post("/api/profiles/import-command", status_code=status.HTTP_201_CREATED)
    async def import_profile_command(
        import_request: ProfileCommandImportRequest, request: Request
    ) -> dict[str, object]:
        runtime = _registered_runtime(request, import_request.runtime_id)
        catalog = _argument_catalog(request)
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            parsed = parse_command(
                import_request.command, catalog, capabilities=runtime_capabilities(runtime)
            )
            location = _locate_command_model(request, parsed)
            if location.path is None:
                raise ValueError(
                    "the command names no local model file; download "
                    f"{location.repo_id or 'the model'} from Discover first"
                )
            profile = ModelProfile(
                alias=import_request.alias,
                model_path=Path(location.path),
                arguments=parsed.arguments,
            )
            record = registry.create(profile=profile, runtime_id=runtime.id, enabled=False)
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(status_code=422, detail=list(error.errors)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        payload = _saved_profile_payload(request, record, profile)
        payload["ignored_arguments"] = list(parsed.ignored)
        payload["model"] = location.to_payload()
        return payload

    @app.get("/api/huggingface/models")
    async def search_huggingface_models(
        request: Request,
        q: str = Query(min_length=1, max_length=200),
        sort: Literal["downloads", "likes", "last_modified", "trending_score"] | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ) -> list[dict[str, object]]:
        hub = cast(Catalog, request.app.state.huggingface_catalog)
        try:
            results = await hub.search(q, sort=sort, limit=limit)
        except HfHubHTTPError as error:
            raise _hub_error(error) from error
        except CatalogUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error
        return [
            {
                "repo_id": result.repo_id,
                "downloads": result.downloads,
                "likes": result.likes,
                "last_modified": result.last_modified,
                "gated": result.gated,
                "private": result.private,
                "tags": result.tags,
            }
            for result in results
        ]

    @app.get("/api/huggingface/repositories/{repo_id:path}")
    async def get_huggingface_repository(
        repo_id: str, request: Request, revision: str | None = None
    ) -> dict[str, object]:
        hub = cast(Catalog, request.app.state.huggingface_catalog)
        try:
            manifest = await hub.repository(repo_id, revision=revision)
        except HfHubHTTPError as error:
            raise _hub_error(error) from error
        except CatalogUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error
        return {
            "repo_id": manifest.repo_id,
            "revision": manifest.revision,
            "readme": manifest.readme,
            "groups": [
                {
                    "key": group.key,
                    "quantization": group.quantization,
                    "total_size": group.total_size,
                    "complete": group.complete,
                    "files": [
                        {
                            "path": file.path,
                            "size": file.size,
                            **({"sha256": file.sha256} if file.sha256 else {}),
                            **({"etag": file.etag} if file.etag else {}),
                        }
                        for file in group.files
                    ],
                    "projectors": [
                        {"path": file.path, "size": file.size}
                        for file in group.projector_files
                    ],
                }
                for group in manifest.groups
            ],
        }

    @app.get("/api/downloads")
    async def list_downloads(request: Request) -> list[dict[str, object]]:
        registry = cast(DownloadRegistry, request.app.state.download_registry)
        return [_download_payload(job) for job in registry.list()]

    @app.delete("/api/downloads/terminal")
    async def clear_terminal_downloads(request: Request) -> dict[str, int]:
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        return {"cleared": coordinator.clear_finished()}

    @app.get("/api/library")
    async def list_library_models(request: Request) -> list[dict[str, object]]:
        library = cast(ModelLibrary, request.app.state.model_library)
        logical_models = cast(LogicalModelRegistry, request.app.state.logical_model_registry)
        logical_models.reconcile_discovered(library.discover())
        logical_models.reconcile_profile_links(
            tuple(cast(ProfileRegistry, request.app.state.profile_registry).list())
        )
        return [
            {
                "download_id": model.download_id,
                "repo_id": model.repo_id,
                "revision": model.revision,
                "group_key": model.group_key,
                "primary_path": str(model.primary_path),
                "file_count": model.file_count,
                "total_bytes": model.total_bytes,
            }
            for model in library.list()
        ]

    @app.get("/api/library/logical")
    async def list_logical_library_models(request: Request) -> list[dict[str, object]]:
        library = cast(ModelLibrary, request.app.state.model_library)
        registry = cast(LogicalModelRegistry, request.app.state.logical_model_registry)
        registry.reconcile_discovered(library.discover())
        registry.reconcile_profile_links(
            tuple(cast(ProfileRegistry, request.app.state.profile_registry).list())
        )
        return [
            {
                "id": model.id,
                "primary_path": str(model.primary_path),
                "files": [str(path) for path in model.files],
                "metadata": model.metadata,
                "validation_state": model.validation_state,
                "profile_ids": registry.profile_ids(model.id),
            }
            for model in registry.list()
        ]

    @app.delete("/api/library/logical/{logical_model_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_missing_logical_model(logical_model_id: str, request: Request) -> None:
        registry = cast(LogicalModelRegistry, request.app.state.logical_model_registry)
        try:
            registry.remove_missing(logical_model_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="logical model not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/library/reconcile")
    async def reconcile_library(request: Request) -> dict[str, int]:
        library = cast(ModelLibrary, request.app.state.model_library)
        registry = cast(LogicalModelRegistry, request.app.state.logical_model_registry)
        result = library.reconcile()
        logical = registry.reconcile_discovered(library.discover())
        registry.reconcile_profile_links(
            tuple(cast(ProfileRegistry, request.app.state.profile_registry).list())
        )
        return {
            "managed_jobs": result.managed_jobs,
            "valid_models": result.valid_models,
            "invalid_jobs": result.invalid_jobs,
            "stray_gguf_files": result.stray_gguf_files,
            "logical_models": len(logical),
            "missing_logical_models": sum(
                model.validation_state == "missing" for model in logical
            ),
            "linked_logical_models": sum(
                bool(registry.profile_ids(model.id)) for model in logical
            ),
        }

    @app.get("/api/library/discover")
    async def discover_library_models(request: Request) -> list[dict[str, object]]:
        library = cast(ModelLibrary, request.app.state.model_library)
        return [
            {
                "primary_path": str(model.primary_path),
                "files": [str(path) for path in model.files],
                "total_bytes": model.total_bytes,
                "model_name": model.model_name,
                "metadata": model.metadata,
            }
            for model in library.discover()
        ]

    @app.post("/api/library/import", status_code=status.HTTP_201_CREATED)
    async def import_external_model(
        import_request: ExternalModelImportRequest, request: Request
    ) -> dict[str, object]:
        library = cast(ModelLibrary, request.app.state.model_library)
        candidate = next(
            (
                item
                for item in library.discover()
                if str(item.primary_path) == import_request.primary_path
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="complete external model was not found")
        profile = ProfileCreateRequest(
            alias=import_request.alias,
            runtime_id=import_request.runtime_id,
            model_path=str(candidate.primary_path),
            enabled=False,
        )
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            created = registry.create(
                profile=profile.to_domain(_argument_catalog(request)),
                runtime_id=profile.runtime_id,
                enabled=False,
            )
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(status_code=422, detail=list(error.errors)) from error
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        return _profile_payload(
            created,
            artifacts,
            catalog=_argument_catalog(request),
            executables=_runtime_executables(request),
        )

    @app.delete("/api/library/{download_id}")
    async def delete_library_model(download_id: str, request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before deleting a model artifact",
            )
        artifacts = cast(ModelArtifactRegistry, request.app.state.model_artifact_registry)
        try:
            return _download_payload(artifacts.delete(download_id))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ModelArtifactError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/downloads/{job_id}/redownload")
    async def redownload_model(job_id: str, request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stop the router before re-downloading a model artifact",
            )
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        try:
            return _download_payload(coordinator.redownload(job_id))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/downloads", status_code=status.HTTP_201_CREATED)
    async def create_download(
        download: DownloadCreateRequest, request: Request
    ) -> dict[str, object]:
        hub = cast(Catalog, request.app.state.huggingface_catalog)
        registry = cast(DownloadRegistry, request.app.state.download_registry)
        try:
            manifest = await hub.repository(download.repo_id, revision=download.revision)
            job = registry.create(
                manifest,
                download.group_key,
                include_projector=download.include_projector,
            )
            payload = _download_payload(job)
            coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
            coordinator.start(job.id)
            return payload
        except HfHubHTTPError as error:
            raise _hub_error(error) from error
        except CatalogUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error
        except DownloadPlanError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

    @app.post("/api/downloads/{job_id}/cancel")
    async def cancel_download(job_id: str, request: Request) -> dict[str, object]:
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        try:
            return _download_payload(await coordinator.cancel(job_id))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/downloads/{job_id}/pause")
    async def pause_download(job_id: str, request: Request) -> dict[str, object]:
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        try:
            return _download_payload(await coordinator.pause(job_id))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/downloads/{job_id}/resume")
    async def resume_download(job_id: str, request: Request) -> dict[str, object]:
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        try:
            return _download_payload(coordinator.resume(job_id))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    if packaged_static_dir.is_dir():
        app.mount("/", StaticFiles(directory=packaged_static_dir, html=True), name="frontend")

    return app


app = create_app()
