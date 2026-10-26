from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Login
from fastapi.testclient import TestClient
from pydantic import ValidationError

from llamawebui.app import create_app
from llamawebui.assistant.brief import Brief, brief_text
from llamawebui.assistant.engine import NOT_RUN, estimated_cost, history
from llamawebui.assistant.openrouter_client import OpenRouterClient, OpenRouterError
from llamawebui.assistant.prompt import (
    TOKENS,
    PromptTemplateError,
    render_prompt,
    system_template,
    template_tokens,
)
from llamawebui.assistant.settings import AssistantSettings
from llamawebui.assistant.setup import hardware_text
from llamawebui.config import Settings
from llamawebui.models import AssistantMessageRecord
from llamawebui.services.authorization import privilege_for_request
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.secret_store import SecretName, SecretStore
from llamawebui.services.settings_store import OPENROUTER_MODEL

MODEL = {
    "id": "test/cheap-model",
    "name": "Cheap",
    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    "selected_at": "2026-10-26T00:00:00+00:00",
}
HARDWARE = HardwareProfile("Test CPU", 64 * 1024**3, ("Test GPU",), 0)


def text_reply(text: str, cost: float | None = 0.01) -> list[dict[str, object]]:
    usage: dict[str, object] = {"prompt_tokens": 100, "completion_tokens": 20}
    if cost is not None:
        usage["cost"] = cost
    return [
        {"choices": [{"delta": {"reasoning": "thinking"}}]},
        {"choices": [{"delta": {"content": text}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage},
    ]


def tool_reply(
    name: str, arguments: Mapping[str, object], call_id: str = "call-1"
) -> list[dict[str, object]]:
    encoded = json.dumps(arguments)
    return [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": call_id, "function": {"name": name, "arguments": ""}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": encoded}}]}}
            ]
        },
        {
            "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "cost": 0.005},
        },
    ]


