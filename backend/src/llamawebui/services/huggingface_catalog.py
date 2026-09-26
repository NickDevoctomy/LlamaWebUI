"""Hugging Face Hub discovery adapter."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypeVar

from huggingface_hub import HfApi
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError

from llamawebui.domain.model_manifest import GgufGroup, HubFile, group_gguf_files

CacheKey = TypeVar("CacheKey")
CacheValue = TypeVar("CacheValue")


@dataclass(frozen=True, slots=True)
class ModelSearchResult:
    repo_id: str
    downloads: int
    likes: int
    last_modified: datetime | None
    gated: bool
    private: bool
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryManifest:
    repo_id: str
    revision: str
    groups: tuple[GgufGroup, ...]
    readme: str | None = None


class CatalogUnavailableError(RuntimeError):
    """The Hub could not be reached and no cached response was available."""


class Catalog(Protocol):
    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 25
    ) -> Sequence[ModelSearchResult]: ...

    async def repository(
        self, repo_id: str, *, revision: str | None = None
    ) -> RepositoryManifest: ...


class HuggingFaceCatalog:
    def __init__(
        self,
        token: str | None = None,
        *,
        api: HfApi | None = None,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        self._api = api or HfApi(token=token)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._search_cache: dict[
            tuple[str, str | None, int], tuple[float, tuple[ModelSearchResult, ...]]
        ] = {}
        self._repository_cache: dict[tuple[str, str | None], tuple[float, RepositoryManifest]] = {}

    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 25
    ) -> tuple[ModelSearchResult, ...]:
        request: dict[str, object] = {
            "filter": "gguf",
            "search": query,
            "limit": limit,
            "full": True,
        }
        if sort is not None:
            request.update(sort=sort, direction=-1)
        cache_key = (query, sort, limit)
        try:
            models = await asyncio.to_thread(lambda: list(self._api.list_models(**request)))
        except (HfHubHTTPError, OSError) as error:
            cached = self._cached(self._search_cache, cache_key)
            if cached is not None:
                return cached
            raise CatalogUnavailableError("Hugging Face search is unavailable") from error
        results = tuple(
            ModelSearchResult(
                repo_id=model.id,
                downloads=model.downloads or 0,
                likes=model.likes or 0,
                last_modified=model.last_modified,
                gated=bool(model.gated),
                private=bool(model.private),
                tags=tuple(model.tags or ()),
            )
            for model in models
        )
        self._search_cache[cache_key] = (time.monotonic(), results)
        return results

    async def repository(
        self, repo_id: str, *, revision: str | None = None
    ) -> RepositoryManifest:
        cache_key = (repo_id, revision)
        try:
            model = await asyncio.to_thread(
                self._api.model_info,
                repo_id,
                revision=revision,
                files_metadata=True,
            )
        except (HfHubHTTPError, OSError) as error:
            cached = self._cached(self._repository_cache, cache_key)
            if cached is not None:
                return cached
            raise CatalogUnavailableError(
                "Hugging Face repository metadata is unavailable"
            ) from error
        if model.sha is None:
            raise ValueError(f"repository did not resolve to a commit SHA: {repo_id}")
        files = tuple(
            HubFile(
                path=sibling.rfilename,
                size=sibling.size,
                sha256=_sibling_sha256(sibling),
                etag=_sibling_etag(sibling),
            )
            for sibling in model.siblings or ()
        )
        manifest = RepositoryManifest(
            repo_id=model.id,
            revision=model.sha,
            groups=group_gguf_files(files),
            readme=await self._readme(repo_id, model.sha),
        )
        self._repository_cache[cache_key] = (time.monotonic(), manifest)
        return manifest

    def _cached(
        self,
        cache: dict[CacheKey, tuple[float, CacheValue]],
        key: CacheKey,
    ) -> CacheValue | None:
        entry = cache.get(key)
        if entry is None:
            return None
        timestamp, value = entry
        if time.monotonic() - timestamp > self._cache_ttl_seconds:
            cache.pop(key, None)
            return None
        return value

    async def _readme(self, repo_id: str, revision: str) -> str | None:
        try:
            content = await asyncio.to_thread(
                self._api.hf_hub_download,
                repo_id,
                "README.md",
                revision=revision,
                repo_type="model",
            )
        except EntryNotFoundError:
            return None
        return await asyncio.to_thread(
            Path(content).read_text, encoding="utf-8", errors="replace"
        )


def _sibling_sha256(sibling: object) -> str | None:
    """Read a Hub LFS SHA-256 without depending on one SDK metadata shape."""
    lfs = getattr(sibling, "lfs", None)
    oid = getattr(lfs, "oid", None)
    if (
        isinstance(oid, str)
        and len(oid) == 64
        and all(character in "0123456789abcdefABCDEF" for character in oid)
    ):
        return oid.lower()
    return None


def _sibling_etag(sibling: object) -> str | None:
    etag = getattr(sibling, "etag", None)
    return etag.strip('"') if isinstance(etag, str) and etag else None
