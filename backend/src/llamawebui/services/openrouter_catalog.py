"""Search OpenRouter's public model list."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class OpenRouterUnavailableError(RuntimeError):
    """OpenRouter could not be reached and no cached model list was available."""


@dataclass(frozen=True, slots=True)
class OpenRouterModel:
    id: str
    name: str
    description: str
    context_length: int | None
    # USD per unit as OpenRouter reports it, for example {"prompt": "0.000002"} per token.
    # "-1" means the price varies, as with routers that pick a model per request.
    pricing: dict[str, str]

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "context_length": self.context_length,
            "pricing": dict(self.pricing),
        }


class OpenRouterCatalog:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, cache_ttl_seconds: float = 3600.0
    ) -> None:
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: tuple[float, tuple[OpenRouterModel, ...]] | None = None
        self._lock = asyncio.Lock()

    async def models(self) -> tuple[OpenRouterModel, ...]:
        async with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < self._cache_ttl_seconds:
                return self._cache[1]
            try:
                models = await self._fetch()
            except (httpx.HTTPError, ValueError) as error:
                if self._cache:
                    return self._cache[1]
                raise OpenRouterUnavailableError(
                    "OpenRouter's model list is unavailable"
                ) from error
            self._cache = (time.monotonic(), models)
            return models

    async def search(self, query: str, limit: int = 50) -> tuple[tuple[OpenRouterModel, ...], int]:
        """Models whose id or name contains every word of the query, and how many matched."""
        terms = query.lower().split()
        matches = [
            model
            for model in await self.models()
            if all(term in f"{model.id} {model.name}".lower() for term in terms)
        ]
        return tuple(matches[:limit]), len(matches)

    async def get(self, model_id: str) -> OpenRouterModel | None:
        return next((model for model in await self.models() if model.id == model_id), None)

    async def _fetch(self) -> tuple[OpenRouterModel, ...]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        try:
            response = await client.get(OPENROUTER_MODELS_URL)
            response.raise_for_status()
            payload = response.json()
        finally:
            if owns_client:
                await client.aclose()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("OpenRouter returned an invalid model list")
        return tuple(model for item in data if (model := _parse(item)) is not None)


def _parse(item: object) -> OpenRouterModel | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    pricing = item.get("pricing")
    context_length = item.get("context_length")
    return OpenRouterModel(
        id=item["id"],
        name=item["name"] if isinstance(item.get("name"), str) else item["id"],
        description=item["description"] if isinstance(item.get("description"), str) else "",
        context_length=context_length if isinstance(context_length, int) else None,
        pricing=(
            {key: value for key, value in pricing.items() if isinstance(value, str)}
            if isinstance(pricing, dict)
            else {}
        ),
    )
