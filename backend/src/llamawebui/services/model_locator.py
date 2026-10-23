"""Find the local copy of a model referenced by an imported llama-server command."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Literal

from llamawebui.domain.download_job import DownloadState
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.model_artifact_registry import ModelArtifactError, ModelArtifactRegistry
from llamawebui.services.model_library import ModelLibrary

_SHARD_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$", re.IGNORECASE
)
_QUANT_SUFFIX = re.compile(r"[-_.](?:UD-)?(?:I?Q\d[A-Z0-9_]*|BF16|F16|F32)$", re.IGNORECASE)

LocationStatus = Literal["found", "relocated", "missing"]


@dataclass(frozen=True, slots=True)
class LocalModel:
    """A primary GGUF file present on disk, with its download when LlamaWebUI fetched it."""

    primary_path: Path
    download_id: str | None = None
    repo_id: str | None = None
    group_key: str | None = None


@dataclass(frozen=True, slots=True)
class ModelLocation:
    """Where an imported command's model lives locally, or how to find it on Discover."""

    status: LocationStatus
    requested_path: str | None
    path: str | None
    download_id: str | None
    repo_id: str | None
    search: str

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requested_path": self.requested_path,
            "path": self.path,
            "download_id": self.download_id,
            "repo_id": self.repo_id,
            "search": self.search,
        }


def local_models(
    downloads: DownloadRegistry, artifacts: ModelArtifactRegistry, library: ModelLibrary
) -> tuple[LocalModel, ...]:
    """Completed downloads first, then other GGUF files found under the model root.

    Only file presence is checked; checksums are left to profile validation so that parsing
    a command stays fast for multi-gigabyte models.
    """
    models: dict[Path, LocalModel] = {}
    for job in downloads.list(include_hidden=True):
        if DownloadState(job.state) is not DownloadState.COMPLETED:
            continue
        try:
            primary = artifacts.primary_path(job)
        except ModelArtifactError:
            continue
        if primary is None or not primary.is_file():
            continue
        models.setdefault(primary, LocalModel(primary, job.id, job.repo_id, job.group_key))
    for discovered in library.discover():
        models.setdefault(discovered.primary_path.resolve(), LocalModel(discovered.primary_path))
    return tuple(models.values())


def locate_model(
    requested_path: str | None,
    candidates: Sequence[LocalModel],
    *,
    hf_repo: str | None = None,
    hf_file: str | None = None,
) -> ModelLocation:
    """Resolve a command's model reference against the local library.

    A path that exists is used as-is. Otherwise the file is matched by name (so a command from
    another machine or an LM Studio folder still resolves), then by Hugging Face repository and
    quantization. When nothing matches, the result carries a repository guess and search text.
    """
    repo, _, quant = (hf_repo or "").partition(":")
    reference = requested_path or hf_file
    pure = _pure_path(reference) if reference else None
    if requested_path and Path(requested_path).is_file():
        resolved = Path(requested_path).resolve()
        match = next((model for model in candidates if model.primary_path == resolved), None)
        return _location("found", requested_path, resolved, match, repo or None, pure)
    if pure is not None:
        name = _primary_name(pure.name).casefold()
        match = next(
            (model for model in candidates if model.primary_path.name.casefold() == name), None
        )
        if match is not None:
            return _location("relocated", requested_path, match.primary_path, match, None, pure)
    if repo:
        match = next(_repository_matches(candidates, repo, quant), None)
        if match is not None:
            return _location("relocated", requested_path, match.primary_path, match, repo, pure)
    guess = repo or (_repository_from_path(pure) if pure is not None else None)
    return ModelLocation(
        status="missing",
        requested_path=requested_path,
        path=requested_path,
        download_id=None,
        repo_id=guess,
        search=_search_text(pure, guess),
    )


def _location(
    status: LocationStatus,
    requested_path: str | None,
    path: Path,
    match: LocalModel | None,
    repo: str | None,
    pure: PurePath | None,
) -> ModelLocation:
    repo_id = match.repo_id if match is not None and match.repo_id else repo
    return ModelLocation(
        status=status,
        requested_path=requested_path,
        path=str(path),
        download_id=match.download_id if match is not None else None,
        repo_id=repo_id,
        search=_search_text(pure or PurePath(path), repo_id),
    )


def _repository_matches(
    candidates: Iterable[LocalModel], repo: str, quant: str
) -> Iterator[LocalModel]:
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(quant)}(?![a-z0-9_])", re.IGNORECASE)
    for model in candidates:
        if model.repo_id is None or model.repo_id.casefold() != repo.casefold():
            continue
        if not quant or pattern.search(model.group_key or model.primary_path.name):
            yield model


def _pure_path(value: str) -> PurePath:
    """Parse a path from either platform, since commands are often copied between machines."""
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        return PureWindowsPath(value)
    return PurePosixPath(value)


def _primary_name(name: str) -> str:
    """Map any shard of a split model to the first shard llama-server must be given."""
    match = _SHARD_PATTERN.match(name)
    if match is None:
        return name
    return f"{match.group('prefix')}-00001-of-{match.group('count')}.gguf"


def _repository_from_path(path: PurePath) -> str | None:
    """Guess ``owner/name`` from Hugging Face cache or LM Studio folder layouts."""
    folders = path.parts[:-1]
    for folder in folders:
        if folder.startswith("models--"):
            owner, _, name = folder.removeprefix("models--").partition("--")
            if owner and name:
                return f"{owner}/{name}"
    lowered = [folder.casefold() for folder in folders]
    if "models" in lowered:
        index = len(lowered) - 1 - lowered[::-1].index("models")
        if len(folders) == index + 3:
            return f"{folders[index + 1]}/{folders[index + 2]}"
    return None


def _search_text(path: PurePath | None, repo_id: str | None) -> str:
    if path is None or not path.name:
        return repo_id or ""
    stem = path.name
    shard = _SHARD_PATTERN.match(stem)
    stem = shard.group("prefix") if shard else stem.removesuffix(".gguf")
    return _QUANT_SUFFIX.sub("", stem) or stem
