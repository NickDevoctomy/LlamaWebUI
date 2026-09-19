"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, status
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel, Field

from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_profile import AdvancedOption, ModelProfile, ProfileValidationError
from llamawebui.models import DownloadJobRecord, ModelProfileRecord, RuntimeRecord
from llamawebui.services.download_registry import (
    DownloadJobNotFoundError,
    DownloadPlanError,
    DownloadRegistry,
)
from llamawebui.services.huggingface_catalog import Catalog, HuggingFaceCatalog
from llamawebui.services.profile_registry import (
    ProfileAliasExistsError,
    ProfileNotFoundError,
    ProfileRegistry,
)
from llamawebui.services.runtime_probe import RuntimeProber, probe_runtime
from llamawebui.services.runtime_registry import (
    RuntimeAlreadyRegisteredError,
    RuntimeInUseError,
    RuntimeNotFoundError,
    RuntimeRegistry,
)


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
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="LlamaWebUI", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "data_dir": str(app_settings.data_dir),
            "database_path": str(app_settings.database_path),
            "hugging_face_token_configured": app_settings.hf_token is not None,
        }

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
            return _download_payload(registry.create(manifest, download.group_key))
        except HfHubHTTPError as error:
            raise _hub_error(error) from error
        except DownloadPlanError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

    @app.post("/api/downloads/{job_id}/cancel")
    async def cancel_download(job_id: str, request: Request) -> dict[str, object]:
        registry = cast(DownloadRegistry, request.app.state.download_registry)
        try:
            return _download_payload(registry.transition(job_id, DownloadState.CANCELLED))
        except DownloadJobNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return app


app = create_app()
