"""Control-plane operations shared by the HTTP API and the assistant.

The rules here (when the router may start, when profiles may change, how a download is
queued) used to live in the HTTP handlers. Keeping them in one place means the assistant
cannot bypass a check the UI is held to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Literal

from llamawebui.config import Settings
from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.model_profile import (
    ModelProfile,
    render_command,
    validate_profile,
    write_combined_preset_atomic,
)
from llamawebui.domain.profile_arguments import errors_of, warnings_of
from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState
from llamawebui.models import DownloadJobRecord, ModelProfileRecord, RuntimeRecord
from llamawebui.services.download_coordinator import DownloadCoordinator
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.huggingface_catalog import Catalog
from llamawebui.services.model_artifact_registry import ModelArtifactRegistry
from llamawebui.services.profile_registry import (
    ProfileRegistry,
    configuration_payload,
    profile_from_record,
)
from llamawebui.services.router_client import RouterModel
from llamawebui.services.router_supervisor import RouterSupervisor
from llamawebui.services.runtime_registry import (
    RuntimeNotFoundError,
    RuntimeRegistry,
    runtime_capabilities,
)
from llamawebui.services.server_run_registry import ServerRunRegistry
from llamawebui.services.token_registry import TokenRegistry

ErrorKind = Literal["conflict", "not_found", "invalid", "timeout", "upstream"]
STOPPED_STATES = frozenset({RouterState.STOPPED, RouterState.CRASHED})


class ControlPlaneError(Exception):
    """A refused or failed operation; ``kind`` says how the API reports it."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def router_diagnostics(runtime: RuntimeRecord) -> tuple[str, ...]:
    """Why a runtime cannot run the router, if it cannot."""
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


def next_profile_version(
    current: ModelProfileRecord,
    profile: ModelProfile,
    *,
    requested_version: int | None,
    runtime_id: str,
    exclusive: bool,
    catalog: ArgumentCatalog,
) -> int | None:
    """The version to save, or ``None`` to keep the current one.

    A recommended profile keeps the version of its recommendation. Any other profile
    takes a version typed in by hand, or moves up one when its settings change.
    """
    if current.recommendation_id is not None:
        return None
    if requested_version is not None and requested_version != current.version:
        return requested_version
    settings_changed = (
        profile.configuration.to_payload(catalog)["arguments"]
        != configuration_payload(current, catalog).get("arguments")
        or str(profile.model_path.resolve()) != current.model_path
        or runtime_id != current.runtime_id
        or exclusive != current.exclusive
    )
    return current.version + 1 if settings_changed else None


