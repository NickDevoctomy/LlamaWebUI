"""Stream chat completions from OpenRouter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Protocol

import httpx

from llamawebui.services.chat_stream import completion_chunks

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


# Error codes a provider sends when it failed or is busy, rather than refusing the request.
TRANSIENT_CODES = frozenset(
    {"internal_server_error", "server_error", "overloaded", "service_unavailable", "timeout"}
)


class OpenRouterError(RuntimeError):
    """OpenRouter refused or failed a request; the message is safe to show.

    ``transient`` marks failures worth retrying: the provider erred or was busy, as
    opposed to a refused request such as a bad key or no credits.
    """

    def __init__(
        self, message: str, status_code: int | None = None, *, transient: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


class ChatClient(Protocol):
    def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]: ...


class OpenRouterClient:
    def __init__(
        self,
        api_key: Callable[[], str | None],
        *,
        client: httpx.AsyncClient | None = None,
        url: str = OPENROUTER_CHAT_URL,
    ) -> None:
        # Read on every request, so a key saved in Settings applies without a restart.
        self._api_key = api_key
        self._client = client
        self._url = url

    async def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        """Stream a completion, yielding each chunk.

        ``usage`` is requested so the last chunk reports tokens and the actual cost.
        A chunk carrying an ``error`` (OpenRouter's way of failing mid-stream) raises.
        """
        key = self._api_key()
        if not key:
            raise OpenRouterError("no OpenRouter API key is saved")
        body: dict[str, object] = {
            **(options or {}),
            "model": model,
            "messages": list(messages),
            "stream": True,
            "usage": {"include": True},
        }
        if tools:
            body["tools"] = list(tools)
        headers = {
            "Authorization": f"Bearer {key}",
            # OpenRouter's app attribution, shown in the account's activity list.
            "X-Title": "LlamaWebUI",
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        try:
            async with client.stream("POST", self._url, json=body, headers=headers) as response:
                if response.is_error:
                    await response.aread()
                    raise OpenRouterError(
                        _error_message(response),
                        response.status_code,
                        transient=response.status_code in {408, 429} or response.status_code >= 500,
                    )
                try:
                    async for chunk in completion_chunks(response.aiter_lines()):
                        error = chunk.get("error")
                        if isinstance(error, dict):
                            raise OpenRouterError(_describe(error), transient=_transient(error))
                        yield chunk
                except ValueError as error:
                    raise OpenRouterError("OpenRouter sent an invalid completion chunk") from error
        except httpx.HTTPError as error:
            raise OpenRouterError(
                f"could not reach OpenRouter ({type(error).__name__})", transient=True
            ) from error
        finally:
            if owns_client:
                await client.aclose()


def _inner(error: Mapping[str, object]) -> Mapping[str, object]:
    """The provider's own error, when OpenRouter passes its raw ``data: {...}`` line on."""
    message = error.get("message")
    if isinstance(message, str) and message.startswith("data:"):
        try:
            payload = json.loads(message.removeprefix("data:").strip())
        except json.JSONDecodeError:
            return error
        nested = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(nested, dict):
            return nested
    return error


def _describe(error: Mapping[str, object]) -> str:
    message = _inner(error).get("message")
    text = message if isinstance(message, str) and message else "request failed"
    return f"OpenRouter: {text}"


def _transient(error: Mapping[str, object]) -> bool:
    for source in (error, _inner(error)):
        code = source.get("code")
        if isinstance(code, int) and (code in {408, 429} or code >= 500):
            return True
        if isinstance(code, str) and code in TRANSIENT_CODES:
            return True
    return False


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return f"{_describe(error)} ({response.status_code})"
    return f"OpenRouter returned {response.status_code}"
