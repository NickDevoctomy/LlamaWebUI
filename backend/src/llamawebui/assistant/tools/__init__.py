"""The assistant's tools: what each does, who may use it, and how calls are run."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from llamawebui.assistant.reports import ReportLibrary
from llamawebui.assistant.research import ResearchLibrary
from llamawebui.assistant.web import WebAccess
from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.benchmark import Benchmark, SystemPrompt
from llamawebui.services.benchmark_runner import BenchmarkRunner, BenchmarkRunRegistry
from llamawebui.services.chat_stream import function_tool, shorten
from llamawebui.services.control_plane import ControlPlane
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.huggingface_catalog import Catalog
from llamawebui.services.llama_release_installer import GitHubReleaseClient
from llamawebui.services.model_artifact_registry import ModelArtifactRegistry
from llamawebui.services.model_library import ModelLibrary
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.profile_snapshots import ProfileSnapshotRegistry
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
    control_plane: ControlPlane
    hub: Catalog
    releases: GitHubReleaseClient
    benchmark_runner: BenchmarkRunner
    benchmark_runs: BenchmarkRunRegistry
    benchmarks: Callable[[], Sequence[Benchmark]]
    system_prompts: Callable[[], Sequence[SystemPrompt]]
    web: WebAccess
    research: ResearchLibrary
    reports: ReportLibrary
    snapshots: ProfileSnapshotRegistry
    # Whether research and reports are written straight into the shipped folders.
    auto_publish: Callable[[], bool] = lambda: False


@dataclass(frozen=True, slots=True)
class ToolContext:
    services: ToolServices
    # The privileges of the user whose message started the turn.
    privileges: frozenset[str]
    # The conversation's optimisation brief, as stored.
    brief: Mapping[str, object] = field(default_factory=dict)
    # Ask to be told when a background job ends: watch("benchmark", run_id).
    watch: Callable[[str, str], None] = lambda kind, job_id: None
    conversation_id: str = ""
    # The OpenRouter model answering, recorded as the author of research documents.
    model_id: str = ""


class ToolError(Exception):
    """A refused or failed call; the message goes back to the model."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    text: str
    # A short line for the conversation view, e.g. "3 profiles".
    summary: str = ""
    ok: bool = True
    # What the call cost on top of the model's reply, e.g. a web search.
    cost_usd: float = 0.0


Handler = Callable[[ToolContext, Mapping[str, object]], Awaitable[ToolResult]]
# What an approval card shows: a title and the details to decide on.
Describer = Callable[[ToolContext, Mapping[str, object]], Awaitable[tuple[str, dict[str, object]]]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, object]
    privilege: str
    handler: Handler
    # Actions needing the operator's approval name their category; None runs freely.
    approval: str | None = field(default=None)
    # Checks the arguments and words the approval request; raises ToolError if invalid.
    describe: Describer | None = field(default=None)

    async def approval_request(
        self, context: ToolContext, arguments: Mapping[str, object]
    ) -> tuple[str, dict[str, object]]:
        if self.describe is not None:
            return await self.describe(context, arguments)
        return self.name, dict(arguments)

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


def error_result(message: str, summary: str | None = None) -> ToolResult:
    return ToolResult(f"Error: {message}", summary or message, ok=False)


def resolve_call(
    tools: Mapping[str, Tool], context: ToolContext, name: str, arguments: str
) -> tuple[Tool, dict[str, object]] | ToolResult:
    """The tool and parsed arguments for a call, or the error to answer it with."""
    tool = tools.get(name)
    if tool is None or tool.privilege not in context.privileges:
        return error_result(f"there is no tool named {name!r}.", "unknown tool")
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        return error_result("the arguments must be a JSON object.", "bad arguments")
    return tool, parsed


async def run_call(tool: Tool, context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    """Run a resolved call; a refusal or failure comes back as an error result."""
    try:
        result = await tool.handler(context, arguments)
    except ToolError as error:
        return error_result(str(error))
    return ToolResult(
        shorten(result.text, MAX_RESULT_CHARACTERS), result.summary, result.ok, result.cost_usd
    )


async def call_tool(
    tools: Mapping[str, Tool], context: ToolContext, name: str, arguments: str
) -> ToolResult:
    """Resolve and run one call without asking for approval."""
    resolved = resolve_call(tools, context, name, arguments)
    if isinstance(resolved, ToolResult):
        return resolved
    tool, parsed = resolved
    return await run_call(tool, context, parsed)
