from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import Login
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.services.authorization import privilege_for_request
from llamawebui.services.openrouter_catalog import (
    OPENROUTER_MODELS_URL,
    OpenRouterCatalog,
    OpenRouterUnavailableError,
)
from llamawebui.services.secret_store import SecretName, SecretStore

LOCAL = ("127.0.0.1", 50000)
MODELS = {
    "data": [
        {
            "id": "anthropic/claude-sonnet-5.5",
            "name": "Anthropic: Claude Sonnet 5.5",
            "description": "Balanced model.",
            "context_length": 1000000,
            "pricing": {
                "prompt": "0.000003",
                "completion": "0.000015",
                "input_cache_read": "0.0000003",
                "overrides": {"ignored": True},
            },
        },
        {
            "id": "qwen/qwen3.8-flash:free",
            "name": "Qwen: Qwen3.8 Flash (free)",
            "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"name": "no id, skipped"},
    ]
}


def _catalog(requests: list[httpx.Request] | None = None, status: int = 200) -> OpenRouterCatalog:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, json=MODELS)

    return OpenRouterCatalog(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_catalog_parses_models_and_caches_the_list() -> None:
    requests: list[httpx.Request] = []
    catalog = _catalog(requests)

    models = await catalog.models()
    await catalog.models()

    assert [model.id for model in models] == [
        "anthropic/claude-sonnet-5.5",
        "qwen/qwen3.8-flash:free",
        "openrouter/auto",
    ]
    assert models[0].pricing == {
        "prompt": "0.000003",
        "completion": "0.000015",
        "input_cache_read": "0.0000003",
    }
    assert models[1].description == ""
    assert models[2].name == "openrouter/auto"
    assert models[2].context_length is None
    assert len(requests) == 1
    assert str(requests[0].url) == OPENROUTER_MODELS_URL
    assert "authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_catalog_search_matches_every_word_in_id_or_name() -> None:
    catalog = _catalog()

    assert [model.id for model in (await catalog.search("qwen free"))[0]] == [
        "qwen/qwen3.8-flash:free"
    ]
    assert (await catalog.search("SONNET"))[1] == 1
    models, total = await catalog.search("", limit=2)
    assert len(models) == 2
    assert total == 3
    assert await catalog.get("openrouter/auto") is not None
    assert await catalog.get("missing/model") is None


@pytest.mark.asyncio
async def test_catalog_falls_back_to_the_cached_list_then_reports_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=MODELS) if calls == 1 else httpx.Response(502)

    catalog = OpenRouterCatalog(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), cache_ttl_seconds=0
    )
    assert len(await catalog.models()) == 3
    assert len(await catalog.models()) == 3

    with pytest.raises(OpenRouterUnavailableError):
        await _catalog(status=502).models()


@pytest.mark.asyncio
async def test_catalog_rejects_an_invalid_payload() -> None:
    catalog = OpenRouterCatalog(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        )
    )

    with pytest.raises(OpenRouterUnavailableError):
        await catalog.models()


def test_a_model_is_selected_with_its_pricing_and_the_assistant_becomes_available(
    tmp_path: Path, login: Login
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"), openrouter_catalog=_catalog())
    with TestClient(app, client=LOCAL) as client:
        login(client)
        privileges = set(client.get("/api/auth/me").json()["privileges"])
        initial = client.get("/api/assistant/status").json()
        search = client.get("/api/settings/openrouter/models", params={"q": "claude"}).json()
        chosen = search["models"][0]["id"]
        selected = client.put("/api/settings/openrouter/model", json={"id": chosen})
        model_only = client.get("/api/assistant/status").json()
        SecretStore().set(SecretName.OPENROUTER_API_KEY, "sk-or-v1-test")
        ready = client.get("/api/assistant/status").json()
        stored = client.get("/api/settings/openrouter/model").json()
        cleared = client.delete("/api/settings/openrouter/model").json()
        after = client.get("/api/assistant/status").json()

    assert {"assistant.read", "assistant.write"} <= privileges
    assert initial == {
        "available": False,
        "openrouter_key_configured": False,
        "model": None,
        "name": "Lama",
    }
    assert search["total"] == 1
    assert selected.status_code == 200
    model = selected.json()["model"]
    assert model["id"] == "anthropic/claude-sonnet-5.5"
    assert model["pricing"]["completion"] == "0.000015"
    assert model["context_length"] == 1000000
    assert model["selected_at"]
    assert model_only["available"] is False
    assert ready == {
        "available": True,
        "openrouter_key_configured": True,
        "model": model,
        "name": "Lama",
    }
    assert "sk-or-v1-test" not in str(ready)
    assert stored == {"model": model}
    assert cleared == {"model": None}
    assert after["available"] is False


def test_selecting_an_unknown_model_or_without_openrouter_fails(
    tmp_path: Path, login: Login
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"), openrouter_catalog=_catalog())
    offline = create_app(
        Settings(data_dir=tmp_path / "offline"), openrouter_catalog=_catalog(status=503)
    )
    with TestClient(app, client=LOCAL) as client:
        login(client)
        unknown = client.put("/api/settings/openrouter/model", json={"id": "missing/model"})
    with TestClient(offline, client=LOCAL) as client:
        login(client)
        search = client.get("/api/settings/openrouter/models")
        select = client.put("/api/settings/openrouter/model", json={"id": "openrouter/auto"})

    assert unknown.status_code == 404
    assert search.status_code == 503
    assert select.status_code == 503


def test_openrouter_and_assistant_routes_are_classified() -> None:
    assert privilege_for_request("/api/settings/openrouter/models", "GET") == "settings.read"
    assert privilege_for_request("/api/settings/openrouter/model", "PUT") == "settings.write"
    assert privilege_for_request("/api/assistant/status", "GET") == "assistant.read"
