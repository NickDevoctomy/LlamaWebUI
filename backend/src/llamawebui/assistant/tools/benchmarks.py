"""Tools that run benchmarks, read and score their results, and start or stop the router.

A benchmark runs in the background; the assistant is notified when it ends instead of
waiting in a tool call for minutes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from llamawebui.assistant.brief import Brief
from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.assistant.tools.system import GIB, find_profile
from llamawebui.domain.benchmark import DEFAULT_BENCHMARK_ID, DEFAULT_SYSTEM_PROMPT_ID, Benchmark
from llamawebui.models import BenchmarkRunRecord
from llamawebui.services.benchmark_runner import BenchmarkBusyError, BenchmarkRunNotFoundError
from llamawebui.services.control_plane import ControlPlaneError

# The figures a run is judged by, as stats keys, with how they read.
KEY_STATS = (
    ("load_seconds", "load", "s"),
    ("prompt_tokens_per_second", "prefill", "tok/s"),
    ("decode_tokens_per_second", "decode", "tok/s"),
)


def peak_memory(record: BenchmarkRunRecord) -> dict[str, float | None]:
    """The most RAM and VRAM the model used during a run, in GiB."""

    def peak(key: str) -> float | None:
        values = [entry[key] for entry in record.resources if isinstance(entry.get(key), int)]
        return round(max(values) / GIB, 2) if values else None  # type: ignore[type-var,operator]

    return {"ram_gib": peak("ram_bytes"), "vram_gib": peak("vram_bytes")}


def run_summary(record: BenchmarkRunRecord) -> dict[str, object]:
    stats = record.stats or {}
    return {
        "run_id": record.id,
        "profile": record.profile_alias,
        "profile_version": record.profile_version,
        "benchmark": record.benchmark_id,
        "state": record.state,
        "error": record.error,
        "load_seconds": stats.get("load_seconds"),
        "prefill_tokens_per_second": stats.get("prompt_tokens_per_second"),
        "decode_tokens_per_second": stats.get("decode_tokens_per_second"),
        "context_size": stats.get("context_size"),
        "peak": peak_memory(record),
        "started_at": record.started_at,
    }


def benchmark_notice(record: BenchmarkRunRecord) -> str:
    """How a finished run went, in one line for the conversation that started it."""
    head = f"Benchmark run {record.id} ({record.benchmark_title} on {record.profile_alias}) "
    if record.state != "completed":
        return head + f"{record.state}: {record.error or 'no reason given'}."
    stats = record.stats or {}
    figures = [
        f"{label} {stats[key]} {unit}"
        for key, label, unit in KEY_STATS
        if isinstance(stats.get(key), int | float)
    ]
    peak = peak_memory(record)
    figures += [
        f"peak {name.split('_')[0].upper()} {value} GiB" for name, value in peak.items() if value
    ]
    return head + "completed: " + (", ".join(figures) or "no figures recorded") + "."


def _run(context: ToolContext, run_id: str) -> BenchmarkRunRecord:
    try:
        return context.services.benchmark_runs.get(run_id)
    except BenchmarkRunNotFoundError as error:
        raise ToolError(str(error)) from error


def _brief(context: ToolContext) -> Brief | None:
    try:
        return Brief.model_validate(context.brief)
    except ValidationError:
        return None


def _benchmark(context: ToolContext, benchmark_id: str | None) -> Benchmark:
    benchmarks: Sequence[Benchmark] = context.services.benchmarks()
    brief = _brief(context)
    wanted = benchmark_id or (brief.benchmark_id if brief else None) or DEFAULT_BENCHMARK_ID
    benchmark = next((item for item in benchmarks if item.id == wanted), None)
    if benchmark is None:
        raise ToolError(f"there is no benchmark {wanted!r}; see list_benchmarks")
    return benchmark


async def list_benchmarks(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    benchmarks = [
        {
            "id": item.id,
            "title": item.title,
            "typescript": item.typescript,
            "tasks": len(item.tasks),
        }
        for item in context.services.benchmarks()
    ]
    return json_result(benchmarks, f"{len(benchmarks)} benchmarks")


def _check_can_run(context: ToolContext, arguments: Mapping[str, object]) -> None:
    services = context.services
    if services.benchmark_runner.active_run_id is not None:
        raise ToolError("a benchmark is already running; wait for its notification")
    if not services.control_plane.router_stopped:
        raise ToolError("the router is running; stop it first with stop_router")


async def describe_benchmark(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    _check_can_run(context, arguments)
    record = find_profile(context, text_argument(arguments, "profile"))
    if not record.enabled:
        raise ToolError(f"{record.alias} is disabled; enable it with update_profile first")
    benchmark = _benchmark(context, text_argument(arguments, "benchmark_id", required=False))
    return (
        f"Benchmark {record.alias} with {benchmark.title}",
        {"profile": record.alias, "version": record.version, "benchmark": benchmark.title},
    )


async def run_benchmark(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    _check_can_run(context, arguments)
    services = context.services
    record = find_profile(context, text_argument(arguments, "profile"))
    benchmark = _benchmark(context, text_argument(arguments, "benchmark_id", required=False))
    brief = _brief(context)
    reasoning = text_argument(arguments, "reasoning_level", required=False) or (
        brief.reasoning_level if brief else None
    )
    system_prompt = next(
        (item for item in services.system_prompts() if item.id == DEFAULT_SYSTEM_PROMPT_ID), None
    )
    try:
        run = services.benchmark_runner.start(
            record,
            benchmark,
            None,
            tools=True,
            system_prompt=system_prompt,
            reasoning_level=reasoning,
        )
    except (BenchmarkBusyError, ValueError) as error:
        raise ToolError(str(error)) from error
    context.watch("benchmark", run.id)
    return json_result(
        {
            "run_id": run.id,
            "note": "The benchmark runs in the background and stops the router when done. "
            "You will be notified when it finishes; do not poll for it.",
        },
        f"started {benchmark.title}",
    )


async def get_benchmark_run(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = _run(context, text_argument(arguments, "run_id"))
    payload = run_summary(record)
    stats = record.stats or {}
    payload["stats"] = stats
    progress = context.services.benchmark_runner.progress(record.id)
    if progress is not None:
        payload["stage"] = progress.get("stage")
    return json_result(payload, f"{record.state}")


async def list_benchmark_runs(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    reference = text_argument(arguments, "profile", required=False)
    profile_id = find_profile(context, reference).id if reference else None
    runs = [run_summary(record) for record in context.services.benchmark_runs.list(profile_id)]
    return json_result(runs[:20], f"{len(runs)} runs")


def brief_target_count(brief: Brief) -> int:
    values = (
        brief.min_decode_tps,
        brief.min_prefill_tps,
        brief.max_load_seconds,
        brief.max_ram_gb,
        brief.max_vram_gb,
        brief.min_context_tokens,
    )
    return sum(1 for value in values if value is not None)


def evaluation(brief: Brief, record: BenchmarkRunRecord) -> dict[str, object]:
    """A run scored against the brief's targets, most important first."""
    stats = record.stats or {}
    peak = peak_memory(record)

    def number(value: object) -> float | None:
        return float(value) if isinstance(value, int | float) else None

    checks: dict[str, list[dict[str, object]]] = {
        "decode": [],
        "prefill": [],
        "load": [],
        "memory": [],
    }

    def check(
        group: str, name: str, required: float | None, measured: float | None, *, most: bool
    ) -> None:
        if required is None:
            return
        met = None if measured is None else (measured <= required if most else measured >= required)
        gap = None if measured is None else round(measured - required, 2)
        checks[group].append(
            {"target": name, "required": required, "measured": measured, "met": met, "gap": gap}
        )

    check(
        "decode",
        "decode tokens/s (at least)",
        brief.min_decode_tps,
        number(stats.get("decode_tokens_per_second")),
        most=False,
    )
    check(
        "prefill",
        "prefill tokens/s (at least)",
        brief.min_prefill_tps,
        number(stats.get("prompt_tokens_per_second")),
        most=False,
    )
    check(
        "load",
        "load seconds (at most)",
        brief.max_load_seconds,
        number(stats.get("load_seconds")),
        most=True,
    )
    check("memory", "RAM GiB (at most)", brief.max_ram_gb, peak["ram_gib"], most=True)
    check("memory", "VRAM GiB (at most)", brief.max_vram_gb, peak["vram_gib"], most=True)
    targets = [item for group in brief.priority for item in checks[group]]
    if brief.min_context_tokens is not None:
        context_size = number(stats.get("context_size"))
        targets.append(
            {
                "target": "context tokens (at least, never traded away)",
                "required": brief.min_context_tokens,
                "measured": context_size,
                "met": None if context_size is None else context_size >= brief.min_context_tokens,
            }
        )
    return {
        "run_id": record.id,
        "profile": record.profile_alias,
        "state": record.state,
        "targets": targets,
        "targets_met": sum(1 for item in targets if item["met"] is True),
        "all_met": bool(targets) and all(item["met"] is True for item in targets),
        "unmeasured": [item["target"] for item in targets if item["met"] is None],
    }


async def evaluate_run(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = _run(context, text_argument(arguments, "run_id"))
    brief = _brief(context)
    if brief is None:
        raise ToolError("this conversation's brief could not be read")
    if record.state != "completed":
        raise ToolError(f"run {record.id} {record.state}, so it has no results to score")
    result = evaluation(brief, record)
    targets = brief_target_count(brief)
    return json_result(result, f"{result['targets_met']} of {targets} targets met")


async def describe_stop(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    if context.services.control_plane.router_stopped:
        raise ToolError("the router is not running")
    return "Stop the router (unloads every model)", {}


async def stop_router(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    try:
        await context.services.control_plane.stop_router()
    except ControlPlaneError as error:
        raise ToolError(str(error)) from error
    return json_result({"router": "stopped"}, "router stopped")


async def describe_start(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    runtime_id = text_argument(arguments, "runtime_id")
    if not context.services.control_plane.router_stopped:
        raise ToolError("the router is already running")
    try:
        runtime = context.services.runtimes.get(runtime_id)
    except LookupError as error:
        raise ToolError(str(error)) from error
    return f"Start the router with {runtime.name}", {"runtime": runtime.name}


async def start_router(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    try:
        await context.services.control_plane.start_router(text_argument(arguments, "runtime_id"))
    except ControlPlaneError as error:
        raise ToolError(str(error)) from error
    return json_result({"router": "running"}, "router started")


BENCHMARK_TOOLS = (
    Tool(
        "list_benchmarks",
        "The bundled benchmark prompts a profile can be measured with.",
        object_schema(),
        "server.read",
        list_benchmarks,
    ),
    Tool(
        "run_benchmark",
        "Benchmark an enabled profile: starts the router, loads only that profile, measures "
        "load time, prefill and decode speed and memory, then stops the router. Needs the "
        "router stopped. Runs in the background; you are notified when it ends.",
        object_schema(
            {
                "profile": {"type": "string", "description": "The profile's id or alias."},
                "benchmark_id": {"type": "string", "description": "Defaults to the brief's."},
                "reasoning_level": {"type": "string"},
            },
            ["profile"],
        ),
        "server.lifecycle.write",
        run_benchmark,
        approval="benchmarks",
        describe=describe_benchmark,
    ),
    Tool(
        "get_benchmark_run",
        "One benchmark run in full: state, error, every statistic and peak memory.",
        object_schema({"run_id": {"type": "string"}}, ["run_id"]),
        "server.read",
        get_benchmark_run,
    ),
    Tool(
        "list_benchmark_runs",
        "Recent benchmark runs, newest first, with their key figures, for comparing changes.",
        object_schema({"profile": {"type": "string", "description": "Only this profile."}}),
        "server.read",
        list_benchmark_runs,
    ),
    Tool(
        "evaluate_run",
        "Score a completed run against the brief's targets, in priority order, with the gap "
        "to each target.",
        object_schema({"run_id": {"type": "string"}}, ["run_id"]),
        "server.read",
        evaluate_run,
    ),
    Tool(
        "stop_router",
        "Stop the llama-server router, unloading every model. Needed before benchmarking or "
        "editing profiles.",
        object_schema(),
        "server.lifecycle.write",
        stop_router,
        approval="benchmarks",
        describe=describe_stop,
    ),
    Tool(
        "start_router",
        "Start the router with a runtime's enabled profiles, for example to leave it running "
        "for the operator when you are done.",
        object_schema({"runtime_id": {"type": "string"}}, ["runtime_id"]),
        "server.lifecycle.write",
        start_router,
        approval="benchmarks",
        describe=describe_start,
    ),
)
