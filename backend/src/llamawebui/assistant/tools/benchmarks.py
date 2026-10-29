"""Tools that run benchmarks, read and score their results, and start or stop the router.

A benchmark runs in the background; the assistant is notified when it ends instead of
waiting in a tool call for minutes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from pydantic import ValidationError

from llamawebui.assistant.brief import Brief
from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    ToolServices,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.assistant.tools.system import GIB, find_profile
from llamawebui.domain.benchmark import DEFAULT_BENCHMARK_ID, DEFAULT_SYSTEM_PROMPT_ID, Benchmark
from llamawebui.models import BenchmarkRunRecord
from llamawebui.services.benchmark_runner import BenchmarkBusyError, BenchmarkRunNotFoundError
from llamawebui.services.control_plane import ControlPlaneError
from llamawebui.services.launch_settings import (
    launch_settings,
    profile_fingerprint,
    settings_changes,
)
from llamawebui.services.profile_registry import ProfileNotFoundError

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
        "reasoning_level": record.reasoning_level,
        # Runs with the same value measured the same launch settings.
        "settings": record.settings_fingerprint[:10] if record.settings_fingerprint else None,
        "state": record.state,
        "error": record.error,
        "load_seconds": stats.get("load_seconds"),
        "prefill_tokens_per_second": stats.get("prompt_tokens_per_second"),
        "decode_tokens_per_second": stats.get("decode_tokens_per_second"),
        "context_size": stats.get("context_size"),
        "peak": peak_memory(record),
        "started_at": record.started_at,
    }


# Router log lines in a failure notice, and the longest a line may be anywhere it is shown.
NOTICE_LOG_LINES = 20
LOG_LINE_CHARACTERS = 400


def log_tail(lines: Sequence[str], count: int, contains: str = "") -> list[str]:
    """The last lines of a log, optionally only those containing some text, each cut short."""
    wanted = contains.lower()
    chosen = [line for line in lines if wanted in line.lower()] if wanted else list(lines)
    return [
        line if len(line) <= LOG_LINE_CHARACTERS else line[:LOG_LINE_CHARACTERS] + "…"
        for line in chosen[-count:]
    ]


def benchmark_notice(record: BenchmarkRunRecord) -> str:
    """How a finished run went, in one line for the conversation that started it."""
    level = record.reasoning_level or "the model's default"
    head = (
        f"Benchmark run {record.id} has finished ({record.state}): {record.benchmark_title} "
        f"on {record.profile_alias}, reasoning {level}. "
    )
    if record.state != "completed":
        notice = head + f"{record.error or 'No reason given'}."
        tail = log_tail(record.server_log or [], NOTICE_LOG_LINES)
        if tail:
            notice += (
                f"\n\nThe router's last {len(tail)} log lines:\n" + "\n".join(tail) + "\n\n"
                f"Read more with read_server_log and run_id {record.id}."
            )
        return notice
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
    text = ", ".join(figures) or "no figures recorded"
    return head + text[0].upper() + text[1:] + "."


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


def _reasoning_level(context: ToolContext) -> str | None:
    """The brief's reasoning level; blank there means the model's default."""
    brief = _brief(context)
    return brief.reasoning_level if brief else None


def _system_prompt_extra(context: ToolContext) -> str | None:
    """The text the brief adds after every run's system prompt; blank adds nothing."""
    brief = _brief(context)
    return ((brief.system_prompt_extra or "").strip() or None) if brief else None


def measured_now(services: ToolServices, reference: str) -> bool:
    """Whether a completed run measured the profile exactly as it launches now."""
    registry = services.profiles
    try:
        profile = registry.get(reference)
    except ProfileNotFoundError:
        match = next((record for record in registry.list() if record.alias == reference), None)
        if match is None:
            return False
        profile = match
    current = profile_fingerprint(profile)
    return any(
        record.state == "completed" and record.settings_fingerprint == current
        for record in services.benchmark_runs.list(profile.id)
    )


