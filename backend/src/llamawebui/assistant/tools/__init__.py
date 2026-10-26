"""The assistant's tools: what each does, who may use it, and how calls are run."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.services.chat_stream import function_tool, shorten
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.model_artifact_registry import ModelArtifactRegistry
from llamawebui.services.model_library import ModelLibrary
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.router_client import RouterClient
from llamawebui.services.router_supervisor import RouterSupervisor
from llamawebui.services.runtime_registry import RuntimeRegistry

# The most a tool result may add to the conversation; longer results are cut.
MAX_RESULT_CHARACTERS = 24_000


@dataclass(frozen=True, slots=True)
class ToolServices:
    """The control-plane services tools work with."""

    catalog: ArgumentCatalog
    runtimes: RuntimeRegistry
    profiles: ProfileRegistry
    artifacts: ModelArtifactRegistry
    library: ModelLibrary
    downloads: DownloadRegistry
    supervisor: RouterSupervisor
    router_client: RouterClient
    hardware: Callable[[], HardwareProfile]


@dataclass(frozen=True, slots=True)
class ToolContext:
    services: ToolServices
    # The privileges of the user whose message started the turn.
    privileges: frozenset[str]


class ToolError(Exception):
    """A refused or failed call; the message goes back to the model."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    text: str
    # A short line for the conversation view, e.g. "3 profiles".
    summary: str = ""
    ok: bool = True


Handler = Callable[[ToolContext, Mapping[str, object]], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, object]
    privilege: str
    handler: Handler
    # Actions needing the operator's approval name their category; None runs freely.
    approval: str | None = field(default=None)

    def definition(self) -> dict[str, object]:
        return function_tool(self.name, self.description, self.parameters)


def offered(tools: Iterable[Tool], privileges: frozenset[str]) -> list[Tool]:
    """The tools a user's privileges allow."""
    return [tool for tool in tools if tool.privilege in privileges]


def object_schema(
    properties: Mapping[str, object] | None = None, required: Sequence[str] = ()
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required),
        "additionalProperties": False,
    }


def json_result(value: object, summary: str = "") -> ToolResult:
    return ToolResult(json.dumps(value, default=str, separators=(",", ":")), summary)


def text_argument(arguments: Mapping[str, object], key: str, *, required: bool = True) -> str:
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        raise ToolError(f"{key} is required and must be text")
    return ""


async def call_tool(
    tools: Mapping[str, Tool], context: ToolContext, name: str, arguments: str
) -> ToolResult:
    """Run one call; any refusal or failure comes back as an error result, never raised."""
    tool = tools.get(name)
    if tool is None or tool.privilege not in context.privileges:
        return ToolResult(f"Error: there is no tool named {name!r}.", "unknown tool", ok=False)
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        return ToolResult("Error: the arguments must be a JSON object.", "bad arguments", ok=False)
    try:
        result = await tool.handler(context, parsed)
    except ToolError as error:
        return ToolResult(f"Error: {error}", str(error), ok=False)
    return ToolResult(shorten(result.text, MAX_RESULT_CHARACTERS), result.summary, result.ok)
