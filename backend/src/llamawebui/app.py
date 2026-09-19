"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.models import RuntimeRecord
from llamawebui.services.runtime_probe import RuntimeProber, probe_runtime
from llamawebui.services.runtime_registry import RuntimeAlreadyRegisteredError, RuntimeRegistry


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


def create_app(
    settings: Settings | None = None, *, runtime_prober: RuntimeProber = probe_runtime
) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        upgrade_database(app_settings.database_path)
        engine = create_database_engine(app_settings.database_path)
        app.state.runtime_registry = RuntimeRegistry(engine, prober=runtime_prober)
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

    return app


app = create_app()
