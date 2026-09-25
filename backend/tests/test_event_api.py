from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.services.event_broker import EventBroker

pytestmark = pytest.mark.asyncio

EventEndpoint = Callable[[Request, int | None], Awaitable[StreamingResponse]]


def find_event_endpoint(app: FastAPI) -> EventEndpoint:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == "/api/events":
            return cast(EventEndpoint, route.endpoint)
    raise AssertionError("event endpoint is not registered")


def event_request(app: FastAPI, last_event_id: str | None = None) -> Request:
    headers = [] if last_event_id is None else [(b"last-event-id", last_event_id.encode())]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events",
            "headers": headers,
            "app": app,
        }
    )


async def test_event_endpoint_replays_then_delivers_live_events(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    endpoint = find_event_endpoint(app)

    async with app.router.lifespan_context(app):
        broker = cast(EventBroker, app.state.event_broker)
        broker.publish("router.state", {"state": "starting"})
        second = broker.publish("router.state", {"state": "ready"})
        response = await endpoint(event_request(app, "1"), None)
        stream = cast(AsyncGenerator[str, None], response.body_iterator)

        assert await anext(stream) == (
            f"id: {second.id}\nevent: router.state\n"
            'data: {"state":"ready"}\n\n'
        )
        third = broker.publish("router.model.model_status", {"status": "loaded"})
        assert await anext(stream) == (
            f"id: {third.id}\nevent: router.model.model_status\n"
            'data: {"status":"loaded"}\n\n'
        )
        await stream.aclose()


async def test_event_endpoint_rejects_invalid_and_stale_cursors(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", event_history_capacity=2))
    endpoint = find_event_endpoint(app)

    async with app.router.lifespan_context(app):
        broker = cast(EventBroker, app.state.event_broker)
        for value in range(3):
            broker.publish("test", {"value": value})

        with pytest.raises(HTTPException) as stale:
            await endpoint(event_request(app), 0)
        with pytest.raises(HTTPException) as malformed:
            await endpoint(event_request(app, "abc"), None)
        with pytest.raises(HTTPException) as conflicting:
            await endpoint(event_request(app, "2"), 1)

    assert stale.value.status_code == 409
    assert stale.value.detail["reconcile"] == "/api/server/status"
    assert stale.value.detail["oldest_event_id"] == 2
    assert malformed.value.status_code == 400
    assert conflicting.value.status_code == 400


async def test_event_endpoint_terminates_slow_subscriber_with_reconcile(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", event_history_capacity=1))
    endpoint = find_event_endpoint(app)

    async with app.router.lifespan_context(app):
        broker = cast(EventBroker, app.state.event_broker)
        response = await endpoint(event_request(app), None)
        stream = cast(AsyncGenerator[str, None], response.body_iterator)
        broker.publish("test", {"value": 1})
        broker.publish("test", {"value": 2})

        event = await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    assert event.startswith("event: reconcile\ndata: ")
    assert '"reconcile":"/api/server/status"' in event
    assert '"oldest_event_id":2' in event