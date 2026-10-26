"""Read-only tools that describe the machine, the router, runtimes, profiles and models."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.domain.model_profile import validate_profile
from llamawebui.domain.profile_arguments import errors_of, warnings_of
from llamawebui.domain.router_lifecycle import RouterState
from llamawebui.models import ModelProfileRecord
from llamawebui.services.profile_registry import (
    ProfileNotFoundError,
    configuration_payload,
    profile_from_record,
)
from llamawebui.services.router_client import RouterAPIError
from llamawebui.services.runtime_registry import RuntimeNotFoundError, runtime_capabilities

GIB = 1024**3


def _gib(value: int | None) -> float | None:
    return round(value / GIB, 2) if value else None


async def get_hardware(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    hardware = await asyncio.to_thread(context.services.hardware)
    return json_result(
        {
            "cpu": hardware.cpu,
            "memory_gib": _gib(hardware.memory_bytes),
            "gpus": list(hardware.gpus),
            # Only NVIDIA cards report this; unified memory and other cards show null.
            "discrete_vram_gib": _gib(hardware.discrete_vram_bytes),
        },
        hardware.cpu or "hardware",
    )


async def router_status(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    supervisor = context.services.supervisor
    payload: dict[str, object] = {"state": supervisor.state.value, "pid": supervisor.pid}
    if supervisor.state in {RouterState.READY, RouterState.DEGRADED}:
        try:
            models = await context.services.router_client.list_models()
        except RouterAPIError as error:
            payload["models_error"] = str(error)
        else:
            payload["models"] = [
                {"id": model.id, "status": model.status.get("value")} for model in models
            ]
    return json_result(payload, f"router {supervisor.state.value}")


async def list_runtimes(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    runtimes = [
        {
            "id": runtime.id,
            "name": runtime.name,
            "build": runtime.build,
            "backend": runtime.backend,
            "devices": runtime.devices,
            "router_compatible": "models-preset" in runtime.options and not runtime.probe_error,
        }
        for runtime in context.services.runtimes.list()
    ]
    return json_result(runtimes, f"{len(runtimes)} runtimes")


def _profile_summary(record: ModelProfileRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "alias": record.alias,
        "runtime_id": record.runtime_id,
        "model_file": Path(record.model_path).name,
        "enabled": record.enabled,
        "exclusive": record.exclusive,
        "version": record.version,
        "benchmarks": sorted((record.benchmark_results or {}).keys()),
    }


async def list_profiles(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    profiles = [_profile_summary(record) for record in context.services.profiles.list()]
    return json_result(profiles, f"{len(profiles)} profiles")


def _find_profile(context: ToolContext, reference: str) -> ModelProfileRecord:
    registry = context.services.profiles
    try:
        return registry.get(reference)
    except ProfileNotFoundError:
        match = next((record for record in registry.list() if record.alias == reference), None)
        if match is None:
            raise ToolError(f"no profile has the id or alias {reference!r}") from None
        return match


async def get_profile(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = _find_profile(context, text_argument(arguments, "profile"))
    services = context.services
    payload: dict[str, object] = {
        **_profile_summary(record),
        "model_path": record.model_path,
        "description": record.description,
        "model_available": services.artifacts.profile_available(record),
        "configuration": configuration_payload(record, services.catalog),
        "benchmark_results": record.benchmark_results or {},
    }
    try:
        runtime = services.runtimes.get(record.runtime_id)
    except RuntimeNotFoundError:
        payload["issues"] = {"errors": ["the profile's runtime is no longer registered"]}
    else:
        issues = validate_profile(
            profile_from_record(record, services.catalog),
            services.catalog,
            runtime_capabilities(runtime),
        )
        payload["issues"] = {"errors": errors_of(issues), "warnings": warnings_of(issues)}
    return json_result(payload, record.alias)


async def list_library(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    models = [
        {
            "download_id": model.download_id,
            "repo_id": model.repo_id,
            "quantization_group": model.group_key,
            "path": str(model.primary_path),
            "files": model.file_count,
            "size_gib": _gib(model.total_bytes),
        }
        for model in context.services.library.list()
    ]
    return json_result(models, f"{len(models)} models")


async def list_downloads(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    jobs = [
        {
            "id": job.id,
            "repo_id": job.repo_id,
            "quantization_group": job.group_key,
            "state": job.state,
            "completed_gib": _gib(job.completed_bytes),
            "total_gib": _gib(job.total_bytes),
            "error": job.error,
        }
        for job in context.services.downloads.list()
    ]
    return json_result(jobs, f"{len(jobs)} downloads")


SYSTEM_TOOLS = (
    Tool(
        "get_hardware",
        "The CPU, system memory and GPUs of this machine.",
        object_schema(),
        "profiles.read",
        get_hardware,
    ),
    Tool(
        "router_status",
        "Whether the llama-server router is running, and its models' load states.",
        object_schema(),
        "server.read",
        router_status,
    ),
    Tool(
        "list_runtimes",
        "The registered llama.cpp runtimes (builds): id, build, backend and devices.",
        object_schema(),
        "runtimes.read",
        list_runtimes,
    ),
    Tool(
        "list_profiles",
        "Every model profile: id, alias, runtime, model file, enabled, version and which "
        "benchmarks have results.",
        object_schema(),
        "profiles.read",
        list_profiles,
    ),
    Tool(
        "get_profile",
        "One profile in full: its llama-server arguments, latest benchmark results and any "
        "validation errors or warnings.",
        object_schema(
            {"profile": {"type": "string", "description": "The profile's id or alias."}},
            ["profile"],
        ),
        "profiles.read",
        get_profile,
    ),
    Tool(
        "list_library",
        "The downloaded models: repository, quantization group, file path and size.",
        object_schema(),
        "library.read",
        list_library,
    ),
    Tool(
        "list_downloads",
        "Model downloads and their progress or errors.",
        object_schema(),
        "downloads.read",
        list_downloads,
    ),
)
