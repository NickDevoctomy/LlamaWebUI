"""Hugging Face Hub discovery adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from huggingface_hub import HfApi

from llamawebui.domain.model_manifest import GgufGroup, HubFile, group_gguf_files


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


class HuggingFaceCatalog:
    def __init__(self, token: str | None = None, *, api: HfApi | None = None) -> None:
        self._api = api or HfApi(token=token)

    async def search(
        self, query: str, *, sort: str = "downloads", limit: int = 25
    ) -> tuple[ModelSearchResult, ...]:
        models = await asyncio.to_thread(
            lambda: list(
                self._api.list_models(
                    filter="gguf", search=query, sort=sort, direction=-1, limit=limit, full=True
                )
            )
        )
        return tuple(
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

    async def repository(
        self, repo_id: str, *, revision: str | None = None
    ) -> RepositoryManifest:
        model = await asyncio.to_thread(
            self._api.model_info,
            repo_id,
            revision=revision,
            files_metadata=True,
        )
        if model.sha is None:
            raise ValueError(f"repository did not resolve to a commit SHA: {repo_id}")
        files = tuple(
            HubFile(path=sibling.rfilename, size=sibling.size) for sibling in model.siblings or ()
        )
        return RepositoryManifest(
            repo_id=model.id,
            revision=model.sha,
            groups=group_gguf_files(files),
        )
