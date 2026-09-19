"""FastAPI application factory."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel, Field, field_validator

from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.model_profile import (
    AdvancedOption,
    ModelProfile,
    ProfileValidationError,
    write_combined_preset_atomic,
)
from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState
from llamawebui.models import (
    DownloadJobRecord,
    ModelProfileRecord,
    RuntimeRecord,
    ServerRunRecord,
)
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
from llamawebui.services.huggingface_catalog import Catalog, HuggingFaceCatalog
from llamawebui.services.profile_registry import (
    ProfileAliasExistsError,
    ProfileNotFoundError,
    ProfileRegistry,
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
)
from llamawebui.services.server_run_registry import ServerRunRegistry


class RuntimeRegistrationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    executable_path: str
    backend: str | None = Field(default=None, max_length=50)


class AdvancedOptionRequest(BaseModel):
    name: str
    value: str | int | bool = True


class ProfileCreateRequest(BaseModel):
    alias: str
    runtime_id: str
    model_path: str
    enabled: bool = True
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

    def to_domain(self) -> ModelProfile:
        return ModelProfile(
            alias=self.alias,
            model_path=Path(self.model_path),
            no_reasoning_preserve=self.no_reasoning_preserve,
            n_gpu_layers=self.n_gpu_layers,
            ctx_size=self.ctx_size,
            flash_attn=self.flash_attn,
            load_mode=self.load_mode,
            lazy_mode=self.lazy_mode,
            cache_ram=self.cache_ram,
            fit=self.fit,
            override_tensor=self.override_tensor,
            cache_type_k=self.cache_type_k,
            cache_type_v=self.cache_type_v,
            threads=self.threads,
            batch_size=self.batch_size,
            ubatch_size=self.ubatch_size,
            advanced=tuple(AdvancedOption(option.name, option.value) for option in self.advanced),
        )


class DownloadCreateRequest(BaseModel):
    repo_id: str = Field(min_length=3, max_length=400)
    group_key: str = Field(min_length=1)
    revision: str | None = Field(default=None, max_length=100)


class ServerStartRequest(BaseModel):
    runtime_id: str


class RouterModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=400)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model ID must not be empty")
        return value


def _runtime_payload(runtime: RuntimeRecord) -> dict[str, object]:
    return {
        "id": runtime.id,
        "name": runtime.name,
        "executable_path": runtime.executable_path,
        "build": runtime.build,
        "commit": runtime.commit,
        "backend": runtime.backend,
        "devices": runtime.devices,
        "options": runtime.options,
        "usable": runtime.probe_error is None and bool(runtime.options),
        "probe_error": runtime.probe_error,
    }


def _profile_payload(profile: ModelProfileRecord) -> dict[str, object]:
    return {
        "id": profile.id,
        "alias": profile.alias,
        "runtime_id": profile.runtime_id,
        "model_path": profile.model_path,
        "configuration": profile.configuration,
        "preset": profile.preset,
        "enabled": profile.enabled,
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


def _server_payload(supervisor: RouterSupervisor, settings: Settings) -> dict[str, object]:
    return {
        "state": supervisor.state,
        "pid": supervisor.pid,
        "last_exit_code": supervisor.last_exit_code,
        "endpoint": f"http://{settings.router_host}:{settings.router_port}",
        "logs": supervisor.logs,
    }


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


def create_app(
    settings: Settings | None = None,
    *,
    runtime_prober: RuntimeProber = probe_runtime,
    catalog: Catalog | None = None,
    file_transfer: FileTransfer | None = None,
    router_supervisor: RouterSupervisor | None = None,
    router_port_probe: RouterPortProbe = probe_router_port,
    router_client: RouterClient | None = None,
) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        upgrade_database(app_settings.database_path)
        engine = create_database_engine(app_settings.database_path)
        app.state.runtime_registry = RuntimeRegistry(engine, prober=runtime_prober)
        app.state.profile_registry = ProfileRegistry(engine)
        app.state.download_registry = DownloadRegistry(engine, app_settings.data_dir / "models")
        token = app_settings.hf_token.get_secret_value() if app_settings.hf_token else None
        app.state.huggingface_catalog = catalog or HuggingFaceCatalog(token)
        transfer = file_transfer or HuggingFaceFileTransfer(token)
        app.state.download_coordinator = DownloadCoordinator(
            app.state.download_registry,
            DownloadWorker(app.state.download_registry, transfer),
        )
        app.state.download_coordinator.start_pending()
        app.state.router_supervisor = router_supervisor or RouterSupervisor(
            restart_policy=RouterRestartPolicy(
                max_attempts=app_settings.router_restart_max_attempts,
                window_seconds=app_settings.router_restart_window_seconds,
                delay_seconds=app_settings.router_restart_delay_seconds,
                ready_timeout_seconds=app_settings.router_ready_timeout_seconds,
            )
        )
        app.state.router_client = router_client or HttpRouterClient(
            app_settings.router_host, app_settings.router_port
        )
        app.state.event_broker = EventBroker(app_settings.event_history_capacity)
        app.state.router_event_synchronizer = RouterEventSynchronizer(
            app.state.router_client, app.state.event_broker
        )
        app.state.server_run_registry = ServerRunRegistry(engine)
        app.state.active_server_run_id = None
        app.state.router_lifecycle_lock = asyncio.Lock()

        def record_router_state(
            state: RouterState, pid: int | None, exit_code: int | None
        ) -> None:
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
        try:
            yield
        finally:
            async with app.state.router_lifecycle_lock:
                await app.state.router_supervisor.stop()
            app.state.router_supervisor.set_state_observer(None)
            await app.state.router_event_synchronizer.shutdown()
            await app.state.download_coordinator.shutdown()
            engine.dispose()

    app = FastAPI(title="LlamaWebUI", version="0.1.0", lifespan=lifespan)

    async def start_router(runtime_id: str, request: Request) -> dict[str, object]:
        runtimes = cast(RuntimeRegistry, request.app.state.runtime_registry)
        profiles = cast(ProfileRegistry, request.app.state.profile_registry)
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        run_registry = cast(ServerRunRegistry, request.app.state.server_run_registry)
        if supervisor.state not in {RouterState.STOPPED, RouterState.CRASHED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"router cannot start while {supervisor.state}",
            )
        try:
            runtime = runtimes.get(runtime_id)
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        if "models-preset" not in runtime.options:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="runtime does not support --models-preset",
            )
        enabled_profiles = profiles.list_enabled(runtime.id)
        if not enabled_profiles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="runtime has no enabled model profiles",
            )

        preset_path = app_settings.data_dir / "generated" / "llama-models.ini"
        write_combined_preset_atomic(
            preset_path, tuple(profile.preset for profile in enabled_profiles)
        )
        launch = RouterLaunch(
            executable=Path(runtime.executable_path),
            preset_path=preset_path,
            host=app_settings.router_host,
            port=app_settings.router_port,
        )
        endpoint = f"http://{app_settings.router_host}:{app_settings.router_port}"
        run = run_registry.create(runtime.id, endpoint)
        request.app.state.active_server_run_id = run.id
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
            "data_dir": str(app_settings.data_dir),
            "database_path": str(app_settings.database_path),
            "hugging_face_token_configured": app_settings.hf_token is not None,
        }

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
        return _server_payload(supervisor, app_settings)

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
            return await start_router(start_request.runtime_id, request)

    @app.post("/api/server/stop")
    async def stop_server(request: Request) -> dict[str, object]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        lock = cast(asyncio.Lock, request.app.state.router_lifecycle_lock)
        async with lock:
            try:
                await supervisor.stop()
            except (RuntimeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from error
        return _server_payload(supervisor, app_settings)

    @app.post("/api/server/restart")
    async def restart_server(request: Request) -> dict[str, object]:
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
            runtime_id = run_registry.get(run_id).runtime_id
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
            return await start_router(runtime_id, request)

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

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for event in client.model_events():
                    yield _router_model_event_sse(event)
            except RouterAPIError as error:
                status_code = error.status_code if 400 <= error.status_code < 500 else 502
                yield _router_model_event_sse(
                    RouterModelEvent(
                        model="*",
                        event="error",
                        data={"code": status_code, "message": str(error)},
                    )
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/server/models/load")
    async def load_router_model(
        model_request: RouterModelRequest, request: Request
    ) -> dict[str, bool]:
        supervisor = cast(RouterSupervisor, request.app.state.router_supervisor)
        client = cast(RouterClient, request.app.state.router_client)
        _require_running_router(supervisor)
        try:
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

    @app.get("/api/runtimes")
    async def list_runtimes(request: Request) -> list[dict[str, object]]:
        registry = cast(RuntimeRegistry, request.app.state.runtime_registry)
        return [_runtime_payload(runtime) for runtime in registry.list()]

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
        try:
            registry.remove(runtime_id)
        except RuntimeInUseError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.get("/api/profiles")
    async def list_profiles(request: Request) -> list[dict[str, object]]:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        return [_profile_payload(profile) for profile in registry.list()]

    @app.post("/api/profiles", status_code=status.HTTP_201_CREATED)
    async def create_profile(
        profile_request: ProfileCreateRequest, request: Request
    ) -> dict[str, object]:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            profile = registry.create(
                profile=profile_request.to_domain(),
                runtime_id=profile_request.runtime_id,
                enabled=profile_request.enabled,
            )
        except RuntimeNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ProfileAliasExistsError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ProfileValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(error.errors)
            ) from error
        return _profile_payload(profile)

    @app.delete("/api/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_profile(profile_id: str, request: Request) -> None:
        registry = cast(ProfileRegistry, request.app.state.profile_registry)
        try:
            registry.remove(profile_id)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

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
        return {
            "repo_id": manifest.repo_id,
            "revision": manifest.revision,
            "groups": [
                {
                    "key": group.key,
                    "quantization": group.quantization,
                    "total_size": group.total_size,
                    "complete": group.complete,
                    "files": [
                        {"path": file.path, "size": file.size} for file in group.files
                    ],
                }
                for group in manifest.groups
            ],
        }

    @app.get("/api/downloads")
    async def list_downloads(request: Request) -> list[dict[str, object]]:
        registry = cast(DownloadRegistry, request.app.state.download_registry)
        return [_download_payload(job) for job in registry.list()]

    @app.post("/api/downloads", status_code=status.HTTP_201_CREATED)
    async def create_download(
        download: DownloadCreateRequest, request: Request
    ) -> dict[str, object]:
        hub = cast(Catalog, request.app.state.huggingface_catalog)
        registry = cast(DownloadRegistry, request.app.state.download_registry)
        try:
            manifest = await hub.repository(download.repo_id, revision=download.revision)
            job = registry.create(manifest, download.group_key)
            payload = _download_payload(job)
            coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
            coordinator.start(job.id)
            return payload
        except HfHubHTTPError as error:
            raise _hub_error(error) from error
        except DownloadPlanError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

    @app.post("/api/downloads/{job_id}/cancel")
    async def cancel_download(job_id: str, request: Request) -> dict[str, object]:
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        try:
            return _download_payload(coordinator.cancel(job_id))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/downloads/{job_id}/pause")
    async def pause_download(job_id: str, request: Request) -> dict[str, object]:
        coordinator = cast(DownloadCoordinator, request.app.state.download_coordinator)
        try:
            return _download_payload(coordinator.pause(job_id))
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

    return app


app = create_app()
