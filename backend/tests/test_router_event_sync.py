from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from llamawebui.services.event_broker import EventBroker
from llamawebui.services.router_client import RouterAPIError, RouterModel, RouterModelEvent
from llamawebui.services.router_event_sync import RouterEventSynchronizer

pytestmark = pytest.mark.asyncio


class FakeRouterClient:
    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()

    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]:
        return ()

    async def load_model(self, model: str) -> None:
        pass

    async def unload_model(self, model: str) -> None:
        pass

    async def model_events(self) -> AsyncIterator[RouterModelEvent]:
        self.calls += 1
        yield RouterModelEvent("local-model", "model_status", {"status": "loaded"})
        await self.release.wait()


async def test_synchronizer_publishes_native_events_once_and_stops() -> None:
    client = FakeRouterClient()
    broker = EventBroker()
    subscription = broker.subscribe()
    synchronizer = RouterEventSynchronizer(client, broker, reconnect_delay_seconds=0)

    synchronizer.start()
    synchronizer.start()
    event = await asyncio.wait_for(anext(subscription), 1)
    await synchronizer.shutdown()

    assert client.calls == 1
    assert event.type == "router.model.model_status"
    assert event.data == {"model": "local-model", "status": "loaded"}
    subscription.close()


async def test_synchronizer_publishes_sanitized_error_and_reconnects() -> None:
    class FailingClient(FakeRouterClient):
        async def model_events(self) -> AsyncIterator[RouterModelEvent]:
            self.calls += 1
            if self.calls == 1:
                raise RouterAPIError(503, "native unavailable")
            yield RouterModelEvent("local-model", "model_status", {"status": "loaded"})
            await self.release.wait()

    client = FailingClient()
    broker = EventBroker()
    subscription = broker.subscribe()
    synchronizer = RouterEventSynchronizer(client, broker, reconnect_delay_seconds=0)

    synchronizer.start()
    error = await asyncio.wait_for(anext(subscription), 1)
    recovered = await asyncio.wait_for(anext(subscription), 1)
    await synchronizer.shutdown()

    assert error.type == "router.model_stream_error"
    assert error.data == {"code": 502, "message": "native unavailable"}
    assert recovered.type == "router.model.model_status"
    assert client.calls == 2
    subscription.close()


async def test_synchronizer_validates_delay_and_deactivates() -> None:
    with pytest.raises(ValueError, match="delay"):
        RouterEventSynchronizer(FakeRouterClient(), EventBroker(), reconnect_delay_seconds=-1)

    client = FakeRouterClient()
    synchronizer = RouterEventSynchronizer(client, EventBroker())
    synchronizer.start()
    await asyncio.sleep(0)
    synchronizer.deactivate()
    await synchronizer.shutdown()

    assert client.calls == 1


async def test_synchronizer_can_restart_before_cancelled_task_unwinds() -> None:
    client = FakeRouterClient()
    broker = EventBroker()
    synchronizer = RouterEventSynchronizer(client, broker)

    synchronizer.start()
    await asyncio.sleep(0)
    synchronizer.deactivate()
    synchronizer.start()
    await asyncio.sleep(0)
    await synchronizer.shutdown()

    assert client.calls == 2