def _benchmark(context: ToolContext) -> Benchmark:
    """The brief's benchmark: every run in a conversation uses it, so runs compare."""
    benchmarks: Sequence[Benchmark] = context.services.benchmarks()
    brief = _brief(context)
    wanted = (brief.benchmark_id if brief else None) or DEFAULT_BENCHMARK_ID
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
    benchmark = _benchmark(context)
    level = _reasoning_level(context)
    details: dict[str, object] = {
        "profile": record.alias,
        "version": record.version,
        "benchmark": benchmark.title,
        "reasoning": level or "model default",
    }
    extra = _system_prompt_extra(context)
    if extra:
        details["added to system prompt"] = extra
    return f"Benchmark {record.alias} with {benchmark.title}", details


async def run_benchmark(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    _check_can_run(context, arguments)
    services = context.services
    record = find_profile(context, text_argument(arguments, "profile"))
    benchmark = _benchmark(context)
    reasoning = _reasoning_level(context)
    extra = _system_prompt_extra(context)
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
            system_prompt_extra=extra,
            reasoning_level=reasoning,
            conversation_id=context.conversation_id or None,
        )
    except (BenchmarkBusyError, ValueError) as error:
        raise ToolError(str(error)) from error
    context.watch("benchmark", run.id)
    return json_result(
        {
            "run_id": run.id,
            "profile": record.alias,
            "benchmark": benchmark.id,
            "reasoning_level": reasoning,
            "system_prompt_extra": extra,
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
    if record.server_log:
        payload["server_log_tail"] = log_tail(record.server_log, NOTICE_LOG_LINES)
    if record.launch_settings is not None:
        try:
            profile = context.services.profiles.get(record.profile_id)
        except ProfileNotFoundError:
            pass
        else:
            # Empty when the profile still launches exactly as this run measured it.
            payload["differs_from_profile_now"] = settings_changes(
                launch_settings(profile), record.launch_settings
            )
    progress = context.services.benchmark_runner.progress(record.id)
    if progress is not None:
        payload["stage"] = progress.get("stage")
    return json_result(payload, f"{record.state}")


async def list_benchmark_runs(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    reference = text_argument(arguments, "profile", required=False)
    profile_id = find_profile(context, reference).id if reference else None
    limit = arguments.get("limit")
    count = limit if isinstance(limit, int) and not isinstance(limit, bool) else 10
    count = max(1, min(count, 30))
    records = context.services.benchmark_runs.list(profile_id)
    benchmark_id = text_argument(arguments, "benchmark_id", required=False)
    if benchmark_id:
        records = [record for record in records if record.benchmark_id == benchmark_id]
    runs = [run_summary(record) for record in records[:count]]
    return json_result(
        {"runs": runs, "total": len(records)}, f"{len(runs)} of {len(records)} runs"
    )


async def read_server_log(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    run_id = text_argument(arguments, "run_id", required=False)
    contains = text_argument(arguments, "contains", required=False)
    count = arguments.get("lines")
    count = count if isinstance(count, int) and not isinstance(count, bool) else 80
    count = max(1, min(count, 200))
    if run_id:
        record = _run(context, run_id)
        if not record.server_log:
            raise ToolError(
                f"run {record.id} {record.state} and kept no router log; only failed runs do"
            )
        lines: Sequence[str] = record.server_log
        source = f"benchmark run {record.id}"
    else:
        lines, source = context.services.supervisor.logs, "the router, most recent output"
    matching = sum(1 for line in lines if contains.lower() in line.lower()) if contains else None
    tail = log_tail(lines, count, contains)
    payload: dict[str, object] = {"source": source, "lines": tail, "kept": len(lines)}
    if matching is not None:
        payload["matching"] = matching
    return json_result(payload, f"{len(tail)} log lines from {source.split(',')[0]}")


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


# Lower is better for these; higher for the rest.
_LOWER_IS_BETTER = frozenset({"load", "peak RAM", "peak VRAM"})
def _baseline(context: ToolContext, record: BenchmarkRunRecord) -> BenchmarkRunRecord | None:
    """The run to compare with: this conversation's first comparable run, else the latest
    comparable one before it. Comparable means completed, earlier, and the same benchmark,
    reasoning level and added system prompt text."""
    runs = context.services.benchmark_runs

    def comparable(item: BenchmarkRunRecord) -> bool:
        return (
            item.id != record.id
            and item.state == "completed"
            and item.benchmark_id == record.benchmark_id
            and item.reasoning_level == record.reasoning_level
            and item.system_prompt_extra == record.system_prompt_extra
            and item.started_at <= record.started_at
        )

    # Newest first, so the conversation's first run is the last.
    mine = (
        [item for item in runs.list(conversation_id=context.conversation_id) if comparable(item)]
        if context.conversation_id
        else []
    )
    brief = _brief(context)
    for profile_id in (record.profile_id, brief.profile_id if brief else None):
        matches = [item for item in mine if item.profile_id == profile_id]
        if matches:
            return matches[-1]
    earlier = [item for item in runs.list(record.profile_id) if comparable(item)]
    return earlier[0] if earlier else None


def _figures(record: BenchmarkRunRecord) -> dict[str, float | None]:
    stats = record.stats or {}
    figures: dict[str, float | None] = {}
    for key, label, _ in KEY_STATS:
        value = stats.get(key)
        figures[label] = float(value) if isinstance(value, int | float) else None
    peak = peak_memory(record)
    figures["peak RAM"] = peak["ram_gib"]
    figures["peak VRAM"] = peak["vram_gib"]
    return figures


def comparison(baseline: BenchmarkRunRecord, record: BenchmarkRunRecord) -> dict[str, object]:
    """How a run differs from its baseline: the settings and each figure.

    How many tokens a run reads and writes varies a lot between runs of the same benchmark,
    so it is not compared: speed and time to finish are what count.
    """
    before, after = _figures(baseline), _figures(record)
    metrics: list[dict[str, object]] = []
    for label, old in before.items():
        new = after[label]
        change = round((new - old) / old * 100, 1) if old and new is not None else None
        better = None
        if change:
            better = (change < 0) == (label in _LOWER_IS_BETTER)
        metrics.append(
            {
                "metric": label,
                "baseline": old,
                "run": new,
                "change_percent": change,
                "better": better,
            }
        )
    return {
        "baseline_run_id": baseline.id,
        "baseline_profile": baseline.profile_alias,
        "settings_changes": (
            settings_changes(baseline.launch_settings, record.launch_settings)
            if baseline.launch_settings is not None and record.launch_settings is not None
            else None
        ),
        "metrics": metrics,
    }


async def evaluate_run(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = _run(context, text_argument(arguments, "run_id"))
    brief = _brief(context)
    if brief is None:
        raise ToolError("this conversation's brief could not be read")
    if record.state != "completed":
        raise ToolError(f"run {record.id} {record.state}, so it has no results to score")
    result = evaluation(brief, record)
    baseline_id = text_argument(arguments, "baseline_run_id", required=False)
    baseline = _run(context, baseline_id) if baseline_id else _baseline(context, record)
    if baseline is not None and baseline.state != "completed":
        raise ToolError(f"run {baseline.id} {baseline.state}, so it cannot be a baseline")
    summary = f"{result['targets_met']} of {brief_target_count(brief)} targets met"
    result["baseline"] = None
    if baseline is not None:
        compared = comparison(baseline, record)
        result["baseline"] = compared
        metrics = cast(list[dict[str, object]], compared["metrics"])
        decode = next(item for item in metrics if item["metric"] == "decode")
        if decode["change_percent"] is not None:
            summary += f"; decode {decode['change_percent']:+}% vs baseline"
    return json_result(result, summary)


def _running(context: ToolContext) -> BenchmarkRunRecord:
    run_id = context.services.benchmark_runner.active_run_id
    if run_id is None:
        raise ToolError("no benchmark is running")
    return _run(context, run_id)


async def describe_stop_benchmark(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    record = _running(context)
    return f"Stop the benchmark of {record.profile_alias}", {
        "run": record.id,
        "profile": record.profile_alias,
    }


async def stop_benchmark(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = _running(context)
    if not await context.services.benchmark_runner.cancel(record.id):
        raise ToolError(f"run {record.id} has already finished")
    return json_result(
        {
            "run_id": record.id,
            "profile": record.profile_alias,
            "state": "cancelled",
            "note": "The run is saved as cancelled with what it measured so far, and the router "
            "is stopped. Its figures cover only part of a run, so don't compare them with "
            "completed runs.",
        },
        "benchmark stopped",
    )


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
    return json_result(
        {
            "router": "running",
            "note": "Leave the router as you found it when this conversation began: stop it "
            "when you finish unless it was running then or the operator asks otherwise.",
        },
        "router started",
    )


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
        "Benchmark an enabled profile with the brief's benchmark and reasoning level, so "
        "every run compares: starts the router, loads only that profile, measures load "
        "time, prefill and decode speed and memory, then stops the router. Needs the "
        "router stopped. Runs in the background; you are notified when it ends.",
        object_schema(
            {"profile": {"type": "string", "description": "The profile's id or alias."}},
            ["profile"],
        ),
        "server.lifecycle.write",
        run_benchmark,
        approval="benchmarks",
        describe=describe_benchmark,
    ),
    Tool(
        "get_benchmark_run",
        "One benchmark run in full: state, error, every statistic, peak memory, and how the "
        "profile's settings now differ from what the run measured.",
        object_schema({"run_id": {"type": "string"}}, ["run_id"]),
        "server.read",
        get_benchmark_run,
    ),
    Tool(
        "list_benchmark_runs",
        "Recent benchmark runs, newest first, with their key figures. Runs with the same "
        "'settings' value measured the same launch settings.",
        object_schema(
            {
                "profile": {"type": "string", "description": "Only this profile."},
                "benchmark_id": {"type": "string", "description": "Only this benchmark."},
                "limit": {"type": "integer", "description": "How many, 1 to 30; default 10."},
            }
        ),
        "server.read",
        list_benchmark_runs,
    ),
    Tool(
        "read_server_log",
        "llama-server's own output, which says why a model failed to load or crashed (out "
        "of memory, an unknown argument, a bad model file). With run_id: the lines kept from "
        "that failed benchmark run. Without: the router's most recent output. Use contains "
        "to keep only lines with some text, such as 'error' or 'failed'.",
        object_schema(
            {
                "run_id": {"type": "string", "description": "A failed benchmark run."},
                "lines": {
                    "type": "integer",
                    "description": "How many, newest last; 1 to 200, default 80.",
                },
                "contains": {"type": "string", "description": "Only lines containing this text."},
            }
        ),
        "server.read",
        read_server_log,
    ),
    Tool(
        "evaluate_run",
        "Score a completed run against the brief's targets, in priority order, with the gap "
        "to each target, and compare it with a baseline run: the settings that differ and the "
        "change in each figure. The baseline defaults to this conversation's first comparable "
        "run.",
        object_schema(
            {"run_id": {"type": "string"}, "baseline_run_id": {"type": "string"}}, ["run_id"]
        ),
        "server.read",
        evaluate_run,
    ),
    Tool(
        "stop_benchmark",
        "Stop the running benchmark, for example when the operator asks, or when a run has gone "
        "wrong: the model repeating the same tool call, or its context filling up. The run is "
        "saved as cancelled with what it measured so far, and the router is stopped. You aren't "
        "woken while a run goes, only when it ends or the operator writes.",
        object_schema(),
        "server.lifecycle.write",
        stop_benchmark,
        approval="benchmarks",
        describe=describe_stop_benchmark,
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
        "Start the router with a runtime's enabled profiles, for example to restore it for "
        "the operator if it was running when you began.",
        object_schema({"runtime_id": {"type": "string"}}, ["runtime_id"]),
        "server.lifecycle.write",
        start_router,
        approval="benchmarks",
        describe=describe_start,
    ),
)
