"""Synchronize native llama.cpp model events into the control-plane broker."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from llamawebui.services.event_broker import EventBroker
from llamawebui.services.router_client import RouterAPIError, RouterClient


class RouterEventSynchronizer:
    def __init__(
        self,
        client: RouterClient,
        broker: EventBroker,
        *,
        reconnect_delay_seconds: float = 1.0,
    ) -> None:
        if reconnect_delay_seconds < 0:
            raise ValueError("event reconnect delay must not be negative")
        self._client = client
        self._broker = broker
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._task: asyncio.Task[None] | None = None
        self._retired_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            self._task.add_done_callback(self._retired_tasks.discard)

    def deactivate(self) -> None:
        if self._task is not None:
            task = self._task
            self._task = None
            self._retired_tasks.add(task)
            task.cancel()

    async def shutdown(self) -> None:
        tasks = set(self._retired_tasks)
        if self._task is not None:
            tasks.add(self._task)
        self._task = None
        self._retired_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            try:
                async for event in self._client.model_events():
                    self._broker.publish(
                        f"router.model.{event.event}",
                        {"model": event.model, **event.data},
                    )
            except RouterAPIError as error:
                self._broker.publish(
                    "router.model_stream_error",
                    {
                        "code": error.status_code if 400 <= error.status_code < 500 else 502,
                        "message": str(error),
                    },
                )
            await asyncio.sleep(self._reconnect_delay_seconds)