class ScriptedClient:
    """Replies with each scripted reply in turn; records every request."""

    def __init__(self, *replies: list[dict[str, object]] | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        self.requests.append(
            {"model": model, "messages": [dict(item) for item in messages], "tools": list(tools)}
        )
        reply = self.replies.pop(0) if self.replies else text_reply("done")
        if isinstance(reply, Exception):
            raise reply
        for chunk in reply:
            if self.gate is not None:
                await self.gate.wait()
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


LOCAL = ("127.0.0.1", 50000)


def make_app(tmp_path: Path, client: ScriptedClient) -> Any:
    return create_app(
        Settings(data_dir=tmp_path / "data"),
        assistant_client=client,
        hardware_detector=lambda: HARDWARE,
    )


def started(client: TestClient, login: Login) -> TestClient:
    login(client)
    client.app.state.settings_store.set_json(OPENROUTER_MODEL, MODEL)  # type: ignore[attr-defined]
    return client


def new_conversation(client: TestClient, **brief: object) -> dict[str, Any]:
    response = client.post(
        "/api/assistant/conversations",
        json={"brief": {"subject": "model", "model": {"repo_id": "owner/Model-GGUF"}, **brief}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait(client: TestClient) -> None:
    client.portal.call(client.app.state.assistant_engine.wait)  # type: ignore[union-attr,attr-defined]


# Brief, prompt and settings ---------------------------------------------------------------


def test_brief_needs_exactly_one_subject_and_a_full_priority() -> None:
    Brief.model_validate({"subject": "profile", "profile_id": "p"})
    for invalid in (
        {"subject": "model"},
        {"subject": "model", "model": {"repo_id": "owner/model"}, "profile_id": "p"},
        {"subject": "profile"},
        {"subject": "profile", "profile_id": "p", "model": {"repo_id": "owner/model"}},
        {"subject": "profile", "profile_id": "p", "priority": ["decode", "decode"]},
        {"subject": "profile", "profile_id": "p", "min_decode_tps": -1},
        {"subject": "profile", "profile_id": "p", "surprise": 1},
    ):
        with pytest.raises(ValidationError):
            Brief.model_validate(invalid)


def test_brief_text_lists_the_targets_set() -> None:
    brief = Brief.model_validate(
        {
            "subject": "model",
            "model": {"repo_id": "owner/Model-GGUF", "quantization": "Q4_K_M"},
            "max_load_seconds": 30,
            "min_prefill_tps": 500,
            "min_decode_tps": 25.5,
            "max_ram_gb": 48,
            "max_vram_gb": 16,
            "min_context_tokens": 65536,
            "parallel_slots": 2,
            "reasoning_level": "low",
            "priority": ["load", "decode", "prefill", "memory"],
        }
    )
    text = brief_text(brief, profile_alias=None, benchmark_title="Tetris")
    assert "`owner/Model-GGUF`, quantization Q4_K_M" in text
    for expected in (
        "at most 30 s",
        "at least 500 tokens/s",
        "at least 25.5 tokens/s",
        "RAM use at most 48 GB",
        "VRAM use at most 16 GB",
        "at least 65536 tokens",
        "2 requests at once",
        '"Tetris", reasoning level low',
        "load, decode, prefill, memory",
    ):
        assert expected in text
    plain = brief_text(
        Brief.model_validate({"subject": "profile", "profile_id": "p1"}),
        profile_alias="qwen",
        benchmark_title="Tetris",
    )
    assert "existing profile `qwen`" in plain
    assert "None set" in plain


def test_system_template_uses_only_known_tokens_and_renders_completely() -> None:
    template = system_template()
    assert template_tokens(template) == set(TOKENS)
    values = {token: f"<{token}>" for token in TOKENS} | {"assistant_name": "Lama {{date}}"}
    rendered = render_prompt(template, values)
    assert "You are Lama {{date}}" in rendered
    assert "{{" not in rendered.replace("Lama {{date}}", "")
    with pytest.raises(PromptTemplateError, match="unknown"):
        render_prompt("{{nope}}", values)
    with pytest.raises(PromptTemplateError, match="no value"):
        render_prompt("{{brief}}", {})


def test_assistant_settings_validate_the_name() -> None:
    assert AssistantSettings().name == "Lama"
    assert AssistantSettings(name="  Max  ").name == "Max"
    for name in ("", "   ", "two\nlines", "x" * 41):
        with pytest.raises(ValidationError):
            AssistantSettings(name=name)


def test_hardware_text_explains_missing_vram() -> None:
    assert "not reported" in hardware_text(HARDWARE)
    assert "Discrete VRAM (NVIDIA): 8.0 GiB" in hardware_text(
        HardwareProfile(None, 1024**3, (), 8 * 1024**3)
    )


# Engine helpers ---------------------------------------------------------------------------


def _message(role: str, **fields: Any) -> AssistantMessageRecord:
    return AssistantMessageRecord(
        id="m", conversation_id="c", seq=1, role=role, content=fields.pop("content", ""), **fields
    )


def test_history_answers_unanswered_calls_and_drops_events() -> None:
    records = [
        _message("user", content="hi"),
        _message("event", content="note"),
        _message(
            "assistant",
            content="",
            tool_calls=[
                {"id": "a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "y", "arguments": "{}"}},
            ],
        ),
        _message("tool", content="result", tool_call_id="a"),
        _message("assistant", content="final"),
    ]
    messages = history(records)
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert messages[2] == {"role": "tool", "tool_call_id": "b", "content": NOT_RUN}
    assert messages[3]["content"] == "result"


def test_estimated_cost_uses_saved_pricing() -> None:
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    assert estimated_cost(usage, {"prompt": "0.000001", "completion": "0.000002"}) == 0.002
    assert estimated_cost(usage, {"prompt": "-1", "completion": "bad"}) == 0.0
    assert estimated_cost({}, {}) == 0.0


# OpenRouter client ------------------------------------------------------------------------


def _sse(*chunks: object) -> bytes:
    lines = [": OPENROUTER PROCESSING"]
    lines += [f"data: {json.dumps(chunk)}" for chunk in chunks]
    return ("\n".join([*lines, "data: [DONE]"]) + "\n").encode()


async def _collect(client: OpenRouterClient) -> list[dict[str, object]]:
    return [
        chunk
        async for chunk in client.chat_stream(
            "m", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}]
        )
    ]


@pytest.mark.asyncio
async def test_openrouter_client_streams_with_usage_and_the_saved_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse({"choices": []}, {"usage": {"cost": 0.1}}))

    client = OpenRouterClient(
        lambda: "sk-or-test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    chunks = await _collect(client)

    assert chunks == [{"choices": []}, {"usage": {"cost": 0.1}}]
    body = json.loads(requests[0].content)
    assert body["usage"] == {"include": True}
    assert body["stream"] is True
    assert body["tools"] == [{"type": "function"}]
    assert requests[0].headers["authorization"] == "Bearer sk-or-test"


@pytest.mark.asyncio
async def test_openrouter_client_reports_errors_readably() -> None:
    def respond(response: httpx.Response) -> OpenRouterClient:
        return OpenRouterClient(
            lambda: "k",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)),
        )

    with pytest.raises(OpenRouterError, match="no OpenRouter API key"):
        await _collect(OpenRouterClient(lambda: None))
    with pytest.raises(OpenRouterError, match="OpenRouter: Insufficient credits \\(402\\)"):
        await _collect(
            respond(httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
        )
    with pytest.raises(OpenRouterError, match="OpenRouter returned 500"):
        await _collect(respond(httpx.Response(500, text="boom")))
    with pytest.raises(OpenRouterError, match="OpenRouter: overloaded"):
        await _collect(
            respond(httpx.Response(200, content=_sse({"error": {"message": "overloaded"}})))
        )
    with pytest.raises(OpenRouterError, match="invalid completion chunk"):
        await _collect(respond(httpx.Response(200, content=b"data: {bad\n")))

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with pytest.raises(OpenRouterError, match="could not reach OpenRouter"):
        await _collect(
            OpenRouterClient(
                lambda: "k", client=httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
            )
        )


# Conversations through the API ------------------------------------------------------------


def test_a_turn_calls_tools_then_answers_and_records_cost(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(
        tool_reply("get_hardware", {}),
        tool_reply("search_arguments", {"query": "gpu layers"}, "call-2"),
        text_reply("You have a Test CPU."),
    )
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        conversation = new_conversation(client, min_decode_tps=30)
        sent = client.post(
            f"/api/assistant/conversations/{conversation['id']}/messages",
            json={"content": "What hardware do I have?"},
        )
        wait(client)
        detail = client.get(f"/api/assistant/conversations/{conversation['id']}").json()
        listed = client.get("/api/assistant/conversations").json()

    assert conversation["title"] == "Get Model-GGUF working"
    assert conversation["brief"]["benchmark_id"] == "tetris-no-details"
    assert sent.status_code == 202
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    hardware = json.loads(detail["messages"][2]["content"])
    assert hardware["cpu"] == "Test CPU"
    assert detail["messages"][2]["summary"] == {"summary": "Test CPU", "ok": True}
    assert detail["messages"][4]["summary"]["ok"] is True
    final = detail["messages"][5]
    assert final["content"] == "You have a Test CPU."
    assert final["reasoning"] == "thinking"
    assert detail["state"] == "idle"
    assert detail["model_id"] == "test/cheap-model"
    assert detail["cost_usd"] == pytest.approx(0.02)
    assert detail["prompt_tokens"] == 200
    assert detail["progress"] is None
    assert listed[0]["id"] == conversation["id"]

    request = scripted.requests[0]
    assert request["model"] == "test/cheap-model"
    system = request["messages"][0]
    assert system["role"] == "system"
    assert "You are Lama" in system["content"]
    assert "Test CPU" in system["content"]
    assert "Average decode at least 30 tokens/s" in system["content"]
    names = {tool["function"]["name"] for tool in request["tools"]}
    assert {"get_hardware", "get_profile", "search_arguments", "get_argument"} <= names
    # The second request carries the first call and its result back.
    assert scripted.requests[1]["messages"][-1]["role"] == "tool"


def test_the_name_setting_reaches_the_prompt_and_status(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(text_reply("hi"))
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        defaults = client.get("/api/settings/assistant").json()
        saved = client.put(
            "/api/settings/assistant",
            json={"name": "Max", "spend_limit_usd": 1.5, "max_rounds_per_turn": 10},
        )
        rejected = client.put("/api/settings/assistant", json={"name": "a\nb"})
        status_payload = client.get("/api/assistant/status").json()
        conversation = new_conversation(client)
        client.post(
            f"/api/assistant/conversations/{conversation['id']}/messages", json={"content": "hi"}
        )
        wait(client)

    assert defaults == {
        "name": "Lama",
        "spend_limit_usd": 2.0,
        "max_rounds_per_turn": 40,
        "approvals": {"downloads": True, "runtimes": False, "profiles": True, "benchmarks": True},
    }
    assert saved.json()["name"] == "Max"
    assert rejected.status_code == 422
    assert status_payload["name"] == "Max"
    assert conversation["spend_limit_usd"] == 1.5
    assert "You are Max" in scripted.requests[0]["messages"][0]["content"]


def test_spending_limit_pauses_and_continue_raises_it(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(
        text_reply("first", cost=0.6), text_reply("second", cost=0.6), text_reply("third")
    )
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        client.put(
            "/api/settings/assistant",
            json={"name": "Lama", "spend_limit_usd": 0.5, "max_rounds_per_turn": 5},
        )
        conversation_id = new_conversation(client)["id"]
        base = f"/api/assistant/conversations/{conversation_id}"
        client.post(f"{base}/messages", json={"content": "one"})
        wait(client)
        client.post(f"{base}/messages", json={"content": "two"})
        wait(client)
        paused = client.get(base).json()
        blocked = client.post(f"{base}/messages", json={"content": "three"})
        early = client.post(
            f"/api/assistant/conversations/{new_conversation(client)['id']}/continue"
        )
        resumed = client.post(f"{base}/continue")
        wait(client)
        after = client.get(base).json()

    assert paused["state"] == "paused"
    assert paused["messages"][-1]["summary"] == {"kind": "paused"}
    assert "reached its $0.50 limit" in paused["messages"][-1]["content"]
    assert blocked.status_code == 409
    assert early.status_code == 409
    assert resumed.status_code == 202
    assert after["spend_limit_usd"] == 1.0
    assert after["state"] == "idle"
    assert after["messages"][-1]["content"] == "second"


def test_round_limit_stops_a_turn(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(*(tool_reply("list_profiles", {}, f"c{n}") for n in range(3)))
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        client.put(
            "/api/settings/assistant",
            json={"name": "Lama", "spend_limit_usd": 2, "max_rounds_per_turn": 2},
        )
        conversation_id = new_conversation(client)["id"]
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "go"}
        )
        wait(client)
        detail = client.get(f"/api/assistant/conversations/{conversation_id}").json()

    assert len(scripted.requests) == 2
    assert detail["messages"][-1]["summary"] == {"kind": "round_limit"}
    assert detail["state"] == "idle"


def test_openrouter_failure_and_missing_model_fail_the_turn(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(OpenRouterError("OpenRouter: Insufficient credits (402)"))
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        conversation_id = new_conversation(client)["id"]
        base = f"/api/assistant/conversations/{conversation_id}"
        client.post(f"{base}/messages", json={"content": "go"})
        wait(client)
        failed = client.get(base).json()
        client.app.state.settings_store.delete(OPENROUTER_MODEL)  # type: ignore[attr-defined]
        client.post(f"{base}/messages", json={"content": "again"})
        wait(client)
        no_model = client.get(base).json()

    assert failed["state"] == "failed"
    assert failed["error"] == "OpenRouter: Insufficient credits (402)"
    assert failed["messages"][-1]["summary"] == {"kind": "error"}
    assert no_model["state"] == "failed"
    assert "no OpenRouter model is selected" in no_model["error"]


def test_stop_ends_a_turn_and_busy_turns_refuse_others(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(tool_reply("list_profiles", {}))
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        gate = client.portal.call(asyncio.Event)  # type: ignore[union-attr]
        scripted.gate = gate
        first = new_conversation(client)["id"]
        second = new_conversation(client)["id"]
        client.post(f"/api/assistant/conversations/{first}/messages", json={"content": "go"})
        running = client.get(f"/api/assistant/conversations/{first}").json()
        busy = client.post(
            f"/api/assistant/conversations/{second}/messages", json={"content": "go"}
        )
        same = client.post(f"/api/assistant/conversations/{first}/messages", json={"content": "x"})
        brief = client.put(
            f"/api/assistant/conversations/{first}/brief",
            json={"subject": "model", "model": {"repo_id": "owner/other"}},
        )
        deleting = client.delete(f"/api/assistant/conversations/{first}")
        stopped = client.post(f"/api/assistant/conversations/{first}/stop").json()
        not_running = client.post(f"/api/assistant/conversations/{second}/stop")

    assert running["state"] == "running"
    assert running["progress"]["round"] == 1
    assert busy.status_code == 409
    assert "already working" in busy.json()["detail"]
    assert "on this conversation" in same.json()["detail"]
    assert brief.status_code == 409
    assert deleting.status_code == 409
    assert stopped["state"] == "idle"
    assert stopped["messages"][-1]["summary"] == {"kind": "stopped"}
    assert not_running.status_code == 200


def test_brief_validation_editing_and_deleting(tmp_path: Path, login: Login) -> None:
    with TestClient(make_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        started(client, login)
        missing_profile = client.post(
            "/api/assistant/conversations",
            json={"brief": {"subject": "profile", "profile_id": "missing"}},
        )
        bad_benchmark = client.post(
            "/api/assistant/conversations",
            json={
                "brief": {
                    "subject": "model",
                    "model": {"repo_id": "owner/m"},
                    "benchmark_id": "nope",
                }
            },
        )
        bad_reasoning = client.post(
            "/api/assistant/conversations",
            json={
                "brief": {
                    "subject": "model",
                    "model": {"repo_id": "owner/m"},
                    "reasoning_level": "galaxy",
                }
            },
        )
        conversation = client.post(
            "/api/assistant/conversations",
            json={
                "title": "  My tuning  ",
                "brief": {"subject": "model", "model": {"repo_id": "owner/m"}},
            },
        ).json()
        base = f"/api/assistant/conversations/{conversation['id']}"
        edited = client.put(
            f"{base}/brief", json={"subject": "model", "model": {"repo_id": "owner/new"}}
        ).json()
        deleted = client.delete(base)
        gone = client.get(base)
        gone_brief = client.put(
            f"{base}/brief", json={"subject": "model", "model": {"repo_id": "owner/new"}}
        )

    assert missing_profile.status_code == 422
    assert bad_benchmark.status_code == 422
    assert bad_reasoning.status_code == 422
    assert conversation["title"] == "My tuning"
    assert edited["brief"]["model"]["repo_id"] == "owner/new"
    assert edited["messages"][-1]["summary"] == {"kind": "brief"}
    assert "admin updated the brief" in edited["messages"][-1]["content"]
    assert deleted.status_code == 204
    assert gone.status_code == 404
    assert gone_brief.status_code == 404


def test_tools_follow_the_senders_privileges(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(text_reply("hi"))
    app = make_app(tmp_path, scripted)
    with TestClient(app, client=LOCAL) as client:
        started(client, login)
        conversation_id = new_conversation(client)["id"]
        engine = app.state.assistant_engine

        async def send() -> None:
            engine.send(conversation_id, "hi", frozenset({"server.read"}))
            await engine.wait()

        client.portal.call(send)  # type: ignore[union-attr]

    assert [tool["function"]["name"] for tool in scripted.requests[0]["tools"]] == [
        "router_status",
        "list_benchmarks",
        "get_benchmark_run",
        "list_benchmark_runs",
        "evaluate_run",
    ]


def test_assistant_routes_are_classified() -> None:
    assert privilege_for_request("/api/assistant/conversations", "GET") == "assistant.read"
    assert privilege_for_request("/api/assistant/conversations/x/messages", "POST") == (
        "assistant.write"
    )
    assert privilege_for_request("/api/settings/assistant", "PUT") == "settings.write"


def test_interrupted_turns_are_marked_at_startup(tmp_path: Path, login: Login) -> None:
    app = make_app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        started(client, login)
        conversation_id = new_conversation(client)["id"]
        app.state.assistant_conversations.update(conversation_id, state="running")
        reported = client.get(f"/api/assistant/conversations/{conversation_id}").json()
    with TestClient(make_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        login(client, "admin", "test-admin-password")
        restarted = client.get(f"/api/assistant/conversations/{conversation_id}").json()

    assert reported["state"] == "interrupted"
    assert restarted["state"] == "interrupted"


def test_status_needs_a_saved_key_and_model(tmp_path: Path, login: Login) -> None:
    with TestClient(make_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        started(client, login)
        before = client.get("/api/assistant/status").json()
        SecretStore().set(SecretName.OPENROUTER_API_KEY, "sk-or-test")
        after = client.get("/api/assistant/status").json()

    assert before["available"] is False
    assert after["available"] is True
    assert after["name"] == "Lama"


# Provider failures and retries --------------------------------------------------------------

NESTED = {
    "error": {
        "code": 502,
        "message": 'data: {"error":{"code":"internal_server_error","message":"Inference engine '
        'abort.","type":"internal_server_error"}}',
    }
}


@pytest.mark.asyncio
async def test_provider_failures_are_unwrapped_and_marked_transient() -> None:
    def respond(response: httpx.Response) -> OpenRouterClient:
        return OpenRouterClient(
            lambda: "k",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)),
        )

    with pytest.raises(OpenRouterError) as nested:
        await _collect(respond(httpx.Response(200, content=_sse(NESTED))))
    with pytest.raises(OpenRouterError) as busy:
        await _collect(respond(httpx.Response(503, json={"error": {"message": "busy"}})))
    with pytest.raises(OpenRouterError) as refused:
        await _collect(respond(httpx.Response(402, json={"error": {"message": "no credits"}})))
    with pytest.raises(OpenRouterError) as named:
        await _collect(
            respond(httpx.Response(200, content=_sse({"error": {"code": "overloaded"}})))
        )
    with pytest.raises(OpenRouterError) as bad_nested:
        await _collect(
            respond(httpx.Response(200, content=_sse({"error": {"message": "data: {nope"}})))
        )

    assert str(nested.value) == "OpenRouter: Inference engine abort."
    assert nested.value.transient is True
    assert busy.value.transient is True
    assert refused.value.transient is False
    assert named.value.transient is True
    assert str(bad_nested.value) == "OpenRouter: data: {nope"
    assert bad_nested.value.transient is False


def _fast_retries(client: TestClient) -> None:
    client.app.state.assistant_engine._retry_delays = (0, 0)  # type: ignore[attr-defined]


def _transient() -> OpenRouterError:
    return OpenRouterError("OpenRouter: Inference engine abort.", transient=True)


def test_a_provider_failure_before_anything_streams_is_retried(
    tmp_path: Path, login: Login
) -> None:
    scripted = ScriptedClient(_transient(), _transient(), text_reply("Recovered."))
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        _fast_retries(client)
        conversation_id = new_conversation(client)["id"]
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "go"}
        )
        wait(client)
        detail = client.get(f"/api/assistant/conversations/{conversation_id}").json()

    assert len(scripted.requests) == 3
    assert detail["state"] == "idle"
    assert detail["messages"][-1]["content"] == "Recovered."
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]


def test_retries_give_up_and_never_repeat_a_paid_or_refused_reply(
    tmp_path: Path, login: Login
) -> None:
    partly = [{"choices": [{"delta": {"content": "Half an ans"}}]}, _transient()]
    scripted = ScriptedClient(
        _transient(),
        _transient(),
        _transient(),
        partly,
        OpenRouterError("OpenRouter: no credits (402)"),
    )
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        _fast_retries(client)
        conversation_id = new_conversation(client)["id"]
        base = f"/api/assistant/conversations/{conversation_id}"
        client.post(f"{base}/messages", json={"content": "go"})
        wait(client)
        exhausted = len(scripted.requests)
        client.post(f"{base}/retry")
        wait(client)
        after_partial = len(scripted.requests)
        client.post(f"{base}/retry")
        wait(client)
        detail = client.get(base).json()

    assert exhausted == 3
    assert after_partial == 4
    assert len(scripted.requests) == 5
    assert detail["state"] == "failed"
    assert detail["error"] == "OpenRouter: no credits (402)"


def test_try_again_carries_on_a_failed_turn(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(OpenRouterError("OpenRouter: down"), text_reply("Back again."))
    with TestClient(make_app(tmp_path, scripted), client=LOCAL) as client:
        started(client, login)
        conversation_id = new_conversation(client)["id"]
        base = f"/api/assistant/conversations/{conversation_id}"
        idle_retry = client.post(f"{base}/retry")
        client.post(f"{base}/messages", json={"content": "go"})
        wait(client)
        failed = client.get(base).json()
        retried = client.post(f"{base}/retry")
        wait(client)
        detail = client.get(base).json()

    assert idle_retry.status_code == 409
    assert failed["state"] == "failed"
    assert retried.status_code == 202
    assert detail["state"] == "idle"
    assert [message["summary"] for message in detail["messages"] if message["role"] == "event"] == [
        {"kind": "error"},
        {"kind": "retry"},
    ]
    assert detail["messages"][-1]["content"] == "Back again."
    # The retried request still ends with the user's message.
    assert scripted.requests[-1]["messages"][-1] == {"role": "user", "content": "go"}
