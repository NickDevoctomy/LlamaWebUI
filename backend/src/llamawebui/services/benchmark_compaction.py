"""Keep a tool-using benchmark run's conversation inside the model's context.

Every round sends the whole history back to llama-server, so a coding agent that
rewrites a large file adds a copy of it each time. When the history plus room for the
next reply no longer fits, the runner compacts it, the way OpenCode does:

1. Prune: in all but the latest rounds, replace bulky tool arguments and results with
   short placeholders. The files are still in the workspace, so nothing is lost.
2. Summarise: if that frees too little, ask the model for a summary of the session and
   carry on from the task, the summary and a note listing the workspace's files.

These helpers work on the message list alone; the runner decides when to call them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import cast

# Characters per token assumed until a request reports its real size.
DEFAULT_TOKENS_PER_CHARACTER = 0.3
# Room always kept free for the next reply, unless the context is too small for it.
RESERVE_FLOOR_TOKENS = 8192
# The latest rounds pruning leaves whole, so the model keeps what it is working on.
KEEP_ROUNDS = 2
# Tool arguments and results longer than this are replaced when pruned.
PRUNE_MIN_CHARACTERS = 400
# Pruning has to free at least this share of the history to count; otherwise summarise.
PRUNE_MIN_SHARE = 0.1
# The longest summary asked for, as OpenCode caps its own.
SUMMARY_MAX_TOKENS = 4096
# Compactions a run may make before it is ended, so one that cannot progress stops.
MAX_COMPACTIONS = 8
# The workspace listing given with a summary is cut to this many characters.
LISTING_CHARACTERS = 2000

SUMMARY_REQUEST = (
    "Your context is nearly full, so this conversation is about to be replaced by a "
    "summary. Write that summary now as a prompt for continuing the job, for someone who "
    "will not see the conversation above. Cover: the goal and any requirements from the "
    "task; what has been done so far; the files in the workspace and what each holds; "
    "important decisions and why they were made; problems found and whether they are "
    "fixed; and what remains to be done next, in order. Be specific and concise. Do not "
    "call any tools and do not repeat whole files: they stay in the workspace."
)
SUMMARY_LEAD = "Summary of my work so far, written when the context was compacted:\n\n"
_OMITTED_ROUNDS = (
    "[Earlier rounds of this conversation were left out here so the summary request fits "
    "the context.]"
)

_CONTEXT_SIZE = re.compile(r"context size \((\d+) tokens\)", re.IGNORECASE)


def context_size_from_error(message: str) -> int | None:
    """The context size llama-server names when it refuses a request as too long."""
    match = _CONTEXT_SIZE.search(message)
    return int(match.group(1)) if match else None


def history_characters(messages: Sequence[Mapping[str, object]]) -> int:
    """The characters a history sends: its text and every tool call's arguments."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        for call in _calls(message):
            function = call.get("function")
            if isinstance(function, dict):
                total += len(str(function.get("name") or ""))
                total += len(str(function.get("arguments") or ""))
    return total


