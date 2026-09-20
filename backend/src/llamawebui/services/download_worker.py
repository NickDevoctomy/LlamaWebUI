"""Execute revision-pinned Hugging Face download jobs."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Protocol

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
        request = json.dumps(
            {
                "repo_id": repo_id,
                "filename": filename,
                "revision": revision,
                "destination": str(destination),
                "token": self._token,
            }
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "llamawebui.services.huggingface_transfer_process",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await process.communicate(request.encode())
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        try:
            response = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OSError("Hugging Face transfer returned an invalid response") from error
        if process.returncode != 0 or not isinstance(response.get("path"), str):
            message = response.get("error")
            raise OSError(message if isinstance(message, str) else "Hugging Face transfer failed")
        return Path(response["path"])


class DownloadWorker:
    def __init__(self, registry: DownloadRegistry, transfer: FileTransfer) -> None:
        self._registry = registry
        self._transfer = transfer

    async def run(self, job_id: str) -> None:
        job = self._registry.transition(job_id, DownloadState.DOWNLOADING)
        destination = Path(job.destination)
        staging = self.staging_path(job_id)
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
            if DownloadState(self._registry.get(job_id).state) is DownloadState.CANCELLED:
                shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as error:
            self._registry.fail(job_id, str(error))

    def staging_path(self, job_id: str) -> Path:
        job = self._registry.get(job_id)
        destination = Path(job.destination)
        return destination.parent / f".{destination.name}.{job.id}.partial"

    def discard_partial(self, job_id: str) -> None:
        shutil.rmtree(self.staging_path(job_id), ignore_errors=True)

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
                await asyncio.gather(transfer, return_exceptions=True)