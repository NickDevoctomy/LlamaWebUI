"""Run assistant turns: stream the model's reply, run its tool calls, repeat.

A turn runs as a background task, so it carries on if the browser closes. One
conversation runs at a time. Everything said is stored as it happens; the reply being
streamed is kept in memory for the UI to poll.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from llamawebui.assistant.conversations import ConversationRegistry
from llamawebui.assistant.openrouter_client import ChatClient, OpenRouterError
from llamawebui.assistant.settings import AssistantSettings
from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    ToolServices,
    error_result,
    offered,
    resolve_call,
    run_call,
)
from llamawebui.models import AssistantConversationRecord, AssistantMessageRecord
from llamawebui.services.chat_stream import (
    PendingCall,
    assistant_message,
    merge_tool_calls,
    ordered_calls,
)

NOT_RUN = "Not run: the turn ended before this call ran."
REJECTED = (
    "The operator rejected this action. Do not retry it unless they ask; "
    "say what you would do instead."
)


class AssistantBusyError(RuntimeError):
    """Another turn is running, or this conversation is waiting to be continued."""


@dataclass(frozen=True, slots=True)
class TurnSetup:
    """What a turn needs that changes between turns: the model and the system prompt."""

    model_id: str
    # USD per token, for estimating a reply's cost when OpenRouter does not report it.
    pricing: Mapping[str, str]
    system_prompt: str


def is_notification(record: AssistantMessageRecord) -> bool:
    return record.role == "event" and (record.summary or {}).get("kind") == "notification"


def history(records: Sequence[AssistantMessageRecord]) -> list[dict[str, object]]:
    """The stored conversation as chat messages for the model.

    Events are for the reader only, except notifications about jobs the assistant
    started, which it reads as user messages. A notification that arrived while tools
    were running waits until their results are in, keeping each reply's calls and
    results together. A tool call that never got a result (the turn was stopped or the
    app restarted) is answered as not run, so the history stays valid.
    """
    messages: list[dict[str, object]] = []
    notes: list[dict[str, object]] = []
    answered = {record.tool_call_id for record in records if record.role == "tool"}
    for record in records:
        if record.role in {"user", "assistant"}:
            messages.extend(notes)
            notes.clear()
        if record.role == "user":
            messages.append({"role": "user", "content": record.content})
        elif is_notification(record):
            notes.append({"role": "user", "content": f"[Notification] {record.content}"})
        elif record.role == "assistant":
            message: dict[str, object] = {"role": "assistant", "content": record.content}
            if record.tool_calls:
                message["tool_calls"] = record.tool_calls
            messages.append(message)
            for call in record.tool_calls or ():
                if call.get("id") not in answered:
                    messages.append(
                        {"role": "tool", "tool_call_id": call.get("id"), "content": NOT_RUN}
                    )
        elif record.role == "tool":
            messages.append(
                {"role": "tool", "tool_call_id": record.tool_call_id, "content": record.content}
            )
    messages.extend(notes)
    return messages


def estimated_cost(usage: Mapping[str, object], pricing: Mapping[str, str]) -> float:
    """A reply's cost from its token counts, when OpenRouter did not report one."""

    def price(key: str) -> float:
        try:
            return max(float(pricing.get(key, "0")), 0.0)
        except ValueError:
            return 0.0

    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return (prompt if isinstance(prompt, int) else 0) * price("prompt") + (
        completion if isinstance(completion, int) else 0
    ) * price("completion")


