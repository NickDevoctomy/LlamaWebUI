from collections.abc import AsyncIterator

import httpx
import pytest

from llamawebui.services import router_client
from llamawebui.services.router_client import (
    HttpRouterClient,
    RouterAPIError,
    collect_model_events,
)

pytestmark = pytest.mark.asyncio


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def install_transport(
    monkeypatch: pytest.MonkeyPatch, handler: httpx.AsyncBaseTransport
) -> None:
    original = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return original(transport=handler, **kwargs)

    monkeypatch.setattr(router_client.httpx, "AsyncClient", client_factory)


async def test_router_client_lists_models_and_normalizes_wildcard_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:1234/models?reload=1"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "local-model",
                        "path": "C:/models/model.gguf",
                        "status": {"value": "loaded", "args": ["llama-server"]},
                        "architecture": {"input_modalities": ["text"]},
                    }
                ]
            },
        )

    install_transport(monkeypatch, httpx.MockTransport(handler))
    models = await HttpRouterClient("0.0.0.0", 1234).list_models(reload=True)

    assert models[0].id == "local-model"
    assert models[0].status["value"] == "loaded"
    assert "architecture" in models[0].metadata


async def test_router_client_loads_and_unloads_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.headers.get("authorization")))
        assert request.content == b'{"model":"local-model"}'
        return httpx.Response(200, json={"success": True})

    install_transport(monkeypatch, httpx.MockTransport(handler))
    client = HttpRouterClient("::", 1234, api_key="secret")
    await client.load_model("local-model")
    await client.unload_model("local-model")

    assert requests == [
        ("POST", "/models/load", "Bearer secret"),
        ("POST", "/models/unload", "Bearer secret"),
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    (
        (httpx.Response(500, json={"error": {"message": "load failed"}}), "load failed"),
        (httpx.Response(200, text="not json"), "invalid JSON"),
        (httpx.Response(200, json=[]), "invalid response"),
        (httpx.Response(200, json={"data": [{}]}), "invalid model entry"),
        (
            httpx.Response(200, json={"data": [{"id": "model", "status": {}}]}),
            "invalid model status",
        ),
    ),
)
async def test_router_client_reports_invalid_responses(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, message: str
) -> None:
    install_transport(monkeypatch, httpx.MockTransport(lambda request: response))
    client = HttpRouterClient("127.0.0.1", 1234)

    with pytest.raises(RouterAPIError, match=message):
        await client.list_models()


async def test_router_client_rejects_unconfirmed_and_empty_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch, httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    client = HttpRouterClient("127.0.0.1", 1234)

    with pytest.raises(RouterAPIError, match="did not confirm"):
        await client.load_model("model")
    with pytest.raises(ValueError, match="must not be empty"):
        await client.unload_model(" ")


async def test_router_client_parses_native_model_event_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ChunkedStream(
        (
            b": keepalive\n\ndata: {\"model\":\"local-model\",\n",
            b'data: "event":"model_status","data":{"status":"loading"}}\n\n',
            b"event: ignored-upstream-field\n",
            b'data: {"model":"local-model","event":"download_progress",',
            b'"data":{"file":{"done":5,"total":10}}}',
        )
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models/sse"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    install_transport(monkeypatch, httpx.MockTransport(handler))
    events = [event async for event in HttpRouterClient("127.0.0.1", 1234).model_events()]

    assert [(event.model, event.event) for event in events] == [
        ("local-model", "model_status"),
        ("local-model", "download_progress"),
    ]
    assert events[0].data == {"status": "loading"}
    assert events[1].data["file"] == {"done": 5, "total": 10}


@pytest.mark.parametrize(
    "payload",
    (
        b"data: not-json\n\n",
        b"data: []\n\n",
        b'data: {"model":"local-model","event":"model_status"}\n\n',
        b'data: {"model":"","event":"model_status","data":{}}\n\n',
        b'data: {"model":"local-model","event":"bad\\nevent","data":{}}\n\n',
    ),
)
async def test_router_client_rejects_invalid_model_events(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=ChunkedStream((payload,)),
            )
        ),
    )

    with pytest.raises(RouterAPIError, match="invalid model event"):
        _ = [event async for event in HttpRouterClient("127.0.0.1", 1234).model_events()]


async def test_router_client_reports_model_event_stream_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={"error": {"message": "router unavailable"}},
            )
        ),
    )

    with pytest.raises(RouterAPIError, match="router unavailable") as error:
        _ = [event async for event in HttpRouterClient("127.0.0.1", 1234).model_events()]
    assert error.value.status_code == 503


async def test_closing_model_event_iterator_closes_upstream_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ChunkedStream(
        (b'data: {"model":"model","event":"model_status","data":{}}\n\n',)
    )
    install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )
        ),
    )
    events = HttpRouterClient("127.0.0.1", 1234).model_events()

    assert (await anext(events)).event == "model_status"
    assert not stream.closed
    await events.aclose()

    assert stream.closed


async def test_collect_model_events_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = ChunkedStream(
        (b'data: {"model":"model","event":"model_status","data":{}}\n\n',)
    )
    install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )
        ),
    )
    events = await collect_model_events(
        HttpRouterClient("127.0.0.1", 1234), limit=1, timeout_seconds=1
    )

    assert len(events) == 1
    assert events[0].event == "model_status"


async def test_router_client_rejects_non_event_stream_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        httpx.MockTransport(lambda request: httpx.Response(200, text="not an event stream")),
    )

    with pytest.raises(RouterAPIError, match="invalid model event stream"):
        _ = [event async for event in HttpRouterClient("127.0.0.1", 1234).model_events()]