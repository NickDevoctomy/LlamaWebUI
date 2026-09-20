"""Project validated completed downloads into usable local models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.download_job import DownloadState
from llamawebui.models import DownloadJobRecord
from llamawebui.services.download_registry import DownloadRegistry

_SHARD_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LibraryModel:
    download_id: str
    repo_id: str
    revision: str
    group_key: str
    primary_path: Path
    file_count: int
    total_bytes: int


class ModelLibrary:
    def __init__(self, downloads: DownloadRegistry, model_root: Path) -> None:
        self._downloads = downloads
        self._model_root = model_root.resolve()

    def list(self) -> tuple[LibraryModel, ...]:
        models = (
            model
            for job in self._downloads.list()
            if DownloadState(job.state) is DownloadState.COMPLETED
            if (model := self._project(job)) is not None
        )
        return tuple(models)

    def _project(self, job: DownloadJobRecord) -> LibraryModel | None:
        destination = Path(job.destination).resolve()
        if not destination.is_relative_to(self._model_root):
            return None

        primary: Path | None = None
        for file_data in job.files:
            raw_path = file_data.get("path")
            expected_size = file_data.get("size")
            if not isinstance(raw_path, str) or not isinstance(expected_size, int):
                return None
            relative_path = Path(raw_path)
            if relative_path.is_absolute() or any(
                part in {".", ".."} for part in relative_path.parts
            ):
                return None
            file_path = (destination / relative_path).resolve()
            if (
                not file_path.is_relative_to(destination)
                or not file_path.is_file()
                or file_path.stat().st_size != expected_size
            ):
                return None
            if file_path.suffix.lower() != ".gguf":
                continue
            shard = _SHARD_PATTERN.match(file_path.name)
            if shard is None or shard.group("index") == "00001":
                primary = primary or file_path

        if primary is None:
            return None
        return LibraryModel(
            download_id=job.id,
            repo_id=job.repo_id,
            revision=job.revision,
            group_key=job.group_key,
            primary_path=primary,
            file_count=len(job.files),
            total_bytes=job.total_bytes,
        )