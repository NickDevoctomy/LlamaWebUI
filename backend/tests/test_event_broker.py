from __future__ import annotations

import asyncio

import pytest

from llamawebui.services.event_broker import EventBroker, EventCursorError

pytestmark = pytest.mark.asyncio


async def test_broker_sequences_replays_and_delivers_live_events() -> None:
    broker = EventBroker(capacity=3)
    first = broker.publish("router.state", {"state": "starting"})
    second = broker.publish("router.state", {"state": "ready"})
    subscription = broker.subscribe(after_id=first.id)

    assert (await anext(subscription)) == second
    third = broker.publish("router.model_status", {"model": "local-model"})
    assert (await anext(subscription)) == third
    assert [first.id, second.id, third.id] == [1, 2, 3]
    subscription.close()


async def test_new_subscription_receives_only_future_events_without_cursor() -> None:
    broker = EventBroker()
    broker.publish("past", {})
    subscription = broker.subscribe()
    future = broker.publish("future", {})

    assert await anext(subscription) == future
    subscription.close()


@pytest.mark.parametrize("cursor", (-1, 4))
async def test_broker_rejects_invalid_cursor(cursor: int) -> None:
    broker = EventBroker(capacity=2)
    for value in range(3):
        broker.publish("test", {"value": value})

    with pytest.raises(EventCursorError) as error:
        broker.subscribe(after_id=cursor)

    assert error.value.oldest == 2
    assert error.value.latest == 3


async def test_broker_rejects_cursor_older_than_retained_history() -> None:
    broker = EventBroker(capacity=2)
    for value in range(3):
        broker.publish("test", {"value": value})

    with pytest.raises(EventCursorError, match="retained range is 2..3"):
        broker.subscribe(after_id=0)


async def test_slow_subscriber_is_closed_instead_of_skipping_events() -> None:
    broker = EventBroker(capacity=2)
    subscription = broker.subscribe()
    broker.publish("test", {"value": 1})
    broker.publish("test", {"value": 2})
    broker.publish("test", {"value": 3})

    with pytest.raises(EventCursorError) as error:
        await anext(subscription)
    assert error.value.requested == 0


async def test_closed_subscription_stops_and_does_not_receive_events() -> None:
    broker = EventBroker()
    subscription = broker.subscribe()
    subscription.close()
    broker.publish("test", {})

    with pytest.raises(StopAsyncIteration):
        await anext(subscription)


async def test_broker_validates_capacity_and_event_type() -> None:
    with pytest.raises(ValueError, match="capacity"):
        EventBroker(0)
    broker = EventBroker()
    with pytest.raises(ValueError, match="single line"):
        broker.publish("bad\nevent", {})


async def test_subscription_wait_can_be_cancelled_cleanly() -> None:
    broker = EventBroker()
    subscription = broker.subscribe()
    pending = asyncio.create_task(anext(subscription))
    await asyncio.sleep(0)
    pending.cancel()

    with pytest.raises(asyncio.CancelledError):
        await pending
    subscription.close()