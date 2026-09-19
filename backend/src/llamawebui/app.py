"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from llamawebui.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="LlamaWebUI", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "data_dir": str(app_settings.data_dir),
            "database_path": str(app_settings.database_path),
            "hugging_face_token_configured": app_settings.hf_token is not None,
        }

    return app


app = create_app()
