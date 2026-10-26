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
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.domain.benchmark import Benchmark, SystemPrompt
from llamawebui.models import BenchmarkRunRecord, ModelProfileRecord
from llamawebui.services import benchmark_tasks, benchmark_typescript
from llamawebui.services.benchmark_linter import BenchmarkLinter, LinterError
from llamawebui.services.benchmark_typescript import (
    BUNDLE_MARKER,
    DEFAULT_ENTRY,
    DEFAULT_TEMPLATE,
    OUTPUT,
)
from llamawebui.services.benchmark_workspace import (
    BenchmarkWorkspace,
    WorkspaceError,
    remove_run_folder,
    run_folder,
)
from llamawebui.services.chat_stream import (
    assistant_message,
    delta_kind,
    merge_tool_calls,
    ordered_calls,
    shorten,
)
from llamawebui.services.launch_settings import fingerprint, launch_settings
from llamawebui.services.router_client import RouterAPIError, RouterModel, RouterUnavailableError

LOGGER = logging.getLogger("llamawebui.benchmarks")
# No output limit: a run may use the profile's whole context, as an agent session can.
DEFAULT_MAX_TOKENS: int | None = None
# Speeds are measured over this window, matching llama.cpp's own tg_3s figure.
RATE_WINDOW_SECONDS = 3.0
# The most router log lines kept with a failed run.
SERVER_LOG_LINES = 200


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
# The tool an agent calls to say it is done. Only a run that calls it counts as completed.
COMPLETE_TOOL = "finish_job"
# Tools renamed since runs were recorded, so an old run can still be run again.
RENAMED_TOOLS = {"task_complete": COMPLETE_TOOL}
# The planning tools, for the agent's own task list. With complete_task, a run starts
# with its benchmark's tasks, and finish_job is refused while any are still open.
PLANNING_TOOLS = ("list_tasks", "create_task", "complete_task", "delete_task")
# What finish_job's final message must cover, as the tool and its refusal say it.
FINAL_MESSAGE_PARTS = (
    "a summary of what you built, the files you created, and your answer to: did you have "
    "all the tools you needed to do the job properly, and if not, what other tools would "
    "have helped?"
)
# The tool that lints a file; offered only once ESLint is installed.
CHECK_TOOL = "check_file"
# The TypeScript tools: offered only on a TypeScript benchmark, once they are installed.
TYPECHECK_TOOL = "check_typescript"
BUNDLE_TOOL = "bundle_typescript"
TYPESCRIPT_TOOLS = (TYPECHECK_TOOL, BUNDLE_TOOL)
_PATH = {"type": "string", "description": "Relative path inside the workspace."}
_TASK_ID = {"type": "integer", "description": "The task's number, from list_tasks."}
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
            "name": "edit_file",
            "description": (
                "Replace part of a text file in the workspace by finding exact text. Copy "
                "old_string from read_file output with its exact indentation, but without the "
                "line-number prefix such as '12: '. If there is no exact match, the edit retries "
                "ignoring typographic quote, dash and space variants, then trailing whitespace "
                "on each line. old_string must be found exactly once: add surrounding lines to "
                "make it unique, give the line it starts on, or set replace_all to change every "
                "occurrence. Use write_file to create a file or replace all of it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": _PATH,
                    "old_string": {"type": "string", "description": "Exact text to replace."},
                    "new_string": {
                        "type": "string",
                        "description": "Text to put in its place; must differ from old_string.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence instead of exactly one.",
                    },
                    "line": {
                        "type": "integer",
                        "description": (
                            "The line old_string starts on, to pick one of several matches."
                        ),
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file from the workspace. Each line is prefixed by its number as "
                "'12: '; the prefix is not part of the file."
            ),
            "parameters": {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": (
                "Delete a file from the workspace. Only files can be deleted, not folders; a "
                "folder left empty is removed with it."
            ),
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
    {
        "type": "function",
        "function": {
            "name": CHECK_TOOL,
            "description": (
                "Lint a JavaScript or HTML file in the workspace with ESLint, including the "
                "scripts inside an HTML page. Lists the problems found, each with the line and "
                "column it is on, matching read_file's line numbers."
            ),
            "parameters": {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": TYPECHECK_TOOL,
            # The description names the run's mode; see _offered.
            "description": "",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": BUNDLE_TOOL,
            "description": (
                "TypeScript only. Bundle a TypeScript entry module and everything it imports "
                f"into one script with esbuild, and write {OUTPUT} from the page template with "
                f"the script inline where the template has {BUNDLE_MARKER}. This is the only "
                f"way to make {OUTPUT}: never write it yourself. Run it again after every "
                "change to the TypeScript or the template."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry": {
                        "type": "string",
                        "description": f"The entry module. Defaults to {DEFAULT_ENTRY}.",
                    },
                    "template": {
                        "type": "string",
                        "description": (
                            f"The HTML page template. Defaults to {DEFAULT_TEMPLATE}."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": (
                "List your task list: each task's number, title and whether it is done. "
                "Deleted tasks are left out."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Add a task to your task list. Returns its number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "What the task is, in a sentence.",
                    }
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task on your task list as done, once its work is finished.",
            "parameters": {"type": "object", "properties": {"id": _TASK_ID}, "required": ["id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": (
                "Delete an open task that is no longer needed. The deletion stays on the "
                "record for whoever reviews the run."
            ),
            "parameters": {"type": "object", "properties": {"id": _TASK_ID}, "required": ["id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": COMPLETE_TOOL,
            "description": (
                "Say the job is finished. Call it once, after the last file is saved and every "
                "task on your task list is done, with your whole final message in "
                "final_message. It is refused while tasks are still open. Ending with a text "
                "reply instead leaves the job unfinished."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "final_message": {
                        "type": "string",
                        "description": f"Your whole final message, in one go: "
                        f"{FINAL_MESSAGE_PARTS}",
                    }
                },
                "required": ["final_message"],
            },
        },
    },
)


# Every tool by name, in the order they are offered.
TOOL_NAMES: tuple[str, ...] = tuple(
    cast(str, cast(Mapping[str, object], tool["function"])["name"]) for tool in FILE_TOOLS
)
# What each tool is for, as the workspace note puts it and the run form shows it.
TOOL_PURPOSES: dict[str, str] = {
    "write_file": "write_file to save files you create",
    "edit_file": "edit_file to change part of a file without writing it all again",
    "read_file": "read_file to read a file back",
    "delete_file": "delete_file to remove a file",
    "list_files": "list_files to see what is there",
    CHECK_TOOL: "check_file to lint a JavaScript or HTML file for mistakes",
    TYPECHECK_TOOL: "check_typescript to type-check your TypeScript",
    BUNDLE_TOOL: f"bundle_typescript to build {OUTPUT} from your TypeScript",
    "list_tasks": "list_tasks to see your task list",
    "create_task": "create_task to add a task to it",
    "complete_task": "complete_task to mark a task done",
    "delete_task": "delete_task to drop a task that is no longer needed",
}
# What each tool does, for the person choosing a run's tools.
TOOL_SUMMARIES: dict[str, str] = {
    "write_file": "Create or replace a whole file.",
    "edit_file": "Replace part of a file by finding its text.",
    "read_file": "Read a file back, with line numbers.",
    "delete_file": "Delete a file; never a folder.",
    "list_files": "List the files and their sizes.",
    CHECK_TOOL: "Lint a .js or .html file with ESLint and list the problems.",
    TYPECHECK_TOOL: (
        "Type-check the run's TypeScript with tsc, strict or not as the benchmark sets."
    ),
    BUNDLE_TOOL: (
        f"Bundle the TypeScript with esbuild into the page template, as {OUTPUT}. With it, "
        "finish_job waits for an up-to-date build."
    ),
    "list_tasks": "List the agent's task list and what is done.",
    "create_task": "Add a task to the agent's task list.",
    "complete_task": (
        "Mark a task done. With it, the run starts with the prompt's tasks and finish_job "
        "waits until none are open."
    ),
    "delete_task": "Drop an open task; it stays on the run, marked deleted.",
    COMPLETE_TOOL: (
        "Say the job is finished. Without it, a run is not marked completed or unfinished."
    ),
}


def tool_system_prompt(names: Sequence[str] = TOOL_NAMES, prepared: int = 0) -> str:
    """The workspace note for a run, naming only the tools it was given.

    prepared is how many tasks the run's task list starts with. They are stated as a
    fact, since an agent told a list might exist tends to plan its own instead.
    """
    purposes = [
        TOOL_PURPOSES[name] for name in TOOL_NAMES if name in names and name in TOOL_PURPOSES
    ]
    parts = ["You have a workspace folder for this task."]
    if purposes:
        listed = ", ".join(purposes[:-1]) + " and " if len(purposes) > 1 else ""
        parts.append(f"Use {listed}{purposes[-1]}.")
    parts.append(
        "Paths are relative to the workspace, like 'tetris/index.html'. You cannot read "
        "or write anything outside it."
    )
    if BUNDLE_TOOL in names:
        # Stated as the job's layout, not an option, so the agent writes TypeScript.
        parts.append(
            f"This is a TypeScript job. Write the code as TypeScript modules under src/, "
            f"starting from {DEFAULT_ENTRY}, and the page as {DEFAULT_TEMPLATE} with "
            f"{BUNDLE_MARKER} where the script goes. Build {OUTPUT} with {BUNDLE_TOOL} and "
            f"never write {OUTPUT} yourself."
        )
        if TYPECHECK_TOOL in names:
            parts.append(f"Run {TYPECHECK_TOOL} after changing the TypeScript.")
    if prepared and "list_tasks" in names:
        count = "1 task" if prepared == 1 else f"{prepared} tasks"
        parts.append(
            f"Your task list already holds {count} prepared for this job. Call list_tasks "
            "first to read them, follow them as your plan instead of making your own, and "
            "mark each one done with complete_task as you finish it."
        )
    if COMPLETE_TOOL in names:
        parts.append(
            f"When the job is finished, call {COMPLETE_TOOL} with your whole final message. A "
            "reply without a tool call ends the run, and a run that ends without it counts as "
            "unfinished."
        )
        if "complete_task" in names:
            parts.append(f"{COMPLETE_TOOL} is refused while any task on your list is still open.")
        if BUNDLE_TOOL in names:
            parts.append(f"It is also refused until {OUTPUT} is built from the latest TypeScript.")
    return " ".join(parts)


def choose_tools(
    names: Sequence[str] | None,
    available: Sequence[str] = TOOL_NAMES,
    *,
    typescript: bool = False,
) -> list[str]:
    """The tools a run is given, in offer order; None gives every available tool.

    The TypeScript tools are only for a TypeScript benchmark, and that benchmark gets
    them by default; without them installed, it cannot run with its default tools.
    """
    if names is None:
        if typescript and not all(name in available for name in TYPESCRIPT_TOOLS):
            raise ValueError(_TYPESCRIPT_MISSING)
        return [
            name
            for name in TOOL_NAMES
            if name in available and (typescript or name not in TYPESCRIPT_TOOLS)
        ]
    names = [RENAMED_TOOLS.get(name, name) for name in names]
    unknown = sorted(set(names) - set(TOOL_NAMES))
    if unknown:
        raise ValueError(f"unknown tools: {', '.join(unknown)}")
    if CHECK_TOOL in names and CHECK_TOOL not in available:
        raise ValueError("check_file needs ESLint: install it in Settings, under Benchmark")
    asked = [name for name in TYPESCRIPT_TOOLS if name in names]
    if asked and not typescript:
        raise ValueError(f"{', '.join(asked)} only work on a TypeScript benchmark")
    if any(name not in available for name in asked):
        raise ValueError(_TYPESCRIPT_MISSING)
    return [name for name in TOOL_NAMES if name in names]


_TYPESCRIPT_MISSING = (
    "the TypeScript tools are not installed: install or update ESLint in Settings, "
    "under Benchmark, which adds them"
)


# The longest failure reason a run keeps; the rest is echoed input, not the reason.
MAX_ERROR_LENGTH = 300


def _short_error(message: str) -> str:
    """Why a run failed, without the input llama.cpp echoes after its parse errors."""
    reason = message.split("; last read:", 1)[0].strip()
    return reason if len(reason) <= MAX_ERROR_LENGTH else reason[: MAX_ERROR_LENGTH - 1] + "…"


def _given_tools(record: BenchmarkRunRecord) -> list[str]:
    """The tools a run was given; runs from before tools could be chosen had them all."""
    if record.tool_names is None:
        return list(TOOL_NAMES)
    return [RENAMED_TOOLS.get(name, name) for name in record.tool_names]


def _typecheck_description(strict: bool) -> str:
    mode = (
        "strict mode, so every value needs a known type and null must be handled"
        if strict
        else "non-strict mode, so untyped values are allowed"
    )
    return (
        f"TypeScript only. Type-check every TypeScript file in the workspace with tsc, in "
        f"{mode}. Lists each type error with its file, line and column, matching "
        "read_file's line numbers. It does not build anything."
    )


def _offered(names: Sequence[str], *, strict: bool = False) -> tuple[dict[str, object], ...]:
    """The tools to send with a request; check_typescript says which mode it checks in."""
    offered: list[dict[str, object]] = []
    for tool, name in zip(FILE_TOOLS, TOOL_NAMES, strict=True):
        if name not in names:
            continue
        if name == TYPECHECK_TOOL:
            function = {**cast(dict[str, object], tool["function"])}
            function["description"] = _typecheck_description(strict)
            tool = {**tool, "function": function}
        offered.append(tool)
    return tuple(offered)


class BenchmarkClient(Protocol):
    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]: ...

    async def load_model(self, model: str) -> None: ...

    def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        max_tokens: int | None,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]: ...


def _call_tool(
    workspace: BenchmarkWorkspace,
    name: str,
    arguments: str,
    allowed: Sequence[str] = TOOL_NAMES,
    linter: BenchmarkLinter | None = None,
    tasks: list[dict[str, object]] | None = None,
    build: dict[str, object] | None = None,
    *,
    strict: bool = False,
    verbose: bool = False,
) -> tuple[str, dict[str, object]]:
    """Run one tool; return the reply for the model and a log entry.

    build keeps what the last bundle was made from, so finish_job can tell whether the
    page is out of date; strict is whether check_typescript checks in strict mode.
    verbose keeps the arguments exactly as the model sent them, and the whole result.
    """
    tasks = tasks if tasks is not None else []
    build = build if build is not None else {}
    entry: dict[str, object] = {"name": name, "path": None, "ok": False}
    if verbose:
        entry["arguments"] = arguments
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
        if name not in allowed:
            # A tool left out of this run is refused as if it did not exist.
            raise WorkspaceError(f"there is no tool called '{name}'")
        if name == "write_file":
            result = workspace.write(path, parsed.get("content"))
        elif name == "read_file":
            result = workspace.read_numbered(path)
        elif name == "edit_file":
            result = workspace.edit(
                path,
                parsed.get("old_string"),
                parsed.get("new_string"),
                replace_all=parsed.get("replace_all", False),
                line=parsed.get("line"),
            )
        elif name == "delete_file":
            result = workspace.delete(path)
        elif name == "list_files":
            result = workspace.listing()
        elif name == CHECK_TOOL:
            if linter is None:
                raise WorkspaceError("the linter is not installed")
            result = linter.check(workspace.read(path), cast(str, path))
        elif name == TYPECHECK_TOOL:
            if linter is None:
                raise WorkspaceError("TypeScript is not installed")
            result = benchmark_typescript.check(linter, workspace, strict=strict)
        elif name == BUNDLE_TOOL:
            if linter is None:
                raise WorkspaceError("TypeScript is not installed")
            template = parsed.get("template")
            result = benchmark_typescript.bundle(linter, workspace, parsed.get("entry"), template)
            build["template"] = template if isinstance(template, str) else DEFAULT_TEMPLATE
            build["fingerprint"] = _build_fingerprint(workspace, build)
        elif name == "list_tasks":
            result = benchmark_tasks.list_tasks(tasks)
        elif name == "create_task":
            result = benchmark_tasks.create_task(tasks, parsed.get("title"))
        elif name == "complete_task":
            result = benchmark_tasks.complete_task(tasks, parsed.get("id"))
        elif name == "delete_task":
            result = benchmark_tasks.delete_task(tasks, parsed.get("id"))
        elif name == COMPLETE_TOOL:
            final = parsed.get("final_message")
            if not isinstance(final, str) or not final.strip():
                # Refused, so the agent calls again with the message rather than ending without it.
                raise WorkspaceError(f"final_message is required. Give {FINAL_MESSAGE_PARTS}")
            # Open tasks only hold the job up when the agent can mark them done.
            unfinished = (
                benchmark_tasks.unfinished_reason(tasks) if "complete_task" in allowed else None
            )
            if unfinished:
                raise WorkspaceError(unfinished)
            unbuilt = _unbuilt_reason(workspace, build) if BUNDLE_TOOL in allowed else None
            if unbuilt:
                raise WorkspaceError(unbuilt)
            result = f"Job marked finished. {final.strip()}"
        else:
            raise WorkspaceError(f"there is no tool called '{name}'")
        entry["ok"] = True
    except (WorkspaceError, LinterError, benchmark_tasks.TaskError) as error:
        result = f"Error: {error}"
    # File contents read back would bloat the log, so it keeps a short excerpt. The final
    # message is kept whole: it is the agent's report, read in the run's tool call log, and
    # so is a refusal, which lists the tasks still open.
    keep_whole = verbose or name == COMPLETE_TOOL
    entry["result"] = result if keep_whole else shorten(result, 300)
    return result, entry


def _build_fingerprint(workspace: BenchmarkWorkspace, build: Mapping[str, object]) -> str:
    """A hash of the TypeScript, the template and the built page, as they are now."""
    paths = [*benchmark_typescript.typescript_sources(workspace), str(build["template"]), OUTPUT]
    return benchmark_typescript.fingerprint(workspace, paths)


def _unbuilt_reason(workspace: BenchmarkWorkspace, build: Mapping[str, object]) -> str | None:
    """Why a TypeScript job cannot finish yet, or None once its page is built and current."""
    if not benchmark_typescript.typescript_sources(workspace):
        return (
            f"the job is not finished: this is a TypeScript job, and there is no TypeScript "
            f"yet. Write the code as TypeScript modules under src/, starting from "
            f"{DEFAULT_ENTRY}, build {OUTPUT} with {BUNDLE_TOOL}, then call {COMPLETE_TOOL} "
            "again"
        )
    if "fingerprint" not in build:
        return (
            f"the job is not finished: {OUTPUT} has not been built. Run {BUNDLE_TOOL}, fix "
            f"anything it reports, then call {COMPLETE_TOOL} again"
        )
    if _build_fingerprint(workspace, build) != build["fingerprint"]:
        return (
            f"the job is not finished: the TypeScript, the template or {OUTPUT} changed after "
            f"the last build. Run {BUNDLE_TOOL} again, then call {COMPLETE_TOOL} again"
        )
    return None


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


# Stands in for the chunks of a request llama.cpp refused because it no longer fits.
_CONTEXT_FULL: dict[str, object] = {}


def _exceeds_context(error: RouterAPIError) -> bool:
    message = str(error).lower()
    return error.status_code == 400 and "context" in message and "exceed" in message


async def _until_context_full(
    stream: AsyncIterator[dict[str, object]],
) -> AsyncIterator[dict[str, object]]:
    """Pass a reply's chunks on; a request too long for the context ends in a marker."""
    try:
        async for chunk in stream:
            yield chunk
    except RouterAPIError as error:
        if not _exceeds_context(error):
            raise
        yield _CONTEXT_FULL


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
        max_tokens: int | None,
        *,
        tools: bool = False,
        tool_names: Sequence[str] | None = None,
        system_prompt: SystemPrompt | None = None,
        reasoning_level: str | None = None,
        tasks: Sequence[dict[str, object]] = (),
        typescript_strict: bool | None = None,
        verbose_log: bool = False,
        conversation_id: str | None = None,
    ) -> BenchmarkRunRecord:
        settings = launch_settings(profile)
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
                tool_names=list(tool_names) if tools and tool_names is not None else None,
                tool_calls=[],
                tasks=[dict(task) for task in tasks],
                typescript_strict=typescript_strict,
                verbose_log=verbose_log,
                system_prompt_id=system_prompt.id if system_prompt else None,
                system_prompt=system_prompt.text if system_prompt else None,
                reasoning_level=reasoning_level,
                state="running",
                stage="starting",
                stats={},
                samples=[],
                output="",
                reasoning="",
                launch_settings=settings,
                settings_fingerprint=fingerprint(settings),
                conversation_id=conversation_id,
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

    def list(
        self, profile_id: str | None = None, *, conversation_id: str | None = None
    ) -> list[BenchmarkRunRecord]:
        with self._sessions() as session:
            query = select(BenchmarkRunRecord).order_by(BenchmarkRunRecord.started_at.desc())
            if profile_id is not None:
                query = query.where(BenchmarkRunRecord.profile_id == profile_id)
            if conversation_id is not None:
                query = query.where(BenchmarkRunRecord.conversation_id == conversation_id)
            return list(session.scalars(query))

    def delete_many(
        self, run_ids: Sequence[str]
    ) -> tuple[Sequence[str], Sequence[Mapping[str, str]]]:
        """Delete finished runs: the ids deleted, and why each other one was skipped."""
        deleted: list[str] = []
        skipped: list[dict[str, str]] = []
        for run_id in dict.fromkeys(run_ids):
            try:
                self.delete(run_id)
            except (BenchmarkRunNotFoundError, BenchmarkBusyError) as error:
                skipped.append({"run_id": run_id, "reason": str(error)})
            else:
                deleted.append(run_id)
        return deleted, skipped

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
        linter: BenchmarkLinter | None = None,
        context: Callable[[str], tuple[int, int] | None] = lambda _alias: None,
        memory: Callable[[str], Mapping[str, object] | None] = lambda _alias: None,
        system_memory: Callable[[], Mapping[str, object] | None] = lambda: None,
        resource_seconds: float = 2.0,
        on_finished: Callable[[BenchmarkRunRecord], object] | None = None,
        log_mark: Callable[[], int] = lambda: 0,
        logs_since: Callable[[int], Sequence[str]] = lambda _mark: (),
    ) -> None:
        self._runs = runs
        # The router's log: a point to read from, and the lines after it, kept on failure.
        self._log_mark = log_mark
        self._logs_since = logs_since
        # Told about each run once it is saved as completed, failed or cancelled.
        self._on_finished = on_finished
        self._linter = linter
        # Tokens in use and the slot's size for a model, from llama-server's own log.
        self._context = context
        # A model process's memory as process_memory measures it, for the resource chart.
        self._memory = memory
        # Memory in use across the whole machine, so the chart shows what else holds it.
        self._system_memory = system_memory
        self._resource_seconds = resource_seconds
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

    def _observe_context(self, alias: str, stats: dict[str, object]) -> None:
        """Keep the context in use now, and the most the run has used, against its size."""
        reading = self._context(alias)
        if reading is None:
            return
        used, size = reading
        self._progress["context"] = {"used": used, "size": size}
        stats["context_size"] = size
        if used > cast(int, stats.get("context_peak", 0)):
            stats["context_peak"] = used

    def available_tools(self) -> list[str]:
        """Every tool a run can be given now; check_file waits for ESLint to be installed,
        and the TypeScript tools for an install that carries them."""
        linted = self._linter is not None and self._linter.installed
        typed = self._linter is not None and self._linter.typescript_installed
        return [
            name
            for name in TOOL_NAMES
            if (name != CHECK_TOOL or linted) and (name not in TYPESCRIPT_TOOLS or typed)
        ]

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
        max_tokens: int | None,
        *,
        tools: bool = False,
        tool_names: Sequence[str] | None = None,
        system_prompt: SystemPrompt | None = None,
        reasoning_level: str | None = None,
        verbose_log: bool = False,
        conversation_id: str | None = None,
    ) -> BenchmarkRunRecord:
        if reasoning_level is not None and reasoning_level not in REASONING_LEVELS:
            raise ValueError(f"unknown reasoning level: {reasoning_level}")
        chosen = choose_tools(
            tool_names if tools else [], self.available_tools(), typescript=benchmark.typescript
        )
        # A run given no tools at all runs without a workspace, as if tools were off.
        tools = tools and bool(chosen)
        if self._task is not None and not self._task.done():
            raise BenchmarkBusyError("a benchmark is already running")
        if not self._router_stopped():
            raise BenchmarkBusyError("stop the router before running a benchmark")
        if not profile.enabled:
            raise BenchmarkBusyError("enable the profile before benchmarking it")
        # The prompt's tasks are the agent's to work through only when it can mark them done.
        tasks = (
            benchmark_tasks.seed_tasks(benchmark.tasks)
            if tools and "complete_task" in chosen
            else []
        )
        record = self._runs.create(
            profile,
            benchmark,
            max_tokens,
            tools=tools,
            tool_names=chosen,
            system_prompt=system_prompt,
            reasoning_level=reasoning_level,
            tasks=tasks,
            # Kept only for a run that had the TypeScript tools, so it says how it was checked.
            typescript_strict=(
                benchmark.typescript_strict
                if tools and any(name in chosen for name in TYPESCRIPT_TOOLS)
                else None
            ),
            # Only tool calls are logged, so a run without tools has nothing to keep.
            verbose_log=tools and verbose_log,
            conversation_id=conversation_id,
        )
        self._active = record.id
        self._progress = {
            "stage": "starting",
            "stats": {},
            "samples": [],
            "resources": [],
            "output": "",
            "reasoning": "",
            "tool_calls": [],
            # The agent's task list, changed in place by the planning tools.
            "tasks": [dict(task) for task in tasks],
            # What the last bundle_typescript was built from, for finish_job to compare.
            "build": {},
            "timeline": [],
            # The tool call being streamed: its name and bytes of arguments so far.
            "tool_call": None,
            # Tokens in the model's context right now, and its size.
            "context": None,
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
        log_mark = self._log_mark()
        server_log: list[str] | None = None
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
            watcher = asyncio.create_task(self._watch_resources(record.profile_alias))
            try:
                await self._generate(record, prompt)
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
        except asyncio.CancelledError:
            state, error = "cancelled", "cancelled"
        except Exception as caught:
            state = "failed"
            error = _short_error(
                str(getattr(caught, "detail", None) or caught) or type(caught).__name__
            )
            LOGGER.warning(
                "benchmark_failed",
                extra={"component": "benchmarks", "event": "failed", "error": error},
            )
            # Why a load or generation failed is usually only in llama-server's output.
            server_log = list(self._logs_since(log_mark))[-SERVER_LOG_LINES:]
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
            if record.tools and COMPLETE_TOOL in _given_tools(record):
                # A run that failed or was cancelled never got to say it was done.
                stats.setdefault("outcome", "unfinished")
            self._runs.update(
                record.id,
                state=state,
                stage=stage,
                error=error,
                stats=dict(stats),
                samples=list(self._samples()),
                resources=list(cast(list[dict[str, object]], self._progress["resources"])),
                output=self._progress["output"],
                reasoning=self._progress["reasoning"],
                tool_calls=list(cast(list[dict[str, object]], self._progress["tool_calls"])),
                tasks=list(cast(list[dict[str, object]], self._progress["tasks"])),
                timeline=list(cast(list[dict[str, object]], self._progress["timeline"])),
                server_log=server_log,
                finished_at=_now(),
            )
            if state == "completed":
                self._runs.record_result(
                    record.profile_id, record.benchmark_id, self._result(record)
                )
            self._active = None
            self._progress = {}
            if self._on_finished is not None:
                try:
                    self._on_finished(self._runs.get(record.id))
                except Exception as caught:  # A listener's failure must not fail the run.
                    LOGGER.warning(
                        "benchmark_finished_hook_failed",
                        extra={
                            "component": "benchmarks",
                            "event": "hook_failed",
                            "error": str(caught),
                        },
                    )

    async def _watch_resources(self, alias: str) -> None:
        """Every few seconds while generating, note the context and memory in use."""
        resources = cast(list[dict[str, object]], self._progress["resources"])
        while True:
            origin = self._progress.get("generation_origin")
            if isinstance(origin, float):
                entry: dict[str, object] = {"t": round(self._clock() - origin, 2)}
                reading = self._context(alias)
                if reading is not None:
                    entry["context_used"], entry["context_size"] = reading
                try:
                    # Reading memory maps takes a moment, so it runs off the event loop.
                    memory = await asyncio.to_thread(self._memory, alias)
                except Exception:  # A missed reading leaves a gap in the chart, no more.
                    memory = None
                if memory is not None:
                    ram = _number(memory.get("ram_bytes"))
                    mapped = _number(memory.get("mapped_bytes")) or 0.0
                    vram = _number(memory.get("vram_bytes"))
                    if ram is not None:
                        entry["ram_bytes"] = int(ram + mapped)
                    if vram is not None:
                        entry["vram_bytes"] = int(vram)
                try:
                    system = await asyncio.to_thread(self._system_memory)
                except Exception:
                    system = None
                if system is not None:
                    for source, key in (
                        ("vram_bytes", "vram_total_bytes"),
                        ("ram_bytes", "ram_total_bytes"),
                    ):
                        total = _number(system.get(source))
                        if total is not None:
                            entry[key] = int(total)
                if len(entry) > 1:
                    resources.append(entry)
            await asyncio.sleep(self._resource_seconds)

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
        given = _given_tools(record)
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        # The chosen system prompt comes first; the workspace note follows it whenever the
        # model has file tools, since it needs to know paths are relative either way.
        instructions = [
            text
            for text in (
                record.system_prompt,
                tool_system_prompt(given, len(cast(list[object], self._progress["tasks"])))
                if workspace
                else None,
            )
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

        completed = False
        for round_number in range(1, MAX_TOOL_ROUNDS + 1):
            stats["rounds"] = round_number
            timings: Mapping[str, object] = {}
            content = ""
            pending: dict[int, dict[str, str]] = {}
            # Bytes of each tool call's arguments so far, by call index.
            sizes: dict[int, int] = {}
            round_chunks = 0
            exhausted = False
            finish: str | None = None
            reading = timeline.switch("reading", round_number)
            async for chunk in _until_context_full(
                self._client.chat_stream(
                    record.profile_alias,
                    messages,
                    max_tokens=max(budget, 1) if budget is not None else None,
                    tools=_offered(given, strict=bool(record.typescript_strict))
                    if workspace is not None
                    else (),
                    options=reasoning_options(record.reasoning_level),
                )
            ):
                if chunk is _CONTEXT_FULL:
                    exhausted = True
                    continue
                self._observe_context(record.profile_alias, stats)
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
                    kind = delta_kind(delta)
                    if kind is not None:
                        timeline.switch(kind, round_number)
                if isinstance(delta, dict) and self._take_delta(
                    delta, pending, sizes, separate=round_number > 1 and not content
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
                    finish = stats["finish_reason"] = choice["finish_reason"]

            # llama.cpp logs the slot's final size when the request stops, just after it.
            self._observe_context(record.profile_alias, stats)
            # The last token's counters arrive with the closing chunk.
            generated = _number(timings.get("predicted_n"))
            decoded_so_far = totals["predicted_n"] + (
                generated if generated is not None else round_chunks
            )
            timeline.close()
            self._progress["tool_call"] = None
            reading["prompt_tokens"] = int(_number(timings.get("prompt_n")) or 0)
            if reading["end"] is None:
                reading["end"] = round(self._clock() - sent, 2)
            for key in totals:
                totals[key] += _number(timings.get(key)) or 0.0
            if budget is not None:
                budget -= int(_number(timings.get("predicted_n")) or round_chunks)
            if exhausted:
                # The conversation no longer fits the profile's context, so the run ends here.
                stats["finish_reason"] = "context"
                break
            if workspace is None or not pending:
                break
            ordered = ordered_calls(pending, round_number)
            for entry in reversed(cast(list[dict[str, object]], self._progress["timeline"])):
                if entry["kind"] == "tool_call" and entry["round"] == round_number:
                    entry["names"] = [call["name"] for call in ordered]
                    entry["bytes"] = sum(sizes.values())
                    break
            if finish == "length":
                # Generation was cut off mid-call, so the arguments are half written. Running
                # the call would only hand back an error the model has no room left to act on.
                calls_made.extend(
                    {
                        "round": round_number,
                        "name": call["name"],
                        "path": None,
                        "ok": False,
                        "result": "Not run: generation was cut off before the call was finished.",
                    }
                    for call in ordered
                )
                stats["tool_calls"] = len(calls_made)
                # Without a token limit, only a full context stops generation this way.
                stats["finish_reason"] = "length" if budget is not None else "context"
                break
            messages.append(assistant_message(content, ordered))
            for call in ordered:
                timeline.switch("tool", round_number, name=call["name"], call=len(calls_made))
                # Off the event loop: linting starts Node.js, which takes a moment.
                result, entry = await asyncio.to_thread(
                    _call_tool,
                    workspace,
                    call["name"],
                    call["arguments"],
                    given,
                    self._linter,
                    cast(list[dict[str, object]], self._progress["tasks"]),
                    cast(dict[str, object], self._progress["build"]),
                    strict=bool(record.typescript_strict),
                    verbose=record.verbose_log,
                )
                timeline.close(path=entry["path"], ok=entry["ok"])
                calls_made.append({"round": round_number, **entry})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                completed = completed or (call["name"] == COMPLETE_TOOL and entry["ok"] is True)
            stats["tool_calls"] = len(calls_made)
            if completed:
                stats["finish_reason"] = COMPLETE_TOOL
                break
            if budget is not None and budget <= 0:
                stats["finish_reason"] = "length"
                break
        else:
            stats["finish_reason"] = "tool_round_limit"
        if workspace is not None and COMPLETE_TOOL in given:
            # Only an agent that said it was done finished the task; any other end did not.
            stats["outcome"] = "completed" if completed else "unfinished"

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
        self,
        delta: Mapping[str, object],
        pending: dict[int, dict[str, str]],
        sizes: dict[int, int],
        *,
        separate: bool,
    ) -> bool:
        """Add a streamed delta to the run's text and tool calls; true if it held any.

        A tool call's arguments carry whole files, so the call being written is shown
        live by its name and how many bytes of arguments have arrived.
        """
        received = False
        for key, field in (("reasoning", "reasoning_content"), ("output", "content")):
            value = delta.get(field)
            if isinstance(value, str) and value:
                text = cast(str, self._progress[key])
                # Each round's answer starts on its own paragraph.
                gap = "\n\n" if key == "output" and separate and text else ""
                self._progress[key] = text + gap + value
                received = True
        latest = merge_tool_calls(delta, pending, sizes)
        if latest is not None:
            self._progress["tool_call"] = latest
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
            "outcome",
            "context_peak",
            "context_size",
        )
        return {
            "run_id": record.id,
            "benchmark_title": record.benchmark_title,
            "profile_version": record.profile_version,
            "reasoning_level": record.reasoning_level,
            "completed_at": _now().isoformat(),
            **{key: stats[key] for key in keys if key in stats},
        }
