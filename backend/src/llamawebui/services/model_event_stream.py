"""Bounded, cancellable streaming of native model events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import cast

from llamawebui.services.router_client import RouterAPIError, RouterClient, RouterModelEvent


async def stream_model_events(
    client: RouterClient, *, keepalive_seconds: float
) -> AsyncIterator[str]:
    events = client.model_events().__aiter__()
    end = object()
    pending: asyncio.Future[RouterModelEvent | object] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events, end))
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(pending), timeout=keepalive_seconds
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            except RouterAPIError as error:
                pending = None
                status_code = error.status_code if 400 <= error.status_code < 500 else 502
                yield _error_event(status_code, str(error))
                break
            if result is end:
                break
            pending = None
            yield _event_sse(cast(RouterModelEvent, result))
    except RouterAPIError as error:
        status_code = error.status_code if 400 <= error.status_code < 500 else 502
        yield _error_event(status_code, str(error))
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, RouterAPIError):
                await pending
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()


def _event_sse(event: RouterModelEvent) -> str:
    payload = json.dumps(
        {"model": event.model, "event": event.event, "data": event.data},
        separators=(",", ":"),
    )
    return f"event: {event.event}\ndata: {payload}\n\n"


def _error_event(status_code: int, message: str) -> str:
    payload = json.dumps(
        {
            "model": "*",
            "event": "error",
            "data": {"code": status_code, "message": message},
        },
        separators=(",", ":"),
    )
    return f"event: error\ndata: {payload}\n\n"