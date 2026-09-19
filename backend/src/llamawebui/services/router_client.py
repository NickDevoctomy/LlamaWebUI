"""Client for llama.cpp's native router model operations."""

from __future__ import annotations

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


class RouterClient(Protocol):
    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]: ...

    async def load_model(self, model: str) -> None: ...

    async def unload_model(self, model: str) -> None: ...


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