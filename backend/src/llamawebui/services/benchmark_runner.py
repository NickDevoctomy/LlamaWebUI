"""Benchmark a profile: start the router, load the model, send a prompt, measure it.

One run at a time, and only from a stopped router, so nothing else shares the
machine while it is measured. The router is always stopped again afterwards.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.domain.benchmark import Benchmark, SystemPrompt
from llamawebui.models import BenchmarkRunRecord, ModelProfileRecord
from llamawebui.services.benchmark_workspace import (
    BenchmarkWorkspace,
    WorkspaceError,
    remove_run_folder,
    run_folder,
)
from llamawebui.services.router_client import RouterModel, RouterUnavailableError

LOGGER = logging.getLogger("llamawebui.benchmarks")
# The output limit a coding client usually gives a model: room for a thinking model to
# reason and then write its files, while still stopping a run that never ends.
DEFAULT_MAX_TOKENS = 32768
# Speeds are measured over this window, matching llama.cpp's own tg_3s figure.
RATE_WINDOW_SECONDS = 3.0


# Reasoning levels a run can ask for. "off" turns thinking off; the rest are passed to
# the chat template as reasoning_effort, and a template refuses levels it lacks.
REASONING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def reasoning_options(level: str | None) -> dict[str, object]:
    """Request fields that set a reasoning level; none keeps the model's own default."""
    if level is None:
        return {}
    if level == "off":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {"reasoning_effort": level}


# Rounds of tool calls a single run may make before it is stopped.
MAX_TOOL_ROUNDS = 24
TOOL_SYSTEM_PROMPT = (
    "You have a workspace folder for this task. Use the write_file tool to save files "
    "you create, read_file to read them back, and list_files to see what is there. "
    "Paths are relative to the workspace, like 'tetris/index.html'. You cannot read "
    "or write anything outside it."
)
_PATH = {"type": "string", "description": "Relative path inside the workspace."}
FILE_TOOLS: tuple[dict[str, object], ...] = (
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or replace a text file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": _PATH,
                    "content": {"type": "string", "description": "The whole file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the workspace.",
            "parameters": {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List the files in the workspace with their sizes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
)


class BenchmarkClient(Protocol):
    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]: ...

    async def load_model(self, model: str) -> None: ...

    def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        max_tokens: int,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]: ...


def _call_tool(
    workspace: BenchmarkWorkspace, name: str, arguments: str
) -> tuple[str, dict[str, object]]:
    """Run one file tool; return the reply for the model and a log entry."""
    entry: dict[str, object] = {"name": name, "path": None, "ok": False}
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        result = "Error: the tool arguments were not a JSON object."
        entry["result"] = result
        return result, entry
    path = parsed.get("path")
    entry["path"] = path if isinstance(path, str) else None
    try:
        if name == "write_file":
            result = workspace.write(path, parsed.get("content"))
        elif name == "read_file":
            result = workspace.read(path)
        elif name == "list_files":
            result = workspace.listing()
        else:
            raise WorkspaceError(f"there is no tool called '{name}'")
        entry["ok"] = True
    except WorkspaceError as error:
        result = f"Error: {error}"
    # File contents read back would bloat the log, so it keeps a short excerpt.
    entry["result"] = result if len(result) <= 300 else result[:300] + "…"
    return result, entry


class BenchmarkBusyError(RuntimeError):
    pass


class BenchmarkRunNotFoundError(LookupError):
    pass


class _WindowRate:
    """Speed over a recent window, from cumulative token counts and elapsed seconds.

    llama.cpp reports running totals, so a rate taken straight from them is the
    average so far. Differences over a short window give the current speed.
    """

    def __init__(self, window_seconds: float) -> None:
        self._window = window_seconds
        self._points: deque[tuple[float, float]] = deque()

    def add(self, seconds: float, tokens: float) -> tuple[float, float] | None:
        """Record a running total; return (tokens per second, span measured over)."""
        self._points.append((seconds, tokens))
        # Keep the newest point at least a full window old as the window's start.
        while len(self._points) > 2 and seconds - self._points[1][0] >= self._window:
            self._points.popleft()
        start_seconds, start_tokens = self._points[0]
        span = seconds - start_seconds
        if span <= 0 or tokens < start_tokens:
            return None
        return (tokens - start_tokens) / span, span


class _Timeline:
    """What generation spent its time on, entry by entry.

    Entries are: reading the prompt, thinking, answering, writing a tool call, and
    running a tool. Times are seconds from when the prompt was sent. Thinking and
    answer entries point into the run's reasoning and output text by position
    instead of copying it.
    """

    _TEXT = {"thinking": "reasoning", "answer": "output"}

    def __init__(
        self,
        entries: list[dict[str, object]],
        *,
        elapsed: Callable[[], float],
        decoded: Callable[[], float],
        text_length: Callable[[str], int],
    ) -> None:
        self._entries = entries
        self._elapsed = elapsed
        self._decoded = decoded
        self._text_length = text_length

    def _open(self) -> dict[str, object] | None:
        last = self._entries[-1] if self._entries else None
        return last if last is not None and last["end"] is None else None

    def switch(self, kind: str, round_number: int, **fields: object) -> dict[str, object]:
        """Continue the open entry if it is the same kind, or start a new one."""
        current = self._open()
        if current is not None and current["kind"] == kind and current["round"] == round_number:
            return current
        self.close()
        entry: dict[str, object] = {
            "kind": kind,
            "round": round_number,
            "start": round(self._elapsed(), 2),
            "end": None,
            **fields,
        }
        if kind in ("thinking", "answer", "tool_call"):
            entry["decoded_from"] = self._decoded()
        if kind in self._TEXT:
            entry["from"] = self._text_length(self._TEXT[kind])
        self._entries.append(entry)
        return entry

    def close(self, **fields: object) -> None:
        current = self._open()
        if current is None:
            return
        current["end"] = round(self._elapsed(), 2)
        current.update(fields)
        if "decoded_from" in current:
            start = cast(float, current.pop("decoded_from"))
            current["tokens"] = max(int(self._decoded() - start), 0)
        kind = cast(str, current["kind"])
        if kind in self._TEXT:
            current["to"] = self._text_length(self._TEXT[kind])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


class BenchmarkRunRegistry:
    def __init__(self, engine: Engine, workspace_root: Path) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self.workspace_root = workspace_root

    def workspace(self, run_id: str) -> BenchmarkWorkspace:
        return BenchmarkWorkspace(run_folder(self.workspace_root, run_id))

    def create(
        self,
        profile: ModelProfileRecord,
        benchmark: Benchmark,
        max_tokens: int,
        *,
        tools: bool = False,
        system_prompt: SystemPrompt | None = None,
        reasoning_level: str | None = None,
    ) -> BenchmarkRunRecord:
        with self._sessions() as session:
            record = BenchmarkRunRecord(
                id=str(uuid4()),
                profile_id=profile.id,
                profile_alias=profile.alias,
                profile_version=profile.version,
                runtime_id=profile.runtime_id,
                benchmark_id=benchmark.id,
                benchmark_title=benchmark.title,
                prompt=benchmark.prompt,
                max_tokens=max_tokens,
                tools=tools,
                tool_calls=[],
                system_prompt_id=system_prompt.id if system_prompt else None,
                system_prompt=system_prompt.text if system_prompt else None,
                reasoning_level=reasoning_level,
                state="running",
                stage="starting",
                stats={},
                samples=[],
                output="",
                reasoning="",
            )
            session.add(record)
            session.commit()
            return record

    def update(self, run_id: str, **fields: object) -> None:
        with self._sessions() as session:
            record = session.get(BenchmarkRunRecord, run_id)
            if record is None:
                return
            for name, value in fields.items():
                setattr(record, name, value)
            session.commit()

    def get(self, run_id: str) -> BenchmarkRunRecord:
        with self._sessions() as session:
            record = session.get(BenchmarkRunRecord, run_id)
            if record is None:
                raise BenchmarkRunNotFoundError(f"benchmark run not found: {run_id}")
            return record

    def list(self, profile_id: str | None = None) -> list[BenchmarkRunRecord]:
        with self._sessions() as session:
            query = select(BenchmarkRunRecord).order_by(BenchmarkRunRecord.started_at.desc())
            if profile_id is not None:
                query = query.where(BenchmarkRunRecord.profile_id == profile_id)
            return list(session.scalars(query))

    def delete(self, run_id: str) -> None:
        """Delete a finished run. Results written to its profile are kept."""
        with self._sessions() as session:
            record = session.get(BenchmarkRunRecord, run_id)
            if record is None:
                raise BenchmarkRunNotFoundError(f"benchmark run not found: {run_id}")
            if record.state == "running":
                raise BenchmarkBusyError("cancel the benchmark before deleting it")
            session.delete(record)
            session.commit()
        remove_run_folder(self.workspace_root, run_id)

    def record_result(self, profile_id: str, benchmark_id: str, result: dict[str, object]) -> None:
        """Write a completed result onto its profile, as the latest for that benchmark."""
        with self._sessions() as session:
            profile = session.get(ModelProfileRecord, profile_id)
            if profile is None:
                return
            # Assign a new dict so the JSON column is seen as changed.
            profile.benchmark_results = {**(profile.benchmark_results or {}), benchmark_id: result}
            session.commit()

    def prune_folders(self) -> int:
        """Delete the files of runs that no longer exist, such as a deleted profile's."""
        if not self.workspace_root.is_dir():
            return 0
        with self._sessions() as session:
            known = set(session.scalars(select(BenchmarkRunRecord.id)))
        removed = 0
        for folder in self.workspace_root.iterdir():
            if folder.name not in known:
                try:
                    remove_run_folder(self.workspace_root, folder.name)
                except ValueError:
                    continue  # Not a run folder; leave it alone.
                removed += 1
        return removed

    def fail_interrupted(self) -> int:
        """Close runs that were still running when the app last stopped."""
        with self._sessions() as session:
            records = list(
                session.scalars(
                    select(BenchmarkRunRecord).where(BenchmarkRunRecord.state == "running")
                )
            )
            for record in records:
                record.state = "failed"
                record.error = "the app stopped while this benchmark was running"
                record.finished_at = _now()
            session.commit()
            return len(records)


class BenchmarkRunner:
    def __init__(
        self,
        runs: BenchmarkRunRegistry,
        client: BenchmarkClient,
        *,
        start_router: Callable[[str], Awaitable[object]],
        stop_router: Callable[[], Awaitable[None]],
        router_stopped: Callable[[], bool],
        clock: Callable[[], float] = monotonic,
        sample_seconds: float = 0.5,
        load_timeout_seconds: float = 900.0,
    ) -> None:
        self._runs = runs
        self._workspace_root = runs.workspace_root
        self._client = client
        self._start_router = start_router
        self._stop_router = stop_router
        self._router_stopped = router_stopped
        self._clock = clock
        self._sample_seconds = sample_seconds
        self._load_timeout = load_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._active: str | None = None
        # The running benchmark's progress, merged into its record until it finishes.
        self._progress: dict[str, object] = {}

    @property
    def active_run_id(self) -> str | None:
        return self._active

    def generation_elapsed(self, run_id: str) -> float | None:
        """Seconds since the running benchmark sent its prompt, on the timeline's clock."""
        origin = self._progress.get("generation_origin") if run_id == self._active else None
        return round(self._clock() - origin, 2) if isinstance(origin, float) else None

    def progress(self, run_id: str) -> dict[str, object] | None:
        return dict(self._progress) if run_id == self._active else None

    def start(
        self,
        profile: ModelProfileRecord,
        benchmark: Benchmark,
        max_tokens: int,
        *,
        tools: bool = False,
        system_prompt: SystemPrompt | None = None,
        reasoning_level: str | None = None,
    ) -> BenchmarkRunRecord:
        if reasoning_level is not None and reasoning_level not in REASONING_LEVELS:
            raise ValueError(f"unknown reasoning level: {reasoning_level}")
        if self._task is not None and not self._task.done():
            raise BenchmarkBusyError("a benchmark is already running")
        if not self._router_stopped():
            raise BenchmarkBusyError("stop the router before running a benchmark")
        if not profile.enabled:
            raise BenchmarkBusyError("enable the profile before benchmarking it")
        record = self._runs.create(
            profile,
            benchmark,
            max_tokens,
            tools=tools,
            system_prompt=system_prompt,
            reasoning_level=reasoning_level,
        )
        self._active = record.id
        self._progress = {
            "stage": "starting",
            "stats": {},
            "samples": [],
            "output": "",
            "reasoning": "",
            "tool_calls": [],
            "timeline": [],
        }
        self._task = asyncio.create_task(self._run(record, benchmark.prompt))
        return record

    async def cancel(self, run_id: str) -> bool:
        task = self._task
        if run_id != self._active or task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task})
        return True

    async def wait(self) -> None:
        """Wait for the running benchmark, if any, to finish."""
        if self._task is not None:
            await asyncio.wait({self._task})

    def _stage(self, run_id: str, stage: str) -> None:
        self._progress["stage"] = stage
        self._runs.update(run_id, stage=stage, stats=dict(self._stats()))

    def _stats(self) -> dict[str, object]:
        return cast(dict[str, object], self._progress["stats"])

    def _samples(self) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], self._progress["samples"])

    async def _run(self, record: BenchmarkRunRecord, prompt: str) -> None:
        started = self._clock()
        stats = self._stats()
        state, error = "completed", None
        try:
            begun = self._clock()
            await self._start_router(record.runtime_id)
            stats["server_start_seconds"] = round(self._clock() - begun, 2)

            self._stage(record.id, "loading")
            begun = self._clock()
            try:
                await self._client.load_model(record.profile_alias)
            except RouterUnavailableError as caught:
                # The router can be slow to answer while it spawns the model process;
                # the model's status, polled next, says whether loading went ahead.
                LOGGER.info(
                    "benchmark_load_request_unanswered",
                    extra={"component": "benchmarks", "event": "load_slow", "error": str(caught)},
                )
            await self._wait_loaded(record.profile_alias)
            stats["load_seconds"] = round(self._clock() - begun, 2)

            self._stage(record.id, "prefill")
            await self._generate(record, prompt)
        except asyncio.CancelledError:
            state, error = "cancelled", "cancelled"
        except Exception as caught:
            state = "failed"
            error = str(getattr(caught, "detail", None) or caught) or type(caught).__name__
            LOGGER.warning(
                "benchmark_failed",
                extra={"component": "benchmarks", "event": "failed", "error": error},
            )
        finally:
            # A finished run is done; otherwise keep the step it stopped at.
            stage = "done" if state == "completed" else cast(str, self._progress["stage"])
            self._progress["stage"] = "stopping"
            try:
                await self._stop_router()
            except Exception as caught:  # The run is over either way; report and move on.
                LOGGER.warning(
                    "benchmark_stop_failed",
                    extra={"component": "benchmarks", "event": "stop_failed", "error": str(caught)},
                )
            stats["total_seconds"] = round(self._clock() - started, 2)
            self._runs.update(
                record.id,
                state=state,
                stage=stage,
                error=error,
                stats=dict(stats),
                samples=list(self._samples()),
                output=self._progress["output"],
                reasoning=self._progress["reasoning"],
                tool_calls=list(cast(list[dict[str, object]], self._progress["tool_calls"])),
                timeline=list(cast(list[dict[str, object]], self._progress["timeline"])),
                finished_at=_now(),
            )
            if state == "completed":
                self._runs.record_result(
                    record.profile_id, record.benchmark_id, self._result(record)
                )
            self._active = None
            self._progress = {}

    async def _wait_loaded(self, alias: str) -> None:
        deadline = self._clock() + self._load_timeout
        unanswered: RouterUnavailableError | None = None
        while True:
            try:
                models = await self._client.list_models()
            except RouterUnavailableError as caught:
                # A busy router is not a failed load: keep asking until the deadline.
                unanswered, models = caught, ()
            model = next((item for item in models if item.id == alias), None)
            status = model.status if model is not None else {}
            if status.get("value") == "loaded":
                return
            exit_code = status.get("exit_code")
            if status.get("failed") is True or (
                status.get("value") == "unloaded" and isinstance(exit_code, int) and exit_code != 0
            ):
                raise RuntimeError(f"{alias} failed to load (exit code {exit_code})")
            if self._clock() >= deadline:
                reason = f"; the router last failed with: {unanswered}" if unanswered else ""
                raise TimeoutError(f"{alias} did not load within {self._load_timeout:g}s{reason}")
            await asyncio.sleep(self._sample_seconds)

    async def _generate(self, record: BenchmarkRunRecord, prompt: str) -> None:
        """Send the prompt, and keep going while the model calls its file tools.

        Speeds add up llama.cpp's own counters across rounds, so the prompt reading
        between tool calls never counts as decode time.
        """
        stats = self._stats()
        samples = self._samples()
        calls_made = cast(list[dict[str, object]], self._progress["tool_calls"])
        workspace = (
            BenchmarkWorkspace(run_folder(self._workspace_root, record.id))
            if record.tools
            else None
        )
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        # The chosen system prompt comes first; the workspace note follows it whenever the
        # model has file tools, since it needs to know paths are relative either way.
        instructions = [
            text
            for text in (record.system_prompt, TOOL_SYSTEM_PROMPT if workspace else None)
            if text
        ]
        if instructions:
            messages.insert(0, {"role": "system", "content": "\n\n".join(instructions)})
        totals = dict.fromkeys(("prompt_n", "prompt_ms", "predicted_n", "predicted_ms"), 0.0)
        budget = record.max_tokens
        sent = self._clock()
        self._progress["generation_origin"] = sent
        last_sample: dict[str, float] = {}
        chunks = 0
        windows = {phase: _WindowRate(RATE_WINDOW_SECONDS) for phase in ("prefill", "decode")}

        def sample(phase: str, seconds: float, tokens: float) -> None:
            """Record cumulative progress; samples hold the speed over the recent window."""
            measured = windows[phase].add(seconds, tokens)
            if measured is None:
                return
            rate, span = measured
            self._progress[phase] = {"tokens": tokens, "rate": round(rate, 2)}
            elapsed = self._clock() - sent
            if elapsed - last_sample.get(phase, float("-inf")) >= self._sample_seconds:
                last_sample[phase] = elapsed
                samples.append(
                    {
                        "t": round(elapsed, 2),
                        "phase": phase,
                        "tokens": tokens,
                        "rate": round(rate, 2),
                        "span": round(span, 2),
                    }
                )

        decoded_so_far = 0.0
        timeline = _Timeline(
            cast(list[dict[str, object]], self._progress["timeline"]),
            elapsed=lambda: self._clock() - sent,
            decoded=lambda: decoded_so_far,
            text_length=lambda key: len(cast(str, self._progress[key])),
        )

        for round_number in range(1, MAX_TOOL_ROUNDS + 1):
            stats["rounds"] = round_number
            timings: Mapping[str, object] = {}
            content = ""
            pending: dict[int, dict[str, str]] = {}
            round_chunks = 0
            reading = timeline.switch("reading", round_number)
            async for chunk in self._client.chat_stream(
                record.profile_alias,
                messages,
                max_tokens=max(budget, 1),
                tools=FILE_TOOLS if workspace is not None else (),
                options=reasoning_options(record.reasoning_level),
            ):
                progress = chunk.get("prompt_progress")
                if isinstance(progress, dict):
                    processed = _number(progress.get("processed"))
                    milliseconds = _number(progress.get("time_ms"))
                    if processed is not None and milliseconds is not None:
                        sample(
                            "prefill",
                            (totals["prompt_ms"] + milliseconds) / 1000,
                            totals["prompt_n"] + processed,
                        )
                if isinstance(chunk.get("timings"), dict):
                    timings = cast(Mapping[str, object], chunk["timings"])
                choices = chunk.get("choices")
                choice = choices[0] if isinstance(choices, list) and choices else {}
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    kind = (
                        "tool_call"
                        if delta.get("tool_calls")
                        else "thinking"
                        if delta.get("reasoning_content")
                        else "answer"
                        if delta.get("content")
                        else None
                    )
                    if kind is not None:
                        timeline.switch(kind, round_number)
                if isinstance(delta, dict) and self._take_delta(
                    delta, pending, separate=round_number > 1 and not content
                ):
                    if isinstance(delta.get("content"), str):
                        content += cast(str, delta["content"])
                    chunks += 1
                    round_chunks += 1
                    if "first_token_seconds" not in stats:
                        stats["first_token_seconds"] = round(self._clock() - sent, 2)
                        self._stage(record.id, "decoding")
                    # llama.cpp's own counters time generation alone; fall back to the
                    # wall clock and chunk count when a build does not stream them.
                    generated = _number(timings.get("predicted_n"))
                    decoding = _number(timings.get("predicted_ms"))
                    decoded_so_far = totals["predicted_n"] + (
                        generated if generated is not None else round_chunks
                    )
                    if generated is not None and decoding is not None:
                        sample(
                            "decode",
                            (totals["predicted_ms"] + decoding) / 1000,
                            totals["predicted_n"] + generated,
                        )
                    else:
                        sample("decode", self._clock() - sent, chunks)
                if isinstance(choice.get("finish_reason"), str):
                    stats["finish_reason"] = choice["finish_reason"]

            # The last token's counters arrive with the closing chunk.
            generated = _number(timings.get("predicted_n"))
            decoded_so_far = totals["predicted_n"] + (
                generated if generated is not None else round_chunks
            )
            timeline.close()
            reading["prompt_tokens"] = int(_number(timings.get("prompt_n")) or 0)
            if reading["end"] is None:
                reading["end"] = round(self._clock() - sent, 2)
            for key in totals:
                totals[key] += _number(timings.get(key)) or 0.0
            budget -= int(_number(timings.get("predicted_n")) or round_chunks)
            if workspace is None or not pending:
                break
            ordered = [pending[index] for index in sorted(pending)]
            for position, call in enumerate(ordered):
                call["id"] = call["id"] or f"call_{round_number}_{position}"
            for entry in reversed(cast(list[dict[str, object]], self._progress["timeline"])):
                if entry["kind"] == "tool_call" and entry["round"] == round_number:
                    entry["names"] = [call["name"] for call in ordered]
                    break
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in ordered
                    ],
                }
            )
            for call in ordered:
                timeline.switch("tool", round_number, name=call["name"], call=len(calls_made))
                result, entry = _call_tool(workspace, call["name"], call["arguments"])
                timeline.close(path=entry["path"], ok=entry["ok"])
                calls_made.append({"round": round_number, **entry})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            stats["tool_calls"] = len(calls_made)
            if budget <= 0:
                stats["finish_reason"] = "length"
                break
        else:
            stats["finish_reason"] = "tool_round_limit"

        stats["generation_seconds"] = round(self._clock() - sent, 2)
        if totals["prompt_ms"]:
            stats["prompt_tokens"] = int(totals["prompt_n"])
            stats["prompt_seconds"] = round(totals["prompt_ms"] / 1000, 2)
            stats["prompt_tokens_per_second"] = round(
                totals["prompt_n"] / totals["prompt_ms"] * 1000, 2
            )
        stats["generated_tokens"] = int(totals["predicted_n"]) or chunks
        if totals["predicted_ms"]:
            stats["decode_seconds"] = round(totals["predicted_ms"] / 1000, 2)
            stats["decode_tokens_per_second"] = round(
                totals["predicted_n"] / totals["predicted_ms"] * 1000, 2
            )
        if workspace is not None:
            stats["tool_calls"] = len(calls_made)
            stats["files_written"] = len(workspace.files())
        # Peak and end speeds use full windows only; a short span early on overstates.
        full = [
            cast(float, item["rate"])
            for item in samples
            if item["phase"] == "decode"
            and cast(float, item["span"]) >= RATE_WINDOW_SECONDS * 0.9
        ]
        if full:
            stats["decode_peak_tokens_per_second"] = max(full)
            stats["decode_final_tokens_per_second"] = full[-1]

    def _take_delta(
        self, delta: Mapping[str, object], pending: dict[int, dict[str, str]], *, separate: bool
    ) -> bool:
        """Add a streamed delta to the run's text and tool calls; true if it held any."""
        received = False
        for key, field in (("reasoning", "reasoning_content"), ("output", "content")):
            value = delta.get(field)
            if isinstance(value, str) and value:
                text = cast(str, self._progress[key])
                # Each round's answer starts on its own paragraph.
                gap = "\n\n" if key == "output" and separate and text else ""
                self._progress[key] = text + gap + value
                received = True
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for position, part in enumerate(tool_calls):
                if not isinstance(part, dict):
                    continue
                index = part.get("index")
                call = pending.setdefault(
                    index if isinstance(index, int) else position,
                    {"id": "", "name": "", "arguments": ""},
                )
                if isinstance(part.get("id"), str):
                    call["id"] = part["id"]
                function = part.get("function")
                if isinstance(function, dict):
                    if isinstance(function.get("name"), str):
                        call["name"] += function["name"]
                    if isinstance(function.get("arguments"), str):
                        call["arguments"] += function["arguments"]
                received = True
        return received

    def _result(self, record: BenchmarkRunRecord) -> dict[str, object]:
        stats = self._stats()
        keys = (
            "server_start_seconds",
            "load_seconds",
            "first_token_seconds",
            "prompt_tokens",
            "prompt_tokens_per_second",
            "generated_tokens",
            "decode_tokens_per_second",
            "decode_peak_tokens_per_second",
            "decode_final_tokens_per_second",
            "total_seconds",
        )
        return {
            "run_id": record.id,
            "benchmark_title": record.benchmark_title,
            "profile_version": record.profile_version,
            "reasoning_level": record.reasoning_level,
            "completed_at": _now().isoformat(),
            **{key: stats[key] for key in keys if key in stats},
        }
