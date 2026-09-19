import httpx
import pytest

from llamawebui.services import router_client
from llamawebui.services.router_client import HttpRouterClient, RouterAPIError

pytestmark = pytest.mark.asyncio


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