"""Client for llama.cpp's native router model operations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import httpx

from llamawebui.services.chat_stream import completion_chunks


class RouterAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class RouterUnavailableError(RouterAPIError):
    """The router could not be reached or did not answer in time, as opposed to refusing."""

    def __init__(self, message: str, error: Exception) -> None:
        super().__init__(502, f"{message} ({type(error).__name__})")


@dataclass(frozen=True, slots=True)
class RouterModel:
    id: str
    path: str | None
    status: dict[str, object]
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class RouterModelEvent:
    model: str
    event: str
    data: dict[str, object]


async def collect_model_events(
    client: RouterClient, *, limit: int = 1, timeout_seconds: float = 15.0
) -> tuple[RouterModelEvent, ...]:
    """Collect a bounded native event sample without waiting forever for SSE."""
    events: list[RouterModelEvent] = []
    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in client.model_events():
                events.append(event)
                if len(events) >= limit:
                    break
    except TimeoutError:
        pass
    return tuple(events)


class RouterClient(Protocol):
    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]: ...

    async def load_model(self, model: str) -> None: ...

    async def unload_model(self, model: str) -> None: ...

    def model_events(self) -> AsyncIterator[RouterModelEvent]: ...


class HttpRouterClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        api_key: str | None = None,
        api_key_provider: Callable[[], str | None] | None = None,
    ) -> None:
        probe_host = "127.0.0.1" if host == "0.0.0.0" else host
        if probe_host == "::":
            probe_host = "::1"
        formatted_host = f"[{probe_host}]" if ":" in probe_host else probe_host
        self._base_url = f"http://{formatted_host}:{port}"
        self._api_key = api_key
        self._api_key_provider = api_key_provider

    def _headers(self) -> dict[str, str]:
        api_key = self._api_key_provider() if self._api_key_provider else self._api_key
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]:
        payload = await self._request(
            "GET", "/models", params={"reload": 1} if reload else None
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise RouterAPIError(502, "llama.cpp returned an invalid model list")
        models: list[RouterModel] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise RouterAPIError(502, "llama.cpp returned an invalid model entry")
            model_data = cast(dict[str, object], item)
            status = model_data.get("status")
            if not isinstance(status, dict) or not isinstance(status.get("value"), str):
                raise RouterAPIError(502, "llama.cpp returned an invalid model status")
            path = model_data.get("path")
            if path is not None and not isinstance(path, str):
                raise RouterAPIError(502, "llama.cpp returned an invalid model path")
            models.append(
                RouterModel(
                    id=cast(str, model_data["id"]),
                    path=path,
                    status=cast(dict[str, object], status),
                    metadata={
                        key: value
                        for key, value in model_data.items()
                        if key not in {"id", "path", "status"}
                    },
                )
            )
        return tuple(models)

    async def load_model(self, model: str) -> None:
        await self._model_action("/models/load", model)

    async def unload_model(self, model: str) -> None:
        await self._model_action("/models/unload", model)

    async def model_events(self) -> AsyncIterator[RouterModelEvent]:
        try:
            timeout = httpx.Timeout(30.0, read=None)
            async with httpx.AsyncClient(
                base_url=self._base_url, headers=self._headers(), timeout=timeout
            ) as client, client.stream("GET", "/models/sse") as response:
                if response.is_error:
                    await response.aread()
                    raise RouterAPIError(response.status_code, _error_message(response))
                if response.headers.get("content-type", "").split(";", 1)[0].strip() != (
                    "text/event-stream"
                ):
                    raise RouterAPIError(
                        502, "llama.cpp returned an invalid model event stream"
                    )
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if not line:
                        if data_lines:
                            yield _parse_model_event("\n".join(data_lines))
                            data_lines.clear()
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    if field == "data":
                        data_lines.append(value.removeprefix(" "))
                if data_lines:
                    yield _parse_model_event("\n".join(data_lines))
        except RouterAPIError:
            raise
        except httpx.HTTPError as error:
            raise RouterUnavailableError("llama.cpp model event stream failed", error) from error

    async def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        max_tokens: int | None,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        """Stream a chat completion, yielding each chunk llama.cpp sends.

        Chunks carry speed timings as they go, and prompt progress while the
        prompt is processed, so a caller can follow both phases live. Without
        ``max_tokens`` generation runs until the model stops or the context is full.
        """
        # Extra fields, such as reasoning controls, go first so none can replace the rest.
        body: dict[str, object] = {
            **(options or {}),
            "model": model,
            "messages": list(messages),
            "stream": True,
            "timings_per_token": True,
            "return_progress": True,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = list(tools)
        try:
            # Generation can run for many minutes, so only connecting is time-limited.
            timeout = httpx.Timeout(30.0, read=None)
            async with httpx.AsyncClient(
                base_url=self._base_url, headers=self._headers(), timeout=timeout
            ) as client, client.stream("POST", "/v1/chat/completions", json=body) as response:
                if response.is_error:
                    await response.aread()
                    raise RouterAPIError(response.status_code, _error_message(response))
                try:
                    async for chunk in completion_chunks(response.aiter_lines()):
                        yield chunk
                except ValueError as error:
                    raise RouterAPIError(
                        502, "llama.cpp returned an invalid completion chunk"
                    ) from error
        except RouterAPIError:
            raise
        except httpx.HTTPError as error:
            raise RouterUnavailableError("llama.cpp completion stream failed", error) from error

    async def _model_action(self, path: str, model: str) -> None:
        if not model.strip():
            raise ValueError("model ID must not be empty")
        payload = await self._request("POST", path, json_body={"model": model})
        if payload.get("success") is not True:
            raise RouterAPIError(502, "llama.cpp did not confirm the model operation")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, int] | None = None,
        json_body: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, headers=self._headers(), timeout=30.0
            ) as client:
                response = await client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as error:
            raise RouterUnavailableError("llama.cpp router request failed", error) from error
        if response.is_error:
            raise RouterAPIError(response.status_code, _error_message(response))
        try:
            payload = response.json()
        except ValueError as error:
            raise RouterAPIError(502, "llama.cpp returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RouterAPIError(502, "llama.cpp returned an invalid response")
        return cast(dict[str, object], payload)


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "llama.cpp router request failed"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return cast(str, error["message"])
    return "llama.cpp router request failed"


def _parse_model_event(raw_data: str) -> RouterModelEvent:
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as error:
        raise RouterAPIError(502, "llama.cpp returned an invalid model event") from error
    if not isinstance(payload, dict):
        raise RouterAPIError(502, "llama.cpp returned an invalid model event")
    model = payload.get("model")
    event = payload.get("event")
    data = payload.get("data")
    if (
        not isinstance(model, str)
        or not model
        or not isinstance(event, str)
        or not event
        or "\r" in event
        or "\n" in event
        or not isinstance(data, dict)
    ):
        raise RouterAPIError(502, "llama.cpp returned an invalid model event")
    return RouterModelEvent(
        model=model,
        event=event,
        data=cast(dict[str, object], data),
    )