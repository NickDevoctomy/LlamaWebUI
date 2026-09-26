"""Deterministic OpenAI-compatible connection checks against a fake router."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

pytestmark = pytest.mark.asyncio


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def fake_router(token: str) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {token}":
            return httpx.Response(
                401,
                json={"error": {"message": "invalid api key: internal-token-detail"}},
            )
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": "demo-model", "object": "model"}]},
            )
        if request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content)
            if payload.get("stream"):
                chunks = (
                    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                )
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=Stream(chunks),
                )
            if payload.get("tools"):
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {
                                            "type": "function",
                                            "function": {
                                                "name": "get_weather",
                                                "arguments": '{"city":"Paris"}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "hello"}}]},
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.MockTransport(handler)


async def test_fake_router_accepts_authenticated_model_listing_and_completion() -> None:
    token = "lwui_test_token"
    async with httpx.AsyncClient(
        base_url="http://router.test", transport=fake_router(token)
    ) as client:
        models = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
        completion = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == "demo-model"
    assert completion.status_code == 200
    assert completion.json()["choices"][0]["message"]["content"] == "hello"


async def test_fake_router_supports_streaming_and_done_sentinel() -> None:
    token = "lwui_test_token"
    async with httpx.AsyncClient(
        base_url="http://router.test", transport=fake_router(token)
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "demo-model", "stream": True, "messages": []},
        )
        body = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()

    assert response.status_code == 200
    assert "hello" in body
    assert body.endswith("data: [DONE]\n\n")


async def test_fake_router_rejects_random_tokens_without_leaking_details() -> None:
    async with httpx.AsyncClient(
        base_url="http://router.test", transport=fake_router("lwui_valid")
    ) as client:
        response = await client.get(
            "/v1/models", headers={"Authorization": "Bearer lwui_random"}
        )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid api key: internal-token-detail"
    assert "lwui_valid" not in response.text


async def test_fake_router_returns_parseable_tool_call() -> None:
    token = "lwui_test_token"
    async with httpx.AsyncClient(
        base_url="http://router.test", transport=fake_router(token)
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "demo-model",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [{"type": "function", "function": {"name": "get_weather"}}],
            },
        )

    call = response.json()["choices"][0]["message"]["tool_calls"][0]
    assert response.status_code == 200
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Paris"}