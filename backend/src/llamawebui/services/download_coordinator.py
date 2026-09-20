"""Coordinate download workers with durable job state."""

from __future__ import annotations

import asyncio

from llamawebui.domain.download_job import DownloadState
from llamawebui.models import DownloadJobRecord
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.download_worker import DownloadWorker


class DownloadCoordinator:
    def __init__(self, registry: DownloadRegistry, worker: DownloadWorker) -> None:
        self._registry = registry
        self._worker = worker
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start_pending(self) -> None:
        self._registry.reconcile_interrupted()
        for job in self._registry.list():
            if DownloadState(job.state) is DownloadState.QUEUED:
                self.start(job.id)

    def start(self, job_id: str) -> None:
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._worker.run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    async def cancel(self, job_id: str) -> DownloadJobRecord:
        record = self._registry.transition(job_id, DownloadState.CANCELLED)
        await self._stop_active(job_id)
        self._worker.discard_partial(job_id)
        return record

    async def pause(self, job_id: str) -> DownloadJobRecord:
        record = self._registry.transition(job_id, DownloadState.PAUSED)
        await self._stop_active(job_id)
        return record

    def resume(self, job_id: str) -> DownloadJobRecord:
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            raise ValueError("download cannot resume until the active transfer stops")
        record = self._registry.transition(job_id, DownloadState.QUEUED)
        self.start(job_id)
        return record

    def redownload(self, job_id: str) -> DownloadJobRecord:
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            raise ValueError("download cannot restart while a transfer is active")
        record = self._registry.reset_for_redownload(job_id)
        self.start(job_id)
        return record

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.items())
        for job_id, task in tasks:
            if not task.done():
                job = self._registry.get(job_id)
                if DownloadState(job.state) is DownloadState.DOWNLOADING:
                    self._registry.transition(job_id, DownloadState.PAUSED)
                task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

    async def _stop_active(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)