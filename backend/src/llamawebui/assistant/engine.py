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
from llamawebui.assistant.tools import Tool, ToolContext, ToolServices, call_tool, offered
from llamawebui.models import AssistantConversationRecord, AssistantMessageRecord
from llamawebui.services.chat_stream import (
    PendingCall,
    assistant_message,
    merge_tool_calls,
    ordered_calls,
)

NOT_RUN = "Not run: the turn ended before this call ran."


class AssistantBusyError(RuntimeError):
    """Another turn is running, or this conversation is waiting to be continued."""


@dataclass(frozen=True, slots=True)
class TurnSetup:
    """What a turn needs that changes between turns: the model and the system prompt."""

    model_id: str
    # USD per token, for estimating a reply's cost when OpenRouter does not report it.
    pricing: Mapping[str, str]
    system_prompt: str


def history(records: Sequence[AssistantMessageRecord]) -> list[dict[str, object]]:
    """The stored conversation as chat messages for the model.

    Events are for the reader only. A tool call that never got a result (the turn was
    stopped or the app restarted) is answered as not run, so the history stays valid.
    """
    messages: list[dict[str, object]] = []
    answered = {record.tool_call_id for record in records if record.role == "tool"}
    for record in records:
        if record.role == "user":
            messages.append({"role": "user", "content": record.content})
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
    ) -> None:
        self._conversations = conversations
        self._client = client
        self._services = services
        self._tools = tuple(tools)
        self._settings = settings
        self._setup = setup
        self._task: asyncio.Task[None] | None = None
        self._active: str | None = None
        self._progress: dict[str, object] = {}

    @property
    def active_conversation_id(self) -> str | None:
        return self._active

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

    async def stop(self, conversation_id: str) -> bool:
        task = self._task
        if self._active != conversation_id or task is None:
            return False
        task.cancel()
        await asyncio.wait({task})
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.wait({self._task})

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

    async def _rounds(self, conversation_id: str, privileges: frozenset[str]) -> str:
        """Model calls and tool calls until the reply is final; returns the end state."""
        conversation = self._conversations.get(conversation_id)
        setup = self._setup(conversation)
        self._conversations.update(conversation_id, model_id=setup.model_id)
        tools = offered(self._tools, privileges)
        by_name = {tool.name: tool for tool in tools}
        definitions = [tool.definition() for tool in tools]
        context = ToolContext(self._services, privileges)
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
            content, reasoning, calls, usage = await self._reply(
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
                result = await call_tool(by_name, context, call["name"], call["arguments"])
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
