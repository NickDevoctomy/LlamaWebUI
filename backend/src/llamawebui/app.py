"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, status
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel, Field

from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.models import RuntimeRecord
from llamawebui.services.huggingface_catalog import Catalog, HuggingFaceCatalog
from llamawebui.services.runtime_probe import RuntimeProber, probe_runtime
from llamawebui.services.runtime_registry import (
    RuntimeAlreadyRegisteredError,
    RuntimeNotFoundError,
    RuntimeRegistry,
)


class RuntimeRegistrationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    executable_path: str
    backend: str | None = Field(default=None, max_length=50)


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
        except RuntimeNotFoundError as error:
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

    return app


app = create_app()
