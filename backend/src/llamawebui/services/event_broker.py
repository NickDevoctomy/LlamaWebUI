"""Bounded event sequencing and replay for control-plane clients."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ControlEvent:
    id: int
    type: str
    data: dict[str, object]


class EventCursorError(ValueError):
    def __init__(self, requested: int, oldest: int, latest: int) -> None:
        self.requested = requested
        self.oldest = oldest
        self.latest = latest
        super().__init__(
            f"event cursor {requested} is unavailable; retained range is {oldest}..{latest}"
        )


class EventSubscription:
    def __init__(
        self,
        broker: EventBroker,
        replay: tuple[ControlEvent, ...],
        queue: asyncio.Queue[ControlEvent | None],
        cursor: int,
    ) -> None:
        self._broker = broker
        self._replay = deque(replay)
        self._queue = queue
        self._cursor = cursor
        self._closed = False

    def __aiter__(self) -> EventSubscription:
        return self

    async def __anext__(self) -> ControlEvent:
        if self._closed:
            raise StopAsyncIteration
        if self._replay:
            event = self._replay.popleft()
            self._cursor = event.id
            return event
        queued_event = await self._queue.get()
        if queued_event is None:
            self.close()
            raise EventCursorError(
                self._cursor,
                self._broker.oldest_id,
                self._broker.latest_id,
            )
        self._cursor = queued_event.id
        return queued_event

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._broker._unsubscribe(self._queue)


class EventBroker:
    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("event capacity must be positive")
        self._capacity = capacity
        self._events: deque[ControlEvent] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue[ControlEvent | None]] = set()
        self._latest_id = 0

    @property
    def latest_id(self) -> int:
        return self._latest_id

    @property
    def oldest_id(self) -> int:
        return self._events[0].id if self._events else self._latest_id + 1

    def publish(self, event_type: str, data: dict[str, object]) -> ControlEvent:
        if not event_type or "\r" in event_type or "\n" in event_type:
            raise ValueError("event type must be a non-empty single line")
        self._latest_id += 1
        event = ControlEvent(self._latest_id, event_type, data)
        self._events.append(event)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
        return event

    def subscribe(self, after_id: int | None = None) -> EventSubscription:
        if after_id is not None:
            self._validate_cursor(after_id)
        replay = (
            tuple(event for event in self._events if event.id > after_id)
            if after_id is not None
            else ()
        )
        queue: asyncio.Queue[ControlEvent | None] = asyncio.Queue(self._capacity)
        self._subscribers.add(queue)
        cursor = after_id if after_id is not None else self.latest_id
        return EventSubscription(self, replay, queue, cursor)

    def _validate_cursor(self, after_id: int) -> None:
        if after_id < 0 or after_id > self._latest_id:
            raise EventCursorError(after_id, self.oldest_id, self.latest_id)
        if self._events and after_id < self.oldest_id - 1:
            raise EventCursorError(after_id, self.oldest_id, self.latest_id)

    def _unsubscribe(self, queue: asyncio.Queue[ControlEvent | None]) -> None:
        self._subscribers.discard(queue)