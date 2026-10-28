"""HTTP routes for assistant conversations and the assistant's settings.

The app's auth middleware has already checked the session and privilege by the time a
handler runs (``/api/assistant`` maps to the assistant privileges, ``/api/settings`` to
the settings ones) and left the signed-in user on ``request.state.user``.
"""

from __future__ import annotations

import re
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from llamawebui.assistant.brief import Brief
from llamawebui.assistant.conversations import (
    ConversationNotFoundError,
    ConversationRegistry,
    approval_payload,
    conversation_payload,
    message_payload,
)
from llamawebui.assistant.engine import AssistantBusyError, AssistantEngine
from llamawebui.assistant.library import (
    SLUG_PATTERN,
    DocumentLibrary,
    DocumentNotFoundError,
    LibraryError,
    document_payload,
    document_summary,
)
from llamawebui.assistant.settings import AssistantSettings, load_settings, save_settings
from llamawebui.domain.benchmark import DEFAULT_BENCHMARK_ID, Benchmark
from llamawebui.local_requests import is_local_request
from llamawebui.services.auth_service import AuthenticatedUser
from llamawebui.services.benchmark_runner import REASONING_LEVELS, BenchmarkRunRegistry
from llamawebui.services.profile_registry import ProfileNotFoundError, ProfileRegistry
from llamawebui.services.settings_store import SettingsStore

router = APIRouter(prefix="/api")


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    brief: Brief


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class ApprovalDecisionRequest(BaseModel):
    approve: bool


class PublishRequest(BaseModel):
    slugs: list[str] = Field(min_length=1, max_length=200)


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


def _benchmark_runs(request: Request) -> BenchmarkRunRegistry:
    return cast(BenchmarkRunRegistry, request.app.state.benchmark_runs)


def _detail(request: Request, conversation_id: str) -> dict[str, object]:
    engine = _engine(request)
    record = _conversations(request).get(conversation_id)
    runs = _benchmark_runs(request).list(conversation_id=conversation_id)
    return {
        **conversation_payload(record, running=engine.active_conversation_id == conversation_id),
        # Runs the assistant started here that still exist, for cleaning up afterwards.
        "benchmark_runs": len(runs),
        "messages": [
            message_payload(message)
            for message in _conversations(request).messages(conversation_id)
        ],
        "progress": engine.progress(conversation_id),
        "approvals": [
            approval_payload(approval)
            for approval in _conversations(request).approvals(conversation_id)
        ],
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


@router.post("/assistant/conversations/{conversation_id}/benchmark-runs/delete")
async def delete_conversation_runs(conversation_id: str, request: Request) -> dict[str, object]:
    """Delete the finished benchmark runs this conversation started."""
    _conversation_or_404(request, conversation_id)
    # The same privilege the benchmark API asks for to delete a run.
    if "server.lifecycle.write" not in _user(request).privileges:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="deleting benchmark runs needs the server lifecycle write privilege",
        )
    runs = _benchmark_runs(request)
    deleted, skipped = runs.delete_many(
        [record.id for record in runs.list(conversation_id=conversation_id)]
    )
    return {"deleted": deleted, "skipped": skipped}


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


@router.post("/assistant/conversations/{conversation_id}/retry", status_code=202)
async def retry_turn(conversation_id: str, request: Request) -> dict[str, object]:
    _conversation_or_404(request, conversation_id)
    try:
        _engine(request).retry(conversation_id, _user(request).privileges)
    except AssistantBusyError as error:
        raise _busy(error) from error
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


@router.post("/assistant/approvals/{approval_id}")
async def decide_approval(
    approval_id: str, decision: ApprovalDecisionRequest, request: Request
) -> dict[str, object]:
    engine = _engine(request)
    try:
        approval = _conversations(request).approval(approval_id)
    except ConversationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    tool = engine.tool(approval.tool_name)
    user = _user(request)
    # Approving an action takes the privilege the action itself needs.
    if tool is None or tool.privilege not in user.privileges:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"deciding this needs the {tool.privilege if tool else 'matching'} privilege",
        )
    try:
        engine.decide(approval_id, decision.approve, user.username)
    except AssistantBusyError as error:
        raise _busy(error) from error
    return _detail(request, approval.conversation_id)


# Research and reports: the same routes for each library ---------------------------------

LIBRARIES = {"research": "research_library", "reports": "report_library"}


def _library(request: Request, kind: str) -> DocumentLibrary[Any]:
    return cast(DocumentLibrary[Any], getattr(request.app.state, LIBRARIES[kind]))


def _publish_problem(request: Request, kind: str) -> str | None:
    """Why this request cannot publish documents, or None if it can."""
    if "settings.write" not in _user(request).privileges:
        return "publishing needs the settings.write privilege"
    if not is_local_request(request):
        return "publishing only works from the machine LlamaWebUI runs on"
    return _library(request, kind).publish_problem()


def _library_routes(kind: str) -> None:
    async def list_documents(request: Request, q: str = "") -> dict[str, object]:
        matches = _library(request, kind).search(q, limit=500)
        return {
            "documents": [document_summary(document) for document, _ in matches],
            "publish_problem": _publish_problem(request, kind),
        }

    async def read_document(slug: str, request: Request) -> dict[str, object]:
        try:
            return document_payload(_library(request, kind).get(slug))
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    async def delete_document(slug: str, request: Request) -> None:
        try:
            _library(request, kind).delete(slug)
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except LibraryError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    async def publish_documents(body: PublishRequest, request: Request) -> dict[str, object]:
        problem = _publish_problem(request, kind)
        if problem:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
        if any(re.fullmatch(SLUG_PATTERN, slug) is None for slug in body.slugs):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="bad slug"
            )
        library = _library(request, kind)
        try:
            published = library.publish(body.slugs)
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except LibraryError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return {"published": published, "folder": str(library.shipped_directory)}

    base = f"/assistant/{kind}"
    router.add_api_route(base, list_documents, methods=["GET"])
    # Registered before /{slug} so "publish" is not taken for a slug.
    router.add_api_route(f"{base}/publish", publish_documents, methods=["POST"])
    router.add_api_route(f"{base}/{{slug}}", read_document, methods=["GET"])
    router.add_api_route(f"{base}/{{slug}}", delete_document, methods=["DELETE"], status_code=204)


for _kind in LIBRARIES:
    _library_routes(_kind)


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
    store = _settings_store(request)
    # Like publishing by hand, switching auto-publish on writes into the source tree, so
    # only the machine itself may do it.
    if (
        validated.auto_publish
        and not load_settings(store).auto_publish
        and not is_local_request(request)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="auto-publish can only be switched on from the machine LlamaWebUI runs on",
        )
    save_settings(store, validated)
    return validated.model_dump()