def exclusive_conflict(
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


class ControlPlane:
    def __init__(
        self,
        *,
        settings: Settings,
        catalog: ArgumentCatalog,
        runtimes: RuntimeRegistry,
        profiles: ProfileRegistry,
        artifacts: ModelArtifactRegistry,
        tokens: TokenRegistry,
        supervisor: RouterSupervisor,
        server_runs: ServerRunRegistry,
        hub: Catalog,
        downloads: DownloadRegistry,
        coordinator: DownloadCoordinator,
        port_probe: Callable[[str, int], Awaitable[bool]],
        on_server_run: Callable[[str], None],
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._runtimes = runtimes
        self._profiles = profiles
        self._artifacts = artifacts
        self._tokens = tokens
        self._supervisor = supervisor
        self._server_runs = server_runs
        self._hub = hub
        self._downloads = downloads
        self._coordinator = coordinator
        self._port_probe = port_probe
        self._on_server_run = on_server_run
        self.lock = asyncio.Lock()

    @property
    def preset_path(self) -> Path:
        return self._settings.data_dir / "generated" / "llama-models.ini"

    @property
    def router_stopped(self) -> bool:
        return self._supervisor.state in STOPPED_STATES

    def require_router_stopped(self, action: str) -> None:
        """Refuse ``action`` (e.g. "editing a profile") while the router runs."""
        if not self.router_stopped:
            raise ControlPlaneError("conflict", f"stop the router before {action}")

    async def start_router(self, runtime_id: str) -> None:
        async with self.lock:
            await self.start_router_locked(runtime_id)

    async def stop_router(self) -> None:
        async with self.lock:
            await self.stop_router_locked()

    async def stop_router_locked(self) -> None:
        try:
            await self._supervisor.stop()
        except (RuntimeError, ValueError) as error:
            raise ControlPlaneError("conflict", str(error)) from error
        self._tokens.remove_materialized_tokens()

    async def start_router_locked(self, runtime_id: str) -> None:
        """Start the router with a runtime's enabled profiles; the caller holds ``lock``."""
        supervisor = self._supervisor
        if supervisor.state not in STOPPED_STATES:
            raise ControlPlaneError("conflict", f"router cannot start while {supervisor.state}")
        try:
            runtime = self._runtimes.get(runtime_id)
        except RuntimeNotFoundError as error:
            raise ControlPlaneError("not_found", str(error)) from error
        diagnostics = router_diagnostics(runtime)
        if diagnostics:
            raise ControlPlaneError("invalid", "; ".join(diagnostics))
        enabled_profiles = self._profiles.list_enabled(runtime.id)
        if not enabled_profiles:
            raise ControlPlaneError("invalid", "runtime has no enabled model profiles")
        broken = tuple(
            profile.alias
            for profile in enabled_profiles
            if not self._artifacts.profile_available(profile)
        )
        if broken:
            raise ControlPlaneError(
                "conflict", f"enabled model profiles are broken: {', '.join(broken)}"
            )
        capabilities = runtime_capabilities(runtime)
        launch_profiles = tuple(
            profile_from_record(profile, self._catalog) for profile in enabled_profiles
        )
        launch_issues = {
            profile.alias: validate_profile(profile, self._catalog, capabilities)
            for profile in launch_profiles
        }
        blockers = [
            f"{alias}: {message}"
            for alias, issues in launch_issues.items()
            for message in errors_of(issues)
        ]
        if blockers:
            raise ControlPlaneError(
                "invalid", "enabled model profiles cannot launch: " + "; ".join(blockers)
            )

        write_combined_preset_atomic(
            self.preset_path, tuple(profile.preset for profile in enabled_profiles)
        )
        self._tokens.materialize_enabled_tokens()
        host, port = self._settings.router_host, self._settings.router_port
        launch = RouterLaunch(
            executable=Path(runtime.executable_path),
            preset_path=self.preset_path,
            host=host,
            port=port,
            api_key_file=self._tokens.key_file if self._tokens.has_enabled() else None,
        )
        supervisor.record_log("llama-server router command: " + " ".join(launch.arguments()))
        for launch_profile in launch_profiles:
            supervisor.record_log(
                "llama-server model command (preset equivalent): "
                + render_command(launch_profile, self._catalog, Path(runtime.executable_path))
            )
            for warning in warnings_of(launch_issues[launch_profile.alias]):
                supervisor.record_log(f"profile {launch_profile.alias} warning: {warning}")
        run = self._server_runs.create(runtime.id, f"http://{host}:{port}")
        self._on_server_run(run.id)
        if not await self._port_probe(host, port):
            detail = f"router port is already in use: {host}:{port}"
            self._server_runs.update(
                run.id, RouterState.CRASHED, pid=None, exit_code=None, error=detail
            )
            raise ControlPlaneError("conflict", detail)

        try:
            await supervisor.start(launch)
            await supervisor.wait_until_ready(
                launch, timeout_seconds=self._settings.router_ready_timeout_seconds
            )
        except TimeoutError as error:
            self._failed_start(run.id, error)
            raise ControlPlaneError("timeout", str(error)) from error
        except (OSError, RuntimeError) as error:
            self._failed_start(run.id, error)
            raise ControlPlaneError("upstream", str(error)) from error
        except ValueError as error:
            self._failed_start(run.id, error)
            raise ControlPlaneError("conflict", str(error)) from error

    def _failed_start(self, run_id: str, error: Exception) -> None:
        self._tokens.remove_materialized_tokens()
        self._server_runs.update(
            run_id,
            self._supervisor.state,
            pid=self._supervisor.pid,
            exit_code=self._supervisor.last_exit_code,
            error=str(error),
        )

    async def queue_download(
        self,
        repo_id: str,
        group_key: str,
        *,
        revision: str | None = None,
        include_projector: bool = False,
    ) -> DownloadJobRecord:
        """Plan a download from a repository's quantization group and start it.

        Raises the Hub's ``HfHubHTTPError``/``CatalogUnavailableError`` and
        ``DownloadPlanError`` for the caller to report.
        """
        manifest = await self._hub.repository(repo_id, revision=revision)
        job = self._downloads.create(manifest, group_key, include_projector=include_projector)
        self._coordinator.start(job.id)
        return job
