"""HTTP routes for assistant conversations and the assistant's settings.

The app's auth middleware has already checked the session and privilege by the time a
handler runs (``/api/assistant`` maps to the assistant privileges, ``/api/settings`` to
the settings ones) and left the signed-in user on ``request.state.user``.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from llamawebui.assistant.brief import Brief
from llamawebui.assistant.conversations import (
    ConversationNotFoundError,
    ConversationRegistry,
    conversation_payload,
    message_payload,
)
from llamawebui.assistant.engine import AssistantBusyError, AssistantEngine
from llamawebui.assistant.settings import AssistantSettings, load_settings, save_settings
from llamawebui.domain.benchmark import DEFAULT_BENCHMARK_ID, Benchmark
from llamawebui.services.auth_service import AuthenticatedUser
from llamawebui.services.benchmark_runner import REASONING_LEVELS
from llamawebui.services.profile_registry import ProfileNotFoundError, ProfileRegistry
from llamawebui.services.settings_store import SettingsStore

router = APIRouter(prefix="/api")


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    brief: Brief


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


def _engine(request: Request) -> AssistantEngine:
    return cast(AssistantEngine, request.app.state.assistant_engine)


def _conversations(request: Request) -> ConversationRegistry:
    return cast(ConversationRegistry, request.app.state.assistant_conversations)


def _settings_store(request: Request) -> SettingsStore:
    return cast(SettingsStore, request.app.state.settings_store)


def _user(request: Request) -> AuthenticatedUser:
    return cast(AuthenticatedUser, request.state.user)


def _conversation_or_404(request: Request, conversation_id: str) -> None:
    try:
        _conversations(request).get(conversation_id)
    except ConversationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


def _busy(error: AssistantBusyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _checked_brief(request: Request, brief: Brief) -> tuple[Brief, str]:
    """The brief with its references checked and the benchmark filled in, and a title."""
    title = ""
    if brief.profile_id is not None:
        try:
            profile = cast(ProfileRegistry, request.app.state.profile_registry).get(
                brief.profile_id
            )
        except ProfileNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        title = f"Optimise {profile.alias}"
    elif brief.model is not None:
        title = f"Get {brief.model.repo_id.rsplit('/', 1)[-1]} working"
    benchmarks = cast(tuple[Benchmark, ...], request.app.state.benchmarks)
    benchmark_id = brief.benchmark_id or next(
        (item.id for item in benchmarks if item.id == DEFAULT_BENCHMARK_ID),
        benchmarks[0].id if benchmarks else None,
    )
    if benchmark_id not in {item.id for item in benchmarks}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown benchmark: {benchmark_id}",
        )
    if brief.reasoning_level is not None and brief.reasoning_level not in REASONING_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown reasoning level: {brief.reasoning_level}",
        )
    return brief.model_copy(update={"benchmark_id": benchmark_id}), title


def _detail(request: Request, conversation_id: str) -> dict[str, object]:
    engine = _engine(request)
    record = _conversations(request).get(conversation_id)
    return {
        **conversation_payload(
            record, running=engine.active_conversation_id == conversation_id
        ),
        "messages": [
            message_payload(message)
            for message in _conversations(request).messages(conversation_id)
        ],
        "progress": engine.progress(conversation_id),
    }


@router.get("/assistant/conversations")
async def list_conversations(request: Request) -> list[dict[str, object]]:
    active = _engine(request).active_conversation_id
    return [
        conversation_payload(record, running=record.id == active)
        for record in _conversations(request).list()
    ]


@router.post("/assistant/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    conversation_request: ConversationCreateRequest, request: Request
) -> dict[str, object]:
    brief, title = _checked_brief(request, conversation_request.brief)
    record = _conversations(request).create(
        title=(conversation_request.title or "").strip() or title,
        created_by=_user(request).username,
        brief=brief.model_dump(mode="json"),
        spend_limit_usd=load_settings(_settings_store(request)).spend_limit_usd,
    )
    return _detail(request, record.id)


@router.get("/assistant/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> dict[str, object]:
    _conversation_or_404(request, conversation_id)
    return _detail(request, conversation_id)


@router.put("/assistant/conversations/{conversation_id}/brief")
async def update_brief(conversation_id: str, brief: Brief, request: Request) -> dict[str, object]:
    _conversation_or_404(request, conversation_id)
    if _engine(request).active_conversation_id == conversation_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stop the assistant before changing the brief",
        )
    checked, _ = _checked_brief(request, brief)
    conversations = _conversations(request)
    conversations.update(conversation_id, brief=checked.model_dump(mode="json"))
    conversations.add_message(
        conversation_id,
        "event",
        f"{_user(request).username} updated the brief.",
        summary={"kind": "brief"},
    )
    return _detail(request, conversation_id)


@router.delete("/assistant/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> None:
    _conversation_or_404(request, conversation_id)
    if _engine(request).active_conversation_id == conversation_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stop the assistant before deleting the conversation",
        )
    _conversations(request).delete(conversation_id)


@router.post("/assistant/conversations/{conversation_id}/messages", status_code=202)
async def send_message(
    conversation_id: str, message: MessageCreateRequest, request: Request
) -> dict[str, object]:
    _conversation_or_404(request, conversation_id)
    try:
        _engine(request).send(conversation_id, message.content, _user(request).privileges)
    except AssistantBusyError as error:
        raise _busy(error) from error
    return _detail(request, conversation_id)


@router.post("/assistant/conversations/{conversation_id}/stop")
async def stop_turn(conversation_id: str, request: Request) -> dict[str, object]:
    _conversation_or_404(request, conversation_id)
    await _engine(request).stop(conversation_id)
    return _detail(request, conversation_id)


@router.post("/assistant/conversations/{conversation_id}/continue", status_code=202)
async def continue_turn(conversation_id: str, request: Request) -> dict[str, object]:
    _conversation_or_404(request, conversation_id)
    raise_by = load_settings(_settings_store(request)).spend_limit_usd
    try:
        _engine(request).resume(conversation_id, _user(request).privileges, raise_by)
    except AssistantBusyError as error:
        raise _busy(error) from error
    return _detail(request, conversation_id)


@router.get("/settings/assistant")
async def assistant_settings(request: Request) -> dict[str, object]:
    return load_settings(_settings_store(request)).model_dump()


@router.put("/settings/assistant")
async def update_assistant_settings(
    settings: dict[str, object], request: Request
) -> dict[str, object]:
    try:
        validated = AssistantSettings.model_validate(settings)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="; ".join(str(issue["msg"]) for issue in error.errors()),
        ) from error
    save_settings(_settings_store(request), validated)
    return validated.model_dump()
