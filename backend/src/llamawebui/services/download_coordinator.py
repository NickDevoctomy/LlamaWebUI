"""Coordinate download workers with durable job state."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from llamawebui.domain.download_job import DownloadState
from llamawebui.models import DownloadJobRecord
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.download_worker import DownloadWorker
from llamawebui.services.event_broker import EventBroker

LOGGER = logging.getLogger("llamawebui.download")


class DownloadCoordinator:
    def __init__(
        self,
        registry: DownloadRegistry,
        worker: DownloadWorker,
        *,
        event_broker: EventBroker | None = None,
        on_completed: Callable[[str], object] | None = None,
    ) -> None:
        self._registry = registry
        self._worker = worker
        self._event_broker = event_broker
        self._on_completed = on_completed
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
        LOGGER.info(
            "download_started",
            extra={"component": "download", "event": "started", "job_id": job_id},
        )
        self._publish(job_id, "started")
        task.add_done_callback(lambda completed: self._task_finished(job_id, completed))

    async def cancel(self, job_id: str) -> DownloadJobRecord:
        record = self._registry.transition(job_id, DownloadState.CANCELLED)
        await self._stop_active(job_id)
        self._worker.discard_partial(job_id)
        self._publish(job_id, "cancelled")
        return record

    def clear_finished(self) -> int:
        cleared = self._registry.clear_terminal()
        for record in cleared:
            # Failed jobs keep their staging directory for retry; clearing gives that up.
            if DownloadState(record.state) is DownloadState.FAILED:
                self._worker.discard_partial(record.id)
        return len(cleared)

    def _publish(self, job_id: str, action: str) -> None:
        if self._event_broker is not None:
            job = self._registry.get(job_id)
            self._event_broker.publish(
                "download." + action,
                {
                    "job_id": job_id,
                    "state": job.state,
                    "completed_bytes": job.completed_bytes,
                    "total_bytes": job.total_bytes,
                },
            )

    def _task_finished(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(job_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "download_task_failed",
                extra={
                    "component": "download",
                    "event": "task_failed",
                    "job_id": job_id,
                    "error": str(error),
                },
            )
            try:
                current_state = DownloadState(self._registry.get(job_id).state)
                if current_state is DownloadState.QUEUED:
                    self._registry.transition(job_id, DownloadState.DOWNLOADING)
                failed = self._registry.fail(job_id, str(error))
                if self._event_broker is not None and failed.state == DownloadState.FAILED:
                    self._publish(job_id, DownloadState.FAILED.value)
            except Exception as failure:
                LOGGER.error(
                    "download_task_failure_persist_failed",
                    extra={
                        "component": "download",
                        "event": "task_failure_persist_failed",
                        "job_id": job_id,
                        "error": str(failure),
                    },
                )
            return
        state = DownloadState(self._registry.get(job_id).state)
        if state is DownloadState.COMPLETED and self._on_completed is not None:
            try:
                self._on_completed(job_id)
            except Exception as failure:
                LOGGER.error(
                    "download_completion_hook_failed",
                    extra={
                        "component": "download",
                        "event": "completion_hook_failed",
                        "job_id": job_id,
                        "error": str(failure),
                    },
                )
        if self._event_broker is None:
            return
        if state in {DownloadState.COMPLETED, DownloadState.FAILED}:
            self._publish(job_id, state.value)

    async def pause(self, job_id: str) -> DownloadJobRecord:
        record = self._registry.transition(job_id, DownloadState.PAUSED)
        await self._stop_active(job_id)
        self._publish(job_id, "paused")
        return record

    def resume(self, job_id: str) -> DownloadJobRecord:
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            raise ValueError("download cannot resume until the active transfer stops")
        record = self._registry.transition(job_id, DownloadState.QUEUED)
        self.start(job_id)
        self._publish(job_id, "resumed")
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