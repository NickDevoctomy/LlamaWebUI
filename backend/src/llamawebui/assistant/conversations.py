"""Store assistant conversations and their messages."""

from __future__ import annotations

import builtins
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import sessionmaker

from llamawebui.models import AssistantConversationRecord, AssistantMessageRecord


class ConversationNotFoundError(LookupError):
    pass


class ConversationRegistry:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(
        self, *, title: str, created_by: str, brief: dict[str, object], spend_limit_usd: float
    ) -> AssistantConversationRecord:
        record = AssistantConversationRecord(
            id=str(uuid4()),
            title=title,
            created_by=created_by,
            brief=brief,
            state="idle",
            cost_usd=0.0,
            spend_limit_usd=spend_limit_usd,
            prompt_tokens=0,
            completion_tokens=0,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
        return record

    def list(self) -> list[AssistantConversationRecord]:
        with self._sessions() as session:
            statement = select(AssistantConversationRecord).order_by(
                AssistantConversationRecord.updated_at.desc(),
                AssistantConversationRecord.created_at.desc(),
            )
            return list(session.scalars(statement))

    def get(self, conversation_id: str) -> AssistantConversationRecord:
        with self._sessions() as session:
            record = session.get(AssistantConversationRecord, conversation_id)
        if record is None:
            raise ConversationNotFoundError(f"conversation not found: {conversation_id}")
        return record

    def update(self, conversation_id: str, **fields: Any) -> AssistantConversationRecord:
        with self._sessions() as session:
            record = session.get(AssistantConversationRecord, conversation_id)
            if record is None:
                raise ConversationNotFoundError(f"conversation not found: {conversation_id}")
            for key, value in fields.items():
                setattr(record, key, value)
            session.commit()
            return record

    def add_usage(
        self, conversation_id: str, *, cost_usd: float, prompt_tokens: int, completion_tokens: int
    ) -> None:
        with self._sessions() as session:
            session.execute(
                update(AssistantConversationRecord)
                .where(AssistantConversationRecord.id == conversation_id)
                .values(
                    cost_usd=AssistantConversationRecord.cost_usd + cost_usd,
                    prompt_tokens=AssistantConversationRecord.prompt_tokens + prompt_tokens,
                    completion_tokens=AssistantConversationRecord.completion_tokens
                    + completion_tokens,
                )
            )
            session.commit()

    def delete(self, conversation_id: str) -> None:
        with self._sessions() as session:
            record = session.get(AssistantConversationRecord, conversation_id)
            if record is None:
                raise ConversationNotFoundError(f"conversation not found: {conversation_id}")
            session.delete(record)
            session.commit()

    def messages(self, conversation_id: str) -> builtins.list[AssistantMessageRecord]:
        with self._sessions() as session:
            statement = (
                select(AssistantMessageRecord)
                .where(AssistantMessageRecord.conversation_id == conversation_id)
                .order_by(AssistantMessageRecord.seq)
            )
            return list(session.scalars(statement))

    def add_message(
        self, conversation_id: str, role: str, content: str = "", **fields: Any
    ) -> AssistantMessageRecord:
        with self._sessions() as session:
            last = session.scalar(
                select(func.max(AssistantMessageRecord.seq)).where(
                    AssistantMessageRecord.conversation_id == conversation_id
                )
            )
            record = AssistantMessageRecord(
                id=str(uuid4()),
                conversation_id=conversation_id,
                seq=(last or 0) + 1,
                role=role,
                content=content,
                reasoning=fields.pop("reasoning", ""),
                **fields,
            )
            session.add(record)
            # Touch the conversation so the most recently active one lists first.
            session.execute(
                update(AssistantConversationRecord)
                .where(AssistantConversationRecord.id == conversation_id)
                .values(updated_at=func.now())
            )
            session.commit()
            return record

    def interrupt_running(self) -> int:
        """Mark turns left running by a stopped app as interrupted; returns how many."""
        with self._sessions() as session:
            result = session.execute(
                update(AssistantConversationRecord)
                .where(AssistantConversationRecord.state == "running")
                .values(state="interrupted")
            )
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)


def conversation_payload(
    record: AssistantConversationRecord, *, running: bool = False
) -> dict[str, object]:
    return {
        "id": record.id,
        "title": record.title,
        "created_by": record.created_by,
        "model_id": record.model_id,
        "brief": record.brief,
        # A turn left "running" by a stopped app is reported as it really is.
        "state": record.state if running or record.state != "running" else "interrupted",
        "error": record.error,
        "cost_usd": record.cost_usd,
        "spend_limit_usd": record.spend_limit_usd,
        "prompt_tokens": record.prompt_tokens,
        "completion_tokens": record.completion_tokens,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def message_payload(record: AssistantMessageRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "seq": record.seq,
        "role": record.role,
        "content": record.content,
        "reasoning": record.reasoning,
        "tool_calls": record.tool_calls,
        "tool_call_id": record.tool_call_id,
        "tool_name": record.tool_name,
        "summary": record.summary,
        "cost_usd": record.cost_usd,
        "created_at": record.created_at,
    }
