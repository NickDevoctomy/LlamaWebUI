"""Inspect and safely remove application-managed model artifacts."""

from __future__ import annotations

import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.download_job import DownloadState
from llamawebui.models import DownloadJobRecord, ModelProfileRecord
from llamawebui.services.download_registry import DownloadRegistry


class ModelArtifactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    download_id: str
    repo_id: str
    revision: str
    group_key: str
    file_count: int
    total_bytes: int


class ModelArtifactRegistry:
    def __init__(self, downloads: DownloadRegistry, model_root: Path) -> None:
        self._downloads = downloads
        self._model_root = model_root.resolve()

    def source_for_profile(self, profile: ModelProfileRecord) -> ArtifactSource | None:
        matches = self._matching_jobs(profile)
        valid = next((job for job in matches if self.is_valid(job)), None)
        completed = next(
            (job for job in matches if DownloadState(job.state) is DownloadState.COMPLETED),
            None,
        )
        selected = valid or completed or next(iter(matches), None)
        return self._source(selected) if selected is not None else None

    def profile_available(self, profile: ModelProfileRecord) -> bool:
        matches = self._matching_jobs(profile)
        if not matches:
            return Path(profile.model_path).is_file()
        return any(self.is_valid(job) for job in matches)

    def is_valid(self, job: DownloadJobRecord) -> bool:
        if DownloadState(job.state) is not DownloadState.COMPLETED:
            return False
        destination = self._safe_destination(job)
        expected = self._expected_paths(job, destination)
        return all(path.is_file() and path.stat().st_size == size for path, size in expected)

    def delete(self, download_id: str) -> DownloadJobRecord:
        job = self._downloads.get(download_id)
        if DownloadState(job.state) is not DownloadState.COMPLETED:
            raise ModelArtifactError("only completed model artifacts can be deleted")
        destination = self._safe_destination(job)
        expected = self._expected_paths(job, destination)
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ModelArtifactError("model artifact destination is unsafe")
            tombstone = destination.parent / f".{destination.name}.{job.id}.deleting"
            if tombstone.exists():
                raise ModelArtifactError("model artifact deletion is already in progress")
            moved: list[tuple[Path, Path]] = []
            try:
                for path, _ in expected:
                    if not path.exists():
                        continue
                    relative = path.relative_to(destination)
                    deleted = tombstone / relative
                    deleted.parent.mkdir(parents=True, exist_ok=True)
                    path.replace(deleted)
                    moved.append((path, deleted))
            except OSError:
                for original, deleted in reversed(moved):
                    original.parent.mkdir(parents=True, exist_ok=True)
                    deleted.replace(original)
                shutil.rmtree(tombstone, ignore_errors=True)
                raise
            shutil.rmtree(tombstone, ignore_errors=True)
            for parent in sorted(
                {path.parent for path, _ in expected},
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                if parent == destination:
                    continue
                with suppress(OSError):
                    parent.rmdir()
            with suppress(OSError):
                destination.rmdir()
        return job

    def _matching_jobs(self, profile: ModelProfileRecord) -> tuple[DownloadJobRecord, ...]:
        profile_path = Path(profile.model_path).resolve()
        return tuple(
            job
            for job in self._downloads.list(include_hidden=True)
            if self.primary_path(job) == profile_path
        )

    def primary_path(self, job: DownloadJobRecord) -> Path | None:
        destination = self._safe_destination(job)
        for path, _ in self._expected_paths(job, destination):
            if path.suffix.lower() != ".gguf":
                continue
            name = path.name.lower()
            if "-of-" not in name or "-00001-of-" in name:
                return path
        return None

    def _safe_destination(self, job: DownloadJobRecord) -> Path:
        destination = Path(job.destination).resolve()
        if not destination.is_relative_to(self._model_root) or destination == self._model_root:
            raise ModelArtifactError("model artifact destination escapes the managed root")
        return destination

    @staticmethod
    def _expected_paths(
        job: DownloadJobRecord, destination: Path
    ) -> tuple[tuple[Path, int], ...]:
        expected: list[tuple[Path, int]] = []
        for file_data in job.files:
            raw_path = file_data.get("path")
            size = file_data.get("size")
            if not isinstance(raw_path, str) or not isinstance(size, int):
                raise ModelArtifactError("model artifact metadata is invalid")
            relative = Path(raw_path)
            if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
                raise ModelArtifactError("model artifact path is unsafe")
            path = (destination / relative).resolve()
            if not path.is_relative_to(destination):
                raise ModelArtifactError("model artifact path escapes its destination")
            expected.append((path, size))
        return tuple(expected)

    @staticmethod
    def _source(job: DownloadJobRecord) -> ArtifactSource:
        return ArtifactSource(
            download_id=job.id,
            repo_id=job.repo_id,
            revision=job.revision,
            group_key=job.group_key,
            file_count=len(job.files),
            total_bytes=job.total_bytes,
        )
