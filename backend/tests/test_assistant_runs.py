"""Benchmark runs as the assistant sees them: settings fingerprints, baselines, cleanup."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import Login
from fastapi.testclient import TestClient
from test_assistant import LOCAL, ScriptedClient
from test_assistant_actions import _app, _setup

from llamawebui.assistant import web
from llamawebui.assistant.checks import turn_checks
from llamawebui.assistant.library import LibraryError
from llamawebui.assistant.reports import ReportLibrary
from llamawebui.assistant.setup import TOOLS
from llamawebui.assistant.tools import ToolContext, ToolResult, call_tool
from llamawebui.assistant.tools.benchmarks import measured_now
from llamawebui.assistant.tools.models import _ARCH, _SYSTEM, for_this_machine
from llamawebui.assistant.tools.profiles import created_profiles, snapshot_once
from llamawebui.assistant.tools.system import _release_tag
from llamawebui.assistant.web import SearchResult, WebAccess, WebError
from llamawebui.models import AssistantMessageRecord
from llamawebui.services.launch_settings import fingerprint, launch_settings, settings_changes

BRIEF = {"subject": "profile", "profile_id": "p", "reasoning_level": "medium"}


def _context(client: TestClient, conversation_id: str = "", **fields: Any) -> ToolContext:
    return ToolContext(
        client.app.state.assistant_engine.services,  # type: ignore[attr-defined]
        frozenset(tool.privilege for tool in TOOLS),
        brief=fields.pop("brief", BRIEF),
        conversation_id=conversation_id,
        **fields,
    )


def _call(context: ToolContext, client: TestClient, name: str, arguments: dict[str, Any]) -> Any:
    async def run() -> ToolResult:
        return await call_tool(
            {tool.name: tool for tool in TOOLS}, context, name, json.dumps(arguments)
        )

    return client.portal.call(run)  # type: ignore[union-attr]


def _completed(
    client: TestClient,
    conversation_id: str | None,
    started: datetime,
    *,
    decode: float,
    generated: int,
    level: str | None = "medium",
) -> str:
    app: Any = client.app
    profile = app.state.profile_registry.list()[0]
    run = app.state.benchmark_runs.create(
        profile,
        app.state.benchmarks[0],
        None,
        reasoning_level=level,
        conversation_id=conversation_id,
    )
    app.state.benchmark_runs.update(
        run.id,
        state="completed",
        started_at=started,
        stats={
            "decode_tokens_per_second": decode,
            "prompt_tokens_per_second": 2000.0,
            "load_seconds": 6.0,
            "prompt_tokens": 12000,
            "generated_tokens": generated,
            "context_peak": 13000,
        },
        resources=[{"vram_bytes": 19 * 1024**3}],
    )
    return str(run.id)


def _edit_profile(client: TestClient, context_size: int) -> None:
    profile = client.get("/api/profiles").json()[0]
    response = client.put(
        f"/api/profiles/{profile['id']}",
        json={
            "alias": profile["alias"],
            "runtime_id": profile["runtime_id"],
            "model_path": profile["model_path"],
            "arguments": [{"id": "ctx-size", "value": context_size}],
        },
    )
    assert response.status_code == 200, response.text


def test_launch_settings_ignore_argument_order_and_name_what_changed() -> None:
    first = {"model_path": "a.gguf", "runtime_id": "r", "arguments": [{"id": "x", "value": 1}]}
    second = {**first, "model_path": "b.gguf", "arguments": [{"id": "y", "value": 2}]}
    assert fingerprint(first) == fingerprint(dict(reversed(first.items())))
    assert settings_changes(first, second) == [
        {"setting": "model_path", "before": "a.gguf", "after": "b.gguf"},
        {"argument": "x", "before": 1, "after": None},
        {"argument": "y", "before": None, "after": 2},
    ]
    legacy: Any = type(
        "Profile",
        (),
        {"configuration": {"alias": "m", "threads": 8}, "model_path": "m.gguf", "runtime_id": "r"},
    )()
    assert launch_settings(legacy)["arguments"] == [{"id": "threads", "value": 8}]


def test_runs_use_the_briefs_level_and_record_their_settings(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        app: Any = client.app
        context = _context(client, conversation_id)
        approval = client.portal.call(  # type: ignore[union-attr]
            app.state.assistant_engine.tool("run_benchmark").approval_request,
            context,
            {"profile": "test-model"},
        )
        started = json.loads(
            _call(context, client, "run_benchmark", {"profile": "test-model"}).text
        )
        record = app.state.benchmark_runs.get(started["run_id"])
        detail = client.get(f"/api/assistant/conversations/{conversation_id}").json()
        listed = client.get("/api/benchmarks/runs").json()

    assert started["reasoning_level"] == "medium"
    assert started["benchmark"] == "tetris-no-details"
    assert record.reasoning_level == "medium"
    assert record.conversation_id == conversation_id
    assert record.settings_fingerprint == fingerprint(record.launch_settings)
    assert approval[1]["reasoning"] == "medium"
    assert detail["benchmark_runs"] == 1
    assert listed[0]["conversation_id"] == conversation_id


def test_runs_show_their_settings_and_compare_with_a_baseline(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        app: Any = client.app
        now = datetime(2026, 10, 26, 12, 0)
        before = _completed(client, None, now - timedelta(hours=1), decode=100.0, generated=9000)
        base = _completed(client, conversation_id, now, decode=120.0, generated=11000)
        _edit_profile(client, 4096)
        changed = _completed(
            client, conversation_id, now + timedelta(minutes=5), decode=126.0, generated=12000
        )
        busy = _completed(
            client, conversation_id, now + timedelta(minutes=9), decode=101.0, generated=80000
        )
        other_level = _completed(
            client,
            conversation_id,
            now + timedelta(minutes=12),
            decode=90.0,
            generated=1,
            level=None,
        )
        context = _context(client, conversation_id)
        detail = json.loads(_call(context, client, "get_benchmark_run", {"run_id": base}).text)
        current = json.loads(_call(context, client, "get_benchmark_run", {"run_id": changed}).text)
        scored = _call(context, client, "evaluate_run", {"run_id": changed})
        noisy = json.loads(_call(context, client, "evaluate_run", {"run_id": busy}).text)
        first = json.loads(_call(context, client, "evaluate_run", {"run_id": base}).text)
        lonely = json.loads(_call(context, client, "evaluate_run", {"run_id": other_level}).text)
        chosen = json.loads(
            _call(
                context, client, "evaluate_run", {"run_id": changed, "baseline_run_id": before}
            ).text
        )
        app.state.benchmark_runs.update(before, state="failed")
        bad_baseline = _call(
            context, client, "evaluate_run", {"run_id": changed, "baseline_run_id": before}
        )
        runs = json.loads(
            _call(context, client, "list_benchmark_runs", {"limit": 2, "benchmark_id": "x"}).text
        )
        all_runs = json.loads(_call(context, client, "list_benchmark_runs", {"limit": 2}).text)

    assert detail["reasoning_level"] == "medium"
    assert detail["differs_from_profile_now"] == [
        {"argument": "ctx-size", "before": 4096, "after": 8192}
    ]
    assert current["differs_from_profile_now"] == []
    assert scored.summary == "0 of 0 targets met; decode +5.0% vs baseline"
    baseline = json.loads(scored.text)["baseline"]
    assert baseline["baseline_run_id"] == base
    assert baseline["settings_changes"] == [{"argument": "ctx-size", "before": 8192, "after": 4096}]
    assert baseline["warnings"] == []
    decode = next(item for item in baseline["metrics"] if item["metric"] == "decode")
    assert (decode["change_percent"], decode["better"]) == (5.0, True)
    assert noisy["baseline"]["warnings"][0].startswith("generated tokens differ (11,000 vs 80,000)")
    # The conversation's first run compares with the latest comparable run before it.
    assert first["baseline"]["baseline_run_id"] == before
    assert lonely["baseline"] is None
    assert chosen["baseline"]["baseline_run_id"] == before
    assert "cannot be a baseline" in bad_baseline.text
    assert runs == {"runs": [], "total": 0}
    assert len(all_runs["runs"]) == 2
    assert all_runs["total"] == 5
    assert all_runs["runs"][0]["settings"]


def test_a_profile_back_at_measured_settings_needs_no_benchmark(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        services = client.app.state.assistant_engine.services  # type: ignore[attr-defined]
        _completed(client, conversation_id, datetime(2026, 10, 26), decode=120.0, generated=1)
        measured = measured_now(services, "test-model")
        _edit_profile(client, 4096)
        changed = measured_now(services, "test-model")
        _edit_profile(client, 8192)
        reverted = measured_now(services, "test-model")
        missing = measured_now(services, "nope")
        by_id = measured_now(services, services.profiles.list()[0].id)

    assert (measured, changed, reverted, missing, by_id) == (True, False, True, False, True)
    edit = AssistantMessageRecord(
        id="m",
        conversation_id="c",
        seq=1,
        role="tool",
        tool_name="update_profile",
        content=json.dumps({"profile": "test-model", "launch_changed": True}),
        summary={"ok": True},
    )
    assert turn_checks([edit], router_running=False, router_at_start=None) != []
    assert (
        turn_checks([edit], router_running=False, router_at_start=None, measured=lambda name: True)
        == []
    )


def test_profiles_the_conversation_made_are_not_snapshotted(tmp_path: Path, login: Login) -> None:
    def message(name: str, payload: object, *, ok: bool = True, role: str = "tool") -> Any:
        return AssistantMessageRecord(
            id=name,
            conversation_id="c",
            seq=1,
            role=role,
            tool_name=name,
            content=payload if isinstance(payload, str) else json.dumps(payload),
            summary={"ok": ok},
        )

    records = [
        message("clone_profile", {"profile_id": "a"}),
        message("create_profile", {"profile_id": "b"}),
        message("create_profile", {"profile_id": "c"}, ok=False),
        message("create_profile", "not json"),
        message("update_profile", {"profile_id": "d"}),
        message("clone_profile", {"profile_id": "e"}, role="assistant"),
    ]
    assert created_profiles(records) == {"a", "b"}

    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        app: Any = client.app
        record = app.state.profile_registry.list()[0]
        made_here = _context(client, conversation_id, created_profiles=lambda: {record.id})
        skipped = snapshot_once(made_here, record, "before")
        context = _context(client, "other-conversation")
        updated = json.loads(
            _call(
                context,
                client,
                "update_profile",
                {"profile": "test-model", "set_arguments": [{"id": "ctx-size", "value": 4096}]},
            ).text
        )

    assert skipped is None
    assert updated["snapshot_note"].endswith("with restore_snapshot if the work does not pay off.")


def test_runtimes_and_releases_name_their_builds() -> None:
    assert _release_tag("E:/data/runtimes/b11815-cuda-31a007b3/llama-server.exe") == "b11815"
    assert _release_tag("E:/tools/llama/llama-server.exe") is None
    assert for_this_machine(f"llama-b1-bin-{_SYSTEM}-cuda-12.4-{_ARCH}.zip")
    assert not for_this_machine("llama-b1-bin-android-arm64.tar.gz")


def test_argument_search_keeps_only_strong_matches(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _setup(client, login, tmp_path)
        context = _context(client)
        found = json.loads(
            _call(context, client, "search_arguments", {"query": "kv cache type"}).text
        )
        drafts = json.loads(
            _call(context, client, "search_arguments", {"query": "draft n min"}).text
        )

    ids = [match["id"] for match in found["matches"]]
    assert len(ids) <= 12
    assert {"cache-type-k", "cache-type-v"} <= set(ids[:4])
    assert all("default" not in match or match["default"] is not None for match in found["matches"])
    controls = [match.get("control") for match in drafts["matches"]]
    # Removed arguments come after every live one.
    assert controls.index("removed") > max(
        index for index, control in enumerate(controls) if control != "removed"
    )


class _Search:
    def __init__(self, result: SearchResult) -> None:
        self.result = result

    async def search(self, query: str) -> SearchResult:
        return self.result


def test_searches_without_sources_are_marked_unsourced(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _setup(client, login, tmp_path)
        context = _context(client)
        services = context.services
        answers = []
        for result in (
            SearchResult("Qwen3.8 does not exist [1].", []),
            SearchResult("<tool_call>\n<function=web_search>", []),
            SearchResult("It is 120 tok/s.", [{"title": "Post", "url": "https://x.test"}]),
        ):
            object.__setattr__(services, "web", _Search(result))
            answers.append(json.loads(_call(context, client, "web_search", {"query": "q"}).text))

    assert "made up" in answers[0]["warning"]
    assert answers[1] == {
        "answer": "",
        "sources": [],
        "warning": "The search failed: no answer came back. Try different words.",
    }
    assert "warning" not in answers[2]


class _SlowClient:
    async def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        await asyncio.sleep(5)
        yield {}


@pytest.mark.asyncio
async def test_slow_searches_are_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "SEARCH_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(WebError, match="took longer than"):
        await WebAccess(_SlowClient(), lambda: "m").search("q")


def test_search_model_setting_is_used_for_searches(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient()
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _setup(client, login, tmp_path)
        settings = client.get("/api/settings/assistant").json()
        saved = client.put(
            "/api/settings/assistant", json={**settings, "search_model": "perplexity/sonar"}
        )
        invalid = client.put(
            "/api/settings/assistant", json={**settings, "search_model": "has spaces"}
        )
        services = client.app.state.assistant_engine.services  # type: ignore[attr-defined]
        client.portal.call(services.web.search, "q")  # type: ignore[union-attr]

    assert saved.status_code == 200, saved.text
    assert invalid.status_code == 422
    assert scripted.requests[-1]["model"] == "perplexity/sonar"


def test_runs_can_be_deleted_together_and_per_conversation(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        app: Any = client.app
        now = datetime(2026, 10, 26)
        mine = [_completed(client, conversation_id, now, decode=1.0, generated=1) for _ in range(2)]
        others = [_completed(client, None, now, decode=1.0, generated=1) for _ in range(3)]
        profile = app.state.profile_registry.list()[0]
        running = app.state.benchmark_runs.create(
            profile, app.state.benchmarks[0], None, conversation_id=conversation_id
        )
        bulk = client.post(
            "/api/benchmarks/runs/delete", json={"run_ids": [*others[:2], others[0], "nope"]}
        )
        empty = client.post("/api/benchmarks/runs/delete", json={"run_ids": []})
        cleaned = client.post(
            f"/api/assistant/conversations/{conversation_id}/benchmark-runs/delete"
        )
        missing = client.post("/api/assistant/conversations/nope/benchmark-runs/delete")
        left = [run["id"] for run in client.get("/api/benchmarks/runs").json()]
        count = client.get(f"/api/assistant/conversations/{conversation_id}").json()

        role = client.post(
            "/api/auth/roles",
            json={"name": "Chat", "privileges": ["assistant.read", "assistant.write"]},
        ).json()
        client.post(
            "/api/auth/users",
            json={"username": "chat", "password": "chat-secret-1", "role_id": role["id"]},
        )
        client.post("/api/auth/logout")
        login(client, "chat", "chat-secret-1")
        forbidden = client.post(
            f"/api/assistant/conversations/{conversation_id}/benchmark-runs/delete"
        )

    assert bulk.json() == {
        "deleted": others[:2],
        "skipped": [{"run_id": "nope", "reason": "benchmark run not found: nope"}],
    }
    assert empty.status_code == 422
    assert sorted(cleaned.json()["deleted"]) == sorted(mine)
    assert cleaned.json()["skipped"] == [
        {"run_id": running.id, "reason": "cancel the benchmark before deleting it"}
    ]
    assert missing.status_code == 404
    assert sorted(left) == sorted([others[2], running.id])
    assert count["benchmark_runs"] == 1
    assert forbidden.status_code == 403


def test_reports_keep_alternatives_and_what_was_left_behind(tmp_path: Path) -> None:
    library = ReportLibrary(tmp_path / "reports", tmp_path / "shipped")
    written = library.write(
        {
            "slug": "second-sweep",
            "title": "Second sweep",
            "summary": "Nothing moved decode.",
            "outcome": "unchanged",
            "alternatives": [
                {
                    "name": "IQ4_XS clone",
                    "profile": "test-iq4xs",
                    "summary": "Less memory, same speed.",
                    "results": [{"metric": "peak VRAM", "before": 19.49, "after": 17.59}],
                }
            ],
            "left_behind": ["Disabled profile test-iq4xs", "UD-IQ4_XS download (13.3 GiB)"],
        },
        "## Changes",
    )
    meta = library.get("second-sweep").meta

    assert written.created is True
    assert meta.alternatives[0].results[0].after == 17.59
    assert meta.left_behind == ["Disabled profile test-iq4xs", "UD-IQ4_XS download (13.3 GiB)"]
    with pytest.raises(LibraryError, match="name"):
        library.write(
            {
                "slug": "bad",
                "title": "Bad",
                "summary": "x",
                "outcome": "unchanged",
                "alternatives": [{"summary": "no name"}],
            },
            "x",
        )