class AssistantEngine:
    def __init__(
        self,
        conversations: ConversationRegistry,
        client: ChatClient,
        *,
        services: ToolServices,
        tools: Sequence[Tool],
        settings: Callable[[], AssistantSettings],
        setup: Callable[[AssistantConversationRecord], TurnSetup],
        retry_delays: Sequence[float] = (2.0, 5.0),
    ) -> None:
        self._conversations = conversations
        self._client = client
        self._services = services
        self._tools = tuple(tools)
        self._settings = settings
        self._setup = setup
        # Pauses before retrying a request the provider failed; one retry per entry.
        self._retry_delays = tuple(retry_delays)
        self._task: asyncio.Task[None] | None = None
        self._active: str | None = None
        self._progress: dict[str, object] = {}
        # Decisions awaited by the running turn, by approval id.
        self._approvals: dict[str, asyncio.Future[bool]] = {}
        # Background jobs a conversation started: (kind, job id) to the conversation and
        # the privileges of whoever started them, used if the outcome wakes it.
        self._watches: dict[tuple[str, str], tuple[str, frozenset[str]]] = {}
        # Conversations with a notification to act on once the assistant is free.
        self._wake: dict[str, frozenset[str]] = {}

    @property
    def active_conversation_id(self) -> str | None:
        return self._active

    @property
    def services(self) -> ToolServices:
        return self._services

    def progress(self, conversation_id: str) -> dict[str, object] | None:
        """The reply being streamed, while that conversation's turn runs."""
        return dict(self._progress) if self._active == conversation_id else None

    def send(
        self, conversation_id: str, text: str, privileges: frozenset[str]
    ) -> AssistantMessageRecord:
        """Add the user's message and start a turn answering it."""
        conversation = self._conversations.get(conversation_id)
        self._require_free(conversation)
        if conversation.state == "paused":
            raise AssistantBusyError(
                "this conversation reached its spending limit; continue it to raise the limit"
            )
        message = self._conversations.add_message(conversation_id, "user", text)
        self._start(conversation_id, privileges)
        return message

    def resume(self, conversation_id: str, privileges: frozenset[str], raise_by: float) -> None:
        """Raise a paused conversation's spending limit and carry on where it stopped."""
        conversation = self._conversations.get(conversation_id)
        self._require_free(conversation)
        if conversation.state != "paused":
            raise AssistantBusyError("only a paused conversation can be continued")
        self._conversations.update(
            conversation_id, spend_limit_usd=conversation.spend_limit_usd + raise_by
        )
        self._conversations.add_message(
            conversation_id,
            "event",
            f"Continued with the spending limit raised to "
            f"${conversation.spend_limit_usd + raise_by:.2f}.",
            summary={"kind": "continued"},
        )
        self._start(conversation_id, privileges)

    def retry(self, conversation_id: str, privileges: frozenset[str]) -> None:
        """Carry on a turn that failed or was interrupted, from where it stopped."""
        conversation = self._conversations.get(conversation_id)
        self._require_free(conversation)
        if conversation.state not in {"failed", "interrupted", "running", "awaiting_approval"}:
            raise AssistantBusyError("only a failed or interrupted conversation can be tried again")
        self._conversations.add_message(
            conversation_id, "event", "Trying again.", summary={"kind": "retry"}
        )
        self._start(conversation_id, privileges)

    async def stop(self, conversation_id: str) -> bool:
        task = self._task
        if self._active != conversation_id or task is None:
            return False
        task.cancel()
        await asyncio.wait({task})
        return True

    async def wait(self) -> None:
        """Wait until no turn runs, including any a notification started meanwhile."""
        while self._task is not None and not self._task.done():
            await asyncio.wait({self._task})

    def tool(self, name: str) -> Tool | None:
        return next((tool for tool in self._tools if tool.name == name), None)

    def decide(self, approval_id: str, approve: bool, decided_by: str) -> None:
        """Approve or reject an action the running turn is waiting on."""
        approval = self._conversations.approval(approval_id)
        if approval.state != "pending":
            raise AssistantBusyError(f"this action was already {approval.state}")
        waiting = self._approvals.get(approval_id)
        if waiting is None or waiting.done():
            self._conversations.decide(approval_id, "expired")
            raise AssistantBusyError("this request expired when its turn ended; ask again")
        self._conversations.decide(approval_id, "approved" if approve else "rejected", decided_by)
        waiting.set_result(approve)

    def watch(
        self, conversation_id: str, privileges: frozenset[str], kind: str, job_id: str
    ) -> None:
        self._watches[(kind, job_id)] = (conversation_id, privileges)

    def notify(self, kind: str, job_id: str, text: str, ok: bool) -> None:
        """Tell the conversation that started a job how it ended, and wake it if idle."""
        watcher = self._watches.pop((kind, job_id), None)
        if watcher is None:
            return
        conversation_id, privileges = watcher
        with contextlib.suppress(LookupError):
            self._conversations.add_message(
                conversation_id, "event", text, summary={"kind": "notification", "ok": ok}
            )
            self._wake[conversation_id] = privileges
            self._wake_next()

    def _wake_next(self) -> None:
        """Start a turn for a conversation with an unread notification, if free to."""
        if self._active is not None:
            return
        while self._wake:
            conversation_id, privileges = self._wake.popitem()
            try:
                conversation = self._conversations.get(conversation_id)
            except LookupError:
                continue
            # A stopped, paused or failed conversation waits for the operator.
            if conversation.state != "idle" or not self._unread(conversation_id):
                continue
            self._start(conversation_id, privileges)
            return

    def _unread(self, conversation_id: str) -> bool:
        """Whether a notification came after the assistant last replied."""
        for record in reversed(self._conversations.messages(conversation_id)):
            if record.role == "assistant":
                return False
            if is_notification(record):
                return True
        return False

    def _require_free(self, conversation: AssistantConversationRecord) -> None:
        if self._active is not None:
            raise AssistantBusyError(
                "the assistant is already working"
                + (" on this conversation" if self._active == conversation.id else "")
            )

    def _start(self, conversation_id: str, privileges: frozenset[str]) -> None:
        self._active = conversation_id
        self._progress = {"round": 0, "content": "", "reasoning": "", "tool_call": None}
        self._conversations.update(conversation_id, state="running", error=None)
        self._task = asyncio.create_task(self._turn(conversation_id, privileges))

    async def _turn(self, conversation_id: str, privileges: frozenset[str]) -> None:
        state, error = "idle", None
        try:
            state = await self._rounds(conversation_id, privileges)
        except asyncio.CancelledError:
            self._conversations.expire_pending(conversation_id)
            self._conversations.add_message(
                conversation_id, "event", "Stopped.", summary={"kind": "stopped"}
            )
        except OpenRouterError as failure:
            state, error = "failed", str(failure)
            self._conversations.add_message(
                conversation_id, "event", error, summary={"kind": "error"}
            )
        finally:
            with contextlib.suppress(LookupError):
                self._conversations.update(conversation_id, state=state, error=error)
            self._active = None
            self._progress = {}
            self._wake_next()

    async def _rounds(self, conversation_id: str, privileges: frozenset[str]) -> str:
        """Model calls and tool calls until the reply is final; returns the end state."""
        conversation = self._conversations.get(conversation_id)
        setup = self._setup(conversation)
        self._conversations.update(conversation_id, model_id=setup.model_id)
        tools = offered(self._tools, privileges)
        by_name = {tool.name: tool for tool in tools}
        definitions = [tool.definition() for tool in tools]
        context = ToolContext(
            self._services,
            privileges,
            brief=conversation.brief,
            watch=lambda kind, job_id: self.watch(conversation_id, privileges, kind, job_id),
        )
        limit = self._settings().max_rounds_per_turn
        for round_number in range(1, limit + 1):
            conversation = self._conversations.get(conversation_id)
            if conversation.cost_usd >= conversation.spend_limit_usd:
                self._conversations.add_message(
                    conversation_id,
                    "event",
                    f"Paused: this conversation has cost ${conversation.cost_usd:.2f}, which "
                    f"reached its ${conversation.spend_limit_usd:.2f} limit.",
                    summary={"kind": "paused"},
                )
                return "paused"
            self._progress.update(round=round_number, content="", reasoning="", tool_call=None)
            messages: list[dict[str, object]] = [
                {"role": "system", "content": setup.system_prompt},
                *history(self._conversations.messages(conversation_id)),
            ]
            content, reasoning, calls, usage = await self._reply_with_retries(
                setup.model_id, messages, definitions, round_number
            )
            cost = usage.get("cost")
            if isinstance(cost, int | float):
                cost_usd = float(cost)
            else:
                cost_usd = estimated_cost(usage, setup.pricing)
            self._conversations.add_message(
                conversation_id,
                "assistant",
                content,
                reasoning=reasoning,
                tool_calls=assistant_message(content, calls)["tool_calls"] if calls else None,
                usage=dict(usage) or None,
                cost_usd=cost_usd,
            )
            self._conversations.add_usage(
                conversation_id,
                cost_usd=cost_usd,
                prompt_tokens=_count(usage.get("prompt_tokens")),
                completion_tokens=_count(usage.get("completion_tokens")),
            )
            if not calls:
                return "idle"
            for call in calls:
                self._progress.update(tool_call=None, running_tool=call["name"])
                result = await self._call(conversation_id, by_name, context, call)
                self._conversations.add_message(
                    conversation_id,
                    "tool",
                    result.text,
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    summary={"summary": result.summary, "ok": result.ok},
                )
            self._progress["running_tool"] = None
        self._conversations.add_message(
            conversation_id,
            "event",
            f"Stopped after {limit} model calls, the most one message may lead to. Send a "
            "message to carry on.",
            summary={"kind": "round_limit"},
        )
        return "idle"

    async def _call(
        self,
        conversation_id: str,
        tools: Mapping[str, Tool],
        context: ToolContext,
        call: PendingCall,
    ) -> ToolResult:
        """Run one call, first asking the operator if its kind of action needs approval."""
        resolved = resolve_call(tools, context, call["name"], call["arguments"])
        if isinstance(resolved, ToolResult):
            return resolved
        tool, arguments = resolved
        if self._settings().needs_approval(tool.approval):
            try:
                title, details = await tool.approval_request(context, arguments)
            except ToolError as error:
                return error_result(str(error))
            if not await self._ask(conversation_id, tool, call, title, details):
                return ToolResult(REJECTED, "Rejected", ok=False)
        return await run_call(tool, context, arguments)

    async def _ask(
        self,
        conversation_id: str,
        tool: Tool,
        call: PendingCall,
        title: str,
        details: dict[str, object],
    ) -> bool:
        """Wait for the operator to approve (True) or reject (False) an action."""
        approval = self._conversations.create_approval(
            conversation_id,
            tool_call_id=call["id"],
            tool_name=tool.name,
            category=str(tool.approval),
            title=title,
            details=details,
        )
        waiting: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._approvals[approval.id] = waiting
        self._conversations.update(conversation_id, state="awaiting_approval")
        self._progress["approval_id"] = approval.id
        try:
            return await waiting
        finally:
            self._approvals.pop(approval.id, None)
            self._progress["approval_id"] = None
            self._conversations.update(conversation_id, state="running")

    async def _reply_with_retries(
        self,
        model_id: str,
        messages: list[dict[str, object]],
        definitions: list[dict[str, object]],
        round_number: int,
    ) -> tuple[str, str, list[PendingCall], dict[str, object]]:
        """A reply, retried when the provider failed before streaming anything.

        Once part of a reply has arrived it has been paid for, so a failure then is final.
        """
        for attempt, delay in enumerate((*self._retry_delays, None), start=1):
            try:
                return await self._reply(model_id, messages, definitions, round_number)
            except OpenRouterError as error:
                streamed = any(
                    self._progress.get(key) for key in ("content", "reasoning", "tool_call")
                )
                if not error.transient or delay is None or streamed:
                    raise
                self._progress["retrying"] = {"attempt": attempt, "error": str(error)}
                await asyncio.sleep(delay)
                self._progress.update(content="", reasoning="", tool_call=None, retrying=None)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _reply(
        self,
        model_id: str,
        messages: list[dict[str, object]],
        definitions: list[dict[str, object]],
        round_number: int,
    ) -> tuple[str, str, list[PendingCall], dict[str, object]]:
        """Stream one reply: its text, reasoning, tool calls and usage."""
        pending: dict[int, PendingCall] = {}
        sizes: dict[int, int] = {}
        usage: dict[str, object] = {}
        async for chunk in self._client.chat_stream(model_id, messages, tools=definitions):
            if isinstance(chunk.get("usage"), dict):
                usage = dict(cast(dict[str, object], chunk["usage"]))
            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else None
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if not isinstance(delta, dict):
                continue
            for key, field in (
                ("content", "content"),
                ("reasoning", "reasoning"),
                ("reasoning", "reasoning_content"),
            ):
                value = delta.get(field)
                if isinstance(value, str) and value:
                    self._progress[key] = str(self._progress.get(key, "")) + value
            latest = merge_tool_calls(delta, pending, sizes)
            if latest is not None:
                self._progress["tool_call"] = latest
        calls = ordered_calls(pending, round_number) if pending else []
        return (
            str(self._progress.get("content", "")),
            str(self._progress.get("reasoning", "")),
            calls,
            usage,
        )


def _count(value: object) -> int:
    return value if isinstance(value, int) else 0