def reserve_tokens(context_size: int, largest_reply: int) -> int:
    """Tokens to keep free for the next reply: the largest so far, with a floor.

    A model that writes 13k-token files needs 13k free. The floor shrinks for a small
    context, and the reserve never takes more than half of it.
    """
    floor = min(RESERVE_FLOOR_TOKENS, context_size // 4)
    return min(max(floor, largest_reply), context_size // 2)


def _calls(message: Mapping[str, object]) -> list[dict[str, object]]:
    calls = message.get("tool_calls")
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _head_length(messages: Sequence[Mapping[str, object]]) -> int:
    """How many messages open the history and are never pruned or dropped.

    That is the system prompt and the task, and after a summary, the summary and the
    note that follows it: everything before the first reply that called tools.
    """
    for index, message in enumerate(messages):
        if message.get("role") == "assistant" and _calls(message):
            return index
    return len(messages)


def _rounds(messages: Sequence[Mapping[str, object]], start: int) -> list[tuple[int, int]]:
    """(first, end) message positions of each round after ``start``: a reply and its results."""
    rounds: list[tuple[int, int]] = []
    for index in range(start, len(messages)):
        if messages[index].get("role") == "assistant":
            if rounds:
                rounds[-1] = (rounds[-1][0], index)
            rounds.append((index, len(messages)))
    return rounds


def compactable(messages: Sequence[Mapping[str, object]]) -> bool:
    """Whether the history holds any rounds of tool calls to prune or summarise."""
    return bool(_rounds(messages, _head_length(messages)))


def _placeholder(size: int, what: str) -> str:
    return f"[{size:,} bytes {what}]"


def _prune_arguments(name: str, arguments: str) -> str:
    """A call's arguments with long string values replaced; still a JSON object."""
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments
    if not isinstance(parsed, dict):
        return arguments
    changed = False
    for key, value in parsed.items():
        if isinstance(value, str) and len(value) > PRUNE_MIN_CHARACTERS:
            size = len(value.encode("utf-8"))
            what = (
                "written to the workspace; read_file shows the file as it is now"
                if name == "write_file" and key == "content"
                else "left out to save context"
            )
            parsed[key] = _placeholder(size, what)
            changed = True
    return json.dumps(parsed, ensure_ascii=False) if changed else arguments


def prune(messages: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """The history with old tool payloads replaced by placeholders.

    Every message and tool-call id is kept, so the history stays valid for the chat
    template; only long arguments and results outside the latest rounds shrink.
    """
    pruned = [dict(message) for message in messages]
    rounds = _rounds(pruned, _head_length(pruned))
    old = rounds[:-KEEP_ROUNDS] if len(rounds) > KEEP_ROUNDS else []
    for first, end in old:
        reply = pruned[first]
        named: dict[str, tuple[str, str | None]] = {}
        calls = []
        for call in _calls(reply):
            function = dict(cast(dict[str, object], call.get("function") or {}))
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "")
            try:
                path = json.loads(arguments).get("path")
            except (json.JSONDecodeError, AttributeError):
                path = None
            named[str(call.get("id"))] = (name, path if isinstance(path, str) else None)
            function["arguments"] = _prune_arguments(name, arguments)
            calls.append({**call, "function": function})
        if calls:
            reply["tool_calls"] = calls
        for index in range(first + 1, end):
            result = pruned[index]
            content = result.get("content")
            if result.get("role") != "tool" or not isinstance(content, str):
                continue
            if len(content) <= PRUNE_MIN_CHARACTERS:
                continue
            name, path = named.get(str(result.get("tool_call_id")), ("tool", None))
            target = f"{name} {path}" if path else name
            result["content"] = _placeholder(
                len(content.encode("utf-8")),
                f"of {target} output cleared to save context; call it again if you need it",
            )
    return pruned


def summary_request(
    messages: Sequence[Mapping[str, object]], budget_characters: int
) -> list[dict[str, object]] | None:
    """The messages that ask for a summary, or None when there is nothing to summarise.

    Rounds are left out oldest first until the history fits ``budget_characters``; the
    head and the latest round always stay.
    """
    head = _head_length(messages)
    rounds = _rounds(messages, head)
    if not rounds:
        return None
    request = [dict(message) for message in messages]
    dropped = 0
    while (
        dropped < len(rounds) - 1
        and history_characters(request) + len(SUMMARY_REQUEST) > budget_characters
    ):
        dropped += 1
        cut = rounds[dropped][0]
        request = [
            *[dict(message) for message in messages[:head]],
            {"role": "user", "content": _OMITTED_ROUNDS},
            *[dict(message) for message in messages[cut:]],
        ]
    return [*request, {"role": "user", "content": SUMMARY_REQUEST}]


def compacted(
    messages: Sequence[Mapping[str, object]], summary: str, listing: str | None, tasks: bool
) -> list[dict[str, object]]:
    """The new history: the system prompt and task, the summary, and a note to carry on."""
    head = [dict(messages[0])] if messages and messages[0].get("role") == "system" else []
    # The first user message is the task; an earlier summary's note comes after it.
    task = next((message for message in messages if message.get("role") == "user"), None)
    if task is not None:
        head.append(dict(task))
    note = [
        "The conversation was compacted into the summary above to free context. Every file "
        "you wrote is still in the workspace; use read_file to see one before you change it."
    ]
    if tasks:
        note.append("Call list_tasks to see which tasks are still open.")
    if listing:
        cut = listing if len(listing) <= LISTING_CHARACTERS else listing[:LISTING_CHARACTERS] + "…"
        note.append(f"Files in the workspace now:\n{cut}")
    note.append("Continue with the next steps.")
    return [
        *head,
        {"role": "assistant", "content": SUMMARY_LEAD + summary.strip()},
        {"role": "user", "content": "\n\n".join(note)},
    ]
