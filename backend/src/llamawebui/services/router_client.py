"""Client for llama.cpp's native router model operations."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast

import httpx


class RouterAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


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


class RouterClient(Protocol):
    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]: ...

    async def load_model(self, model: str) -> None: ...

    async def unload_model(self, model: str) -> None: ...

    def model_events(self) -> AsyncIterator[RouterModelEvent]: ...


class HttpRouterClient:
    def __init__(self, host: str, port: int, *, api_key: str | None = None) -> None:
        probe_host = "127.0.0.1" if host == "0.0.0.0" else host
        if probe_host == "::":
            probe_host = "::1"
        formatted_host = f"[{probe_host}]" if ":" in probe_host else probe_host
        self._base_url = f"http://{formatted_host}:{port}"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

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
                base_url=self._base_url, headers=self._headers, timeout=timeout
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
            raise RouterAPIError(502, "llama.cpp model event stream failed") from error

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
                base_url=self._base_url, headers=self._headers, timeout=30.0
            ) as client:
                response = await client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as error:
            raise RouterAPIError(502, "llama.cpp router request failed") from error
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