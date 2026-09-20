"""Execute revision-pinned Hugging Face download jobs."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Protocol

from huggingface_hub import hf_hub_download

from llamawebui.domain.download_job import DownloadState
from llamawebui.services.download_registry import DownloadRegistry

_PROGRESS_INTERVAL_SECONDS = 0.25


class FileTransfer(Protocol):
    async def download(
        self, *, repo_id: str, filename: str, revision: str, destination: Path
    ) -> Path: ...


class HuggingFaceFileTransfer:
    def __init__(self, token: str | None = None) -> None:
        self._token = token

    async def download(
        self, *, repo_id: str, filename: str, revision: str, destination: Path
    ) -> Path:
        downloaded = await asyncio.to_thread(
            hf_hub_download,
            repo_id=repo_id,
            filename=filename,
            revision=revision,
            local_dir=destination,
            token=self._token,
        )
        return Path(downloaded)


class DownloadWorker:
    def __init__(self, registry: DownloadRegistry, transfer: FileTransfer) -> None:
        self._registry = registry
        self._transfer = transfer

    async def run(self, job_id: str) -> None:
        job = self._registry.transition(job_id, DownloadState.DOWNLOADING)
        destination = Path(job.destination)
        staging = destination.parent / f".{destination.name}.{job.id}.partial"
        completed_bytes = 0
        try:
            for file_data in job.files:
                if DownloadState(self._registry.get(job_id).state) is DownloadState.CANCELLED:
                    return
                filename = str(file_data["path"])
                expected_size = file_data["size"]
                if not isinstance(expected_size, int):
                    raise OSError(f"download file size is invalid: {filename}")
                downloaded = staging / filename
                if not downloaded.is_file() or downloaded.stat().st_size != expected_size:
                    downloaded = await self._download_with_progress(
                        job_id=job_id,
                        repo_id=job.repo_id,
                        filename=filename,
                        revision=job.revision,
                        destination=staging,
                        completed_bytes=completed_bytes,
                        expected_size=expected_size,
                    )
                current_state = DownloadState(self._registry.get(job_id).state)
                if current_state is not DownloadState.DOWNLOADING:
                    if current_state is DownloadState.CANCELLED:
                        shutil.rmtree(staging, ignore_errors=True)
                    return
                if not downloaded.resolve().is_relative_to(staging.resolve()):
                    raise OSError(f"downloaded file is outside the staging directory: {filename}")
                if not downloaded.is_file():
                    raise OSError(f"downloaded file is missing: {filename}")
                actual_size = downloaded.stat().st_size
                if expected_size is not None and actual_size != expected_size:
                    raise OSError(
                        f"downloaded file size mismatch: {filename} "
                        f"(expected {expected_size}, got {actual_size})"
                    )
                completed_bytes += actual_size
                if completed_bytes > job.completed_bytes:
                    job = self._registry.update_progress(job_id, completed_bytes)

            if DownloadState(self._registry.get(job_id).state) is DownloadState.DOWNLOADING:
                if destination.exists():
                    raise FileExistsError(f"download destination already exists: {destination}")
                staging.replace(destination)
                self._registry.transition(job_id, DownloadState.COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._registry.fail(job_id, str(error))

    async def _download_with_progress(
        self,
        *,
        job_id: str,
        repo_id: str,
        filename: str,
        revision: str,
        destination: Path,
        completed_bytes: int,
        expected_size: int,
    ) -> Path:
        transfer = asyncio.create_task(
            self._transfer.download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                destination=destination,
            )
        )
        target = destination / filename
        cache_directory = destination / ".cache" / "huggingface" / "download"
        reported_bytes = self._registry.get(job_id).completed_bytes
        try:
            while not transfer.done():
                await asyncio.sleep(_PROGRESS_INTERVAL_SECONDS)
                incomplete_bytes = max(
                    (
                        path.stat().st_size
                        for path in cache_directory.rglob("*.incomplete")
                        if path.is_file()
                    ),
                    default=0,
                )
                current_file_bytes = max(
                    target.stat().st_size if target.is_file() else 0,
                    incomplete_bytes,
                )
                next_bytes = completed_bytes + min(current_file_bytes, expected_size)
                if next_bytes > reported_bytes:
                    updated = self._registry.update_progress(job_id, next_bytes)
                    reported_bytes = updated.completed_bytes
            return await transfer
        finally:
            if not transfer.done():
                transfer.cancel()