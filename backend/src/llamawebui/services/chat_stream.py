"""Pieces of an OpenAI-style streamed chat that every tool-using agent here needs.

The benchmark runner (llama.cpp) and the assistant (OpenRouter) both stream replies whose
tool calls arrive in fragments, and both send the calls back in the history.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import cast

# A tool call being streamed: its id, function name and the argument text so far.
PendingCall = dict[str, str]


async def completion_chunks(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, object]]:
    """The JSON chunks of a streamed completion, until ``[DONE]``.

    Comment lines (OpenRouter sends ``: OPENROUTER PROCESSING`` keep-alives) and other
    fields are skipped. Raises ``ValueError`` for a chunk that is not JSON.
    """
    async for line in lines:
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as error:
            raise ValueError("invalid completion chunk") from error
        if isinstance(chunk, dict):
            yield cast(dict[str, object], chunk)


def function_tool(
    name: str, description: str, parameters: Mapping[str, object]
) -> dict[str, object]:
    """A tool definition in the chat completions format."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": dict(parameters)},
    }


def delta_kind(delta: Mapping[str, object]) -> str | None:
    """What a delta carries: "tool_call", "thinking", "answer", or nothing."""
    if delta.get("tool_calls"):
        return "tool_call"
    if delta.get("reasoning_content") or delta.get("reasoning"):
        return "thinking"
    if delta.get("content"):
        return "answer"
    return None


def merge_tool_calls(
    delta: Mapping[str, object], pending: dict[int, PendingCall], sizes: dict[int, int]
) -> dict[str, object] | None:
    """Add a delta's tool-call fragments to ``pending``.

    Returns the name and argument bytes of the call written last, for showing the call
    live, or ``None`` when the delta had no tool calls. ``sizes`` counts argument bytes
    by call index.
    """
    tool_calls = delta.get("tool_calls")
    if not isinstance(tool_calls, list):
        return None
    latest: dict[str, object] | None = None
    for position, part in enumerate(tool_calls):
        if not isinstance(part, dict):
            continue
        index = part.get("index")
        slot = index if isinstance(index, int) else position
        call = pending.setdefault(slot, {"id": "", "name": "", "arguments": ""})
        if isinstance(part.get("id"), str):
            call["id"] = part["id"]
        function = part.get("function")
        if isinstance(function, dict):
            if isinstance(function.get("name"), str):
                call["name"] += function["name"]
            piece = function.get("arguments")
            if isinstance(piece, str):
                call["arguments"] += piece
                sizes[slot] = sizes.get(slot, 0) + len(piece.encode("utf-8"))
        latest = {"name": call["name"], "bytes": sizes.get(slot, 0)}
    return latest


def ordered_calls(pending: Mapping[int, PendingCall], round_number: int) -> list[PendingCall]:
    """A round's calls in order, each with an id; servers that omit one get a stable id."""
    ordered = [pending[index] for index in sorted(pending)]
    for position, call in enumerate(ordered):
        call["id"] = call["id"] or f"call_{round_number}_{position}"
    return ordered


def history_arguments(arguments: str) -> str:
    """A call's arguments as sent back to the server, which may reject any that are not JSON.

    A malformed call still gets its error as the tool result, so the model can retry it.
    """
    try:
        valid = isinstance(json.loads(arguments or "{}"), dict)
    except json.JSONDecodeError:
        valid = False
    return arguments if valid else "{}"


def assistant_message(content: str, calls: Sequence[PendingCall]) -> dict[str, object]:
    """The history entry for a reply that called tools."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": history_arguments(call["arguments"]),
                },
            }
            for call in calls
        ],
    }


def shorten(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, marked with an ellipsis when cut."""
    return text if len(text) <= limit else text[:limit] + "…"
