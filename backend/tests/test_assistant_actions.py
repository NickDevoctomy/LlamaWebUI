"""The assistant's action tools, approvals and job notifications, with a scripted model."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from conftest import Login
from fastapi.testclient import TestClient
from test_assistant import MODEL, ScriptedClient, text_reply, tool_reply

from llamawebui.app import create_app
from llamawebui.assistant.brief import Brief
from llamawebui.assistant.checks import turn_checks
from llamawebui.assistant.engine import REJECTED, history
from llamawebui.assistant.setup import TOOLS
from llamawebui.assistant.tools import ToolContext, ToolError, ToolResult, call_tool
from llamawebui.assistant.tools.benchmarks import benchmark_notice, evaluation, peak_memory
from llamawebui.assistant.tools.profiles import merge_arguments
from llamawebui.config import Settings
from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.model_manifest import HubFile, group_gguf_files
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.models import AssistantMessageRecord, BenchmarkRunRecord
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.huggingface_catalog import ModelSearchResult, RepositoryManifest
from llamawebui.services.llama_release_installer import ReleaseAsset, ReleaseInfo
from llamawebui.services.runtime_probe import RuntimeProbeResult
from llamawebui.services.settings_store import OPENROUTER_MODEL

LOCAL = ("127.0.0.1", 50000)


async def fake_probe(path: Path) -> RuntimeProbeResult:
    return RuntimeProbeResult(
        executable=path.resolve(),
        version=RuntimeVersion(build="b9000", commit=None, raw="version"),
        capabilities=RuntimeCapabilities(
            options=frozenset({"model", "ctx-size", "models-preset", "n-gpu-layers"}),
            raw_help="help",
        ),
        devices_output=None,
        errors=(),
    )


class FakeHub:
    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 25
    ) -> tuple[ModelSearchResult, ...]:
        return (ModelSearchResult("owner/Model-GGUF", 10, 2, None, False, False, ("gguf",)),)

    async def repository(self, repo_id: str, *, revision: str | None = None) -> RepositoryManifest:
        if repo_id == "owner/missing":
            raise ValueError("no such repository")
        return RepositoryManifest(
            repo_id=repo_id,
            revision="b" * 40,
            groups=group_gguf_files((HubFile("Model-Q4_K_M.gguf", 4 * 1024**3),)),
            readme="# Model card\n" + "x" * 5000,
        )


def _app(tmp_path: Path, client: ScriptedClient) -> Any:
    return create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        catalog=FakeHub(),
        assistant_client=client,
        hardware_detector=lambda: HardwareProfile("Test CPU", 32 * 1024**3, (), 0),
    )


def _setup(client: TestClient, login: Login, tmp_path: Path) -> tuple[str, str]:
    """A signed-in client with a model selected, a runtime and one profile."""
    login(client)
    client.app.state.settings_store.set_json(OPENROUTER_MODEL, MODEL)  # type: ignore[attr-defined]
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    runtime = client.post(
        "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
    ).json()
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    profile = client.post(
        "/api/profiles",
        json={
            "alias": "test-model",
            "runtime_id": runtime["id"],
            "model_path": str(model),
            "arguments": [{"id": "ctx-size", "value": 8192}],
        },
    )
    assert profile.status_code == 201, profile.text
    conversation = client.post(
        "/api/assistant/conversations",
        json={
            "brief": {
                "subject": "profile",
                "profile_id": profile.json()["id"],
                "min_decode_tps": 30,
            }
        },
    ).json()
    return runtime["id"], conversation["id"]


def _wait(client: TestClient) -> None:
    client.portal.call(client.app.state.assistant_engine.wait)  # type: ignore[union-attr,attr-defined]


def _detail(client: TestClient, conversation_id: str) -> dict[str, Any]:
    return client.get(f"/api/assistant/conversations/{conversation_id}").json()  # type: ignore[no-any-return]


def _until(client: TestClient, conversation_id: str, state: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while True:
        detail = _detail(client, conversation_id)
        if detail["state"] == state or time.monotonic() > deadline:
            return detail
        time.sleep(0.02)


def _approvals(client: TestClient, **approvals: bool) -> None:
    response = client.put(
        "/api/settings/assistant",
        json={
            "name": "Lama",
            "spend_limit_usd": 2,
            "max_rounds_per_turn": 10,
            "approvals": approvals,
        },
    )
    assert response.status_code == 200, response.text


def _tool_results(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [message for message in detail["messages"] if message["role"] == "tool"]


# Approvals ----------------------------------------------------------------------------------


def test_an_approved_action_runs_and_a_rejected_one_does_not(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(
        tool_reply("clone_profile", {"profile": "test-model", "alias": "test-copy"}),
        text_reply("Copied."),
        tool_reply("clone_profile", {"profile": "test-model", "alias": "test-other"}, "call-2"),
        text_reply("Understood."),
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        base = f"/api/assistant/conversations/{conversation_id}"
        client.post(f"{base}/messages", json={"content": "copy it"})
        waiting = _until(client, conversation_id, "awaiting_approval")
        approval = waiting["approvals"][0]
        approved = client.post(f"/api/assistant/approvals/{approval['id']}", json={"approve": True})
        _wait(client)
        again = client.post(f"/api/assistant/approvals/{approval['id']}", json={"approve": True})
        client.post(f"{base}/messages", json={"content": "copy it again"})
        second = _until(client, conversation_id, "awaiting_approval")["approvals"][-1]
        client.post(f"/api/assistant/approvals/{second['id']}", json={"approve": False})
        _wait(client)
        detail = _detail(client, conversation_id)
        aliases = [profile["alias"] for profile in client.get("/api/profiles").json()]
        missing = client.post("/api/assistant/approvals/nope", json={"approve": True})

    assert waiting["state"] == "awaiting_approval"
    assert approval["title"] == "Copy profile test-model as test-copy"
    assert approval["category"] == "profiles"
    assert approved.status_code == 200
    assert again.status_code == 409
    assert "already approved" in again.json()["detail"]
    assert sorted(aliases) == ["test-copy", "test-model"]
    results = _tool_results(detail)
    assert results[0]["summary"] == {"summary": "copied as test-copy", "ok": True}
    assert results[1]["content"] == REJECTED
    assert results[1]["summary"]["ok"] is False
    assert [item["state"] for item in detail["approvals"]] == ["approved", "rejected"]
    assert detail["approvals"][0]["decided_by"] == "admin"
    assert detail["state"] == "idle"
    assert missing.status_code == 404


def test_invalid_actions_fail_before_asking_and_switched_off_categories_run(
    tmp_path: Path, login: Login
) -> None:
    scripted = ScriptedClient(
        tool_reply("clone_profile", {"profile": "nope", "alias": "x"}),
        text_reply("That profile does not exist."),
        tool_reply("clone_profile", {"profile": "test-model", "alias": "test-free"}, "call-2"),
        text_reply("Done."),
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        base = f"/api/assistant/conversations/{conversation_id}"
        client.post(f"{base}/messages", json={"content": "copy the missing one"})
        _wait(client)
        _approvals(client, profiles=False)
        client.post(f"{base}/messages", json={"content": "copy it"})
        _wait(client)
        detail = _detail(client, conversation_id)

    results = _tool_results(detail)
    assert "no profile has the id or alias 'nope'" in results[0]["content"]
    assert results[1]["summary"]["ok"] is True
    assert detail["approvals"] == []


def test_stopping_expires_a_pending_approval(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(
        tool_reply("clone_profile", {"profile": "test-model", "alias": "test-copy"})
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        base = f"/api/assistant/conversations/{conversation_id}"
        client.post(f"{base}/messages", json={"content": "copy it"})
        approval = _until(client, conversation_id, "awaiting_approval")["approvals"][0]
        stopped = client.post(f"{base}/stop").json()
        late = client.post(f"/api/assistant/approvals/{approval['id']}", json={"approve": True})

    assert stopped["approvals"][0]["state"] == "expired"
    assert stopped["state"] == "idle"
    assert late.status_code == 409


# Profile tools ------------------------------------------------------------------------------


def test_profile_tools_preview_change_create_and_delete(tmp_path: Path, login: Login) -> None:
    other = tmp_path / "other.gguf"
    other.write_bytes(b"GGUF")
    scripted = ScriptedClient(
        tool_reply(
            "preview_profile",
            {"profile": "test-model", "set_arguments": [{"id": "ctx-size", "value": 16384}]},
        ),
        tool_reply(
            "update_profile",
            {
                "profile": "test-model",
                "set_arguments": [{"id": "ctx-size", "value": 16384}],
                "remove_arguments": [],
            },
            "c2",
        ),
        tool_reply(
            "update_profile",
            {"profile": "test-model", "set_arguments": [{"id": "ctx-size", "value": "lots"}]},
            "c3",
        ),
        tool_reply(
            "update_profile", {"profile": "test-model", "remove_arguments": ["nothing"]}, "c4"
        ),
        tool_reply(
            "create_profile",
            {"alias": "second", "runtime_id": "RUNTIME", "model_path": str(other)},
            "c5",
        ),
        tool_reply(
            "create_profile",
            {"alias": "second", "runtime_id": "RUNTIME", "model_path": str(other)},
            "c6",
        ),
        tool_reply("delete_profile", {"profile": "second"}, "c7"),
        text_reply("Done."),
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        runtime_id, conversation_id = _setup(client, login, tmp_path)
        for reply in scripted.replies:
            if isinstance(reply, list):
                for chunk in reply:
                    for choice in chunk.get("choices", []):  # type: ignore[union-attr]
                        for call in choice.get("delta", {}).get("tool_calls", []):
                            arguments = call["function"].get("arguments")
                            if arguments:
                                call["function"]["arguments"] = arguments.replace(
                                    "RUNTIME", runtime_id
                                )
        _approvals(client, profiles=False)
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "tune"}
        )
        _wait(client)
        detail = _detail(client, conversation_id)
        profiles = {profile["alias"]: profile for profile in client.get("/api/profiles").json()}

    results = _tool_results(detail)
    preview = json.loads(results[0]["content"])
    assert preview["changes"] == [{"argument": "ctx-size", "before": 8192, "after": 16384}]
    assert preview["valid"] is True
    assert "16384" in preview["command"]
    updated = json.loads(results[1]["content"])
    assert updated["version"] == 2
    assert "invalid arguments" in results[2]["content"]
    assert "nothing would change" in results[3]["content"]
    assert json.loads(results[4]["content"])["alias"] == "second"
    assert "already registered: second" in results[5]["content"]
    assert json.loads(results[6]["content"]) == {"deleted": "second"}
    assert set(profiles) == {"test-model"}
    assert profiles["test-model"]["configuration"]["arguments"] == [
        {"id": "ctx-size", "value": 16384}
    ]


def test_merge_arguments_replaces_adds_and_removes() -> None:
    catalog = default_catalog()
    current = [{"id": "ctx-size", "value": 8192}, {"id": "n-gpu-layers", "value": 10}]
    merged = merge_arguments(
        current,
        [{"flag": "--ctx-size", "value": 4096}, {"id": "threads", "value": 8}],
        ["n-gpu-layers"],
        catalog,
    )
    assert merged == [{"flag": "--ctx-size", "value": 4096}, {"id": "threads", "value": 8}]
    assert merge_arguments(current, [], ["--n-gpu-layers"], catalog) == [current[0]]


# Model tools -------------------------------------------------------------------------------


def test_model_tools_search_describe_and_plan_downloads(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(
        tool_reply("search_models", {"query": "model"}),
        tool_reply("get_repository", {"repo_id": "owner/Model-GGUF"}, "c2"),
        tool_reply("get_repository", {"repo_id": "owner/missing"}, "c3"),
        tool_reply("queue_download", {"repo_id": "owner/Model-GGUF", "group_key": "nope"}, "c4"),
        text_reply("Found it."),
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "find"}
        )
        _wait(client)
        detail = _detail(client, conversation_id)

    results = _tool_results(detail)
    assert json.loads(results[0]["content"])[0]["repo_id"] == "owner/Model-GGUF"
    repository = json.loads(results[1]["content"])
    assert repository["quantization_groups"][0]["size_gib"] == 4.0
    assert repository["readme"].endswith("… (cut)")
    assert "could not read owner/missing" in results[2]["content"]
    # The bad group is caught while wording the approval, so nothing was asked.
    assert "has no quantization group 'nope'" in results[3]["content"]
    assert detail["approvals"] == []


# Benchmarks and notifications --------------------------------------------------------------


def test_a_failed_benchmark_notifies_and_wakes_the_conversation(
    tmp_path: Path, login: Login
) -> None:
    scripted = ScriptedClient(
        tool_reply("run_benchmark", {"profile": "test-model"}),
        text_reply("Started; I will report back."),
        text_reply("The benchmark failed because the runtime did not start."),
    )
    app = _app(tmp_path, scripted)
    with TestClient(app, client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        _approvals(client, benchmarks=False)
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "bench"}
        )
        _wait(client)
        client.portal.call(app.state.benchmark_runner.wait)  # type: ignore[union-attr]
        _wait(client)
        detail = _detail(client, conversation_id)

    notifications = [
        message
        for message in detail["messages"]
        if (message["summary"] or {}).get("kind") == "notification"
    ]
    assert len(notifications) == 1
    assert notifications[0]["summary"]["ok"] is False
    assert "failed" in notifications[0]["content"]
    replies = [
        message["content"] for message in detail["messages"] if message["role"] == "assistant"
    ]
    assert replies[-2].startswith("The benchmark failed")
    # It benchmarked, so before finishing it is reminded to write a report.
    assert detail["messages"][-2]["summary"] == {"kind": "check", "reminders": ["report"]}
    # The woken turn sent the notification to the model as a user message.
    assert scripted.requests[-2]["messages"][-1]["content"].startswith(
        "[Notification] Benchmark run"
    )


def test_notifications_only_reach_the_conversation_that_started_the_job(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ScriptedClient(text_reply("Noted."))), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        engine = client.app.state.assistant_engine  # type: ignore[attr-defined]
        engine.notify("download", "unwatched", "Download done.", True)
        client.post(f"/api/assistant/conversations/{conversation_id}/stop")
        engine.watch(conversation_id, frozenset({"server.read"}), "download", "job-1")
        client.app.state.assistant_conversations.update(conversation_id, state="paused")  # type: ignore[attr-defined]
        client.portal.call(lambda: _notify(engine, "job-1"))  # type: ignore[union-attr]
        paused = _detail(client, conversation_id)

    assert [message["content"] for message in paused["messages"]] == ["Download job-1 completed."]
    assert paused["state"] == "paused"


async def _notify(engine: Any, job_id: str) -> None:
    engine.notify("download", job_id, f"Download {job_id} completed.", True)


def _message(role: str, seq: int, **fields: Any) -> AssistantMessageRecord:
    return AssistantMessageRecord(
        id=str(seq),
        conversation_id="c",
        seq=seq,
        role=role,
        content=fields.pop("content", ""),
        **fields,
    )


def test_history_holds_notifications_until_tool_results_are_in() -> None:
    call = {"id": "a", "type": "function", "function": {"name": "x", "arguments": "{}"}}
    records = [
        _message("user", 1, content="go"),
        _message("assistant", 2, tool_calls=[call]),
        _message("event", 3, content="Download done.", summary={"kind": "notification"}),
        _message("tool", 4, content="ok", tool_call_id="a"),
        _message("event", 5, content="Brief edited.", summary={"kind": "brief"}),
    ]
    assert [(item["role"], item.get("content")) for item in history(records)] == [
        ("user", "go"),
        ("assistant", ""),
        ("tool", "ok"),
        ("user", "[Notification] Download done."),
    ]


def _run(**fields: Any) -> BenchmarkRunRecord:
    defaults: dict[str, Any] = {
        "id": "run-1",
        "profile_alias": "test-model",
        "benchmark_title": "Tetris",
        "state": "completed",
        "error": None,
        "stats": {
            "load_seconds": 12.5,
            "prompt_tokens_per_second": 900.0,
            "decode_tokens_per_second": 25.0,
            "context_size": 32768,
        },
        "resources": [
            {"ram_bytes": 10 * 1024**3, "vram_bytes": 4 * 1024**3},
            {"ram_bytes": 12 * 1024**3},
        ],
    }
    return BenchmarkRunRecord(**(defaults | fields))


def test_benchmark_results_are_summarised_and_scored() -> None:
    record = _run()
    assert peak_memory(record) == {"ram_gib": 12.0, "vram_gib": 4.0}
    assert benchmark_notice(record) == (
        "Benchmark run run-1 (Tetris on test-model) completed: load 12.5 s, prefill 900.0 "
        "tok/s, decode 25.0 tok/s, peak RAM 12.0 GiB, peak VRAM 4.0 GiB."
    )
    assert "failed: boom" in benchmark_notice(_run(state="failed", error="boom"))
    brief = Brief.model_validate(
        {
            "subject": "profile",
            "profile_id": "p",
            "min_decode_tps": 30,
            "max_load_seconds": 20,
            "max_vram_gb": 8,
            "min_context_tokens": 65536,
            "priority": ["load", "decode", "prefill", "memory"],
        }
    )
    result = evaluation(brief, record)
    assert [item["target"] for item in result["targets"]] == [  # type: ignore[index]
        "load seconds (at most)",
        "decode tokens/s (at least)",
        "VRAM GiB (at most)",
        "context tokens (at least, never traded away)",
    ]
    assert [item["met"] for item in result["targets"]] == [True, False, True, False]  # type: ignore[index]
    assert result["targets"][1]["gap"] == -5.0  # type: ignore[index]
    assert result["targets_met"] == 2
    assert result["all_met"] is False


# Direct tool calls ---------------------------------------------------------------------


class InstantTransfer:
    async def download(
        self, *, repo_id: str, filename: str, revision: str, destination: Path
    ) -> Path:
        target = destination / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        return target


def _context(
    client: TestClient, watched: list[tuple[str, str]] | None = None, **brief: Any
) -> ToolContext:
    return ToolContext(
        client.app.state.assistant_engine.services,  # type: ignore[attr-defined]
        frozenset(tool.privilege for tool in TOOLS),
        brief=brief or {"subject": "profile", "profile_id": "p", "min_decode_tps": 30},
        watch=lambda kind, job_id: (watched if watched is not None else []).append((kind, job_id)),
    )


def _call(client: TestClient, name: str, arguments: dict[str, Any], **brief: Any) -> ToolResult:
    context = _context(client, **brief)

    async def run() -> ToolResult:
        return await call_tool(
            {tool.name: tool for tool in TOOLS}, context, name, json.dumps(arguments)
        )

    return client.portal.call(run)  # type: ignore[union-attr,no-any-return]


def _request(client: TestClient, name: str, arguments: dict[str, Any]) -> Any:
    """What a tool's approval card would say, or the error that stops it being asked."""
    engine = client.app.state.assistant_engine  # type: ignore[attr-defined]
    context = _context(client)

    async def run() -> Any:
        try:
            return await engine.tool(name).approval_request(context, arguments)
        except ToolError as error:
            return error

    return client.portal.call(run)  # type: ignore[union-attr]


def test_benchmark_tools_read_and_score_runs(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        runtime_id, _ = _setup(client, login, tmp_path)
        listed = _call(client, "list_benchmarks", {})
        unknown = _call(client, "run_benchmark", {"profile": "test-model", "benchmark_id": "nope"})
        profile = app.state.profile_registry.list()[0]
        run = app.state.benchmark_runs.create(profile, app.state.benchmarks[0], None)
        app.state.benchmark_runs.update(
            run.id,
            state="completed",
            stats={"decode_tokens_per_second": 31.0, "load_seconds": 9.0},
            resources=[{"ram_bytes": 2 * 1024**3}],
        )
        detail = _call(client, "get_benchmark_run", {"run_id": run.id})
        runs = _call(client, "list_benchmark_runs", {"profile": "test-model"})
        scored = _call(client, "evaluate_run", {"run_id": run.id})
        missing = _call(client, "get_benchmark_run", {"run_id": "nope"})
        bad_brief = _call(client, "evaluate_run", {"run_id": run.id}, subject="nonsense")
        app.state.benchmark_runs.update(run.id, state="failed")
        unscored = _call(client, "evaluate_run", {"run_id": run.id})
        stop = _call(client, "stop_router", {})
        start_bad = _call(client, "start_router", {"runtime_id": "nope"})
        start = _call(client, "start_router", {"runtime_id": runtime_id})

    assert json.loads(listed.text)[0]["id"]
    assert "there is no benchmark 'nope'" in unknown.text
    assert json.loads(detail.text)["peak"] == {"ram_gib": 2.0, "vram_gib": None}
    assert json.loads(runs.text)[0]["run_id"] == run.id
    assert scored.summary == "1 of 1 targets met"
    assert missing.ok is False
    assert "brief could not be read" in bad_brief.text
    assert "has no results to score" in unscored.text
    assert stop.ok is True
    assert start_bad.ok is False
    # The fake runtime is an empty file, so the router cannot really launch.
    assert start.ok is False


def test_approval_requests_check_their_preconditions(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        runtime_id, _ = _setup(client, login, tmp_path)
        bench = _request(client, "run_benchmark", {"profile": "test-model"})
        stop = _request(client, "stop_router", {})
        start = _request(client, "start_router", {"runtime_id": runtime_id})
        start_missing = _request(client, "start_router", {"runtime_id": "nope"})
        delete = _request(client, "delete_profile", {"profile": "test-model"})
        create = _request(
            client,
            "create_profile",
            {
                "alias": "new-one",
                "runtime_id": runtime_id,
                "model_path": str(tmp_path / "model.gguf"),
            },
        )
        install = _request(
            client,
            "install_runtime",
            {"tag": "b9000", "asset_name": "llama.zip", "backend": "cuda"},
        )
        download = _request(
            client, "queue_download", {"repo_id": "owner/Model-GGUF", "group_key": "Model-Q4_K_M"}
        )
        app.state.profile_registry.set_enabled(app.state.profile_registry.list()[0].id, False)
        disabled = _request(client, "run_benchmark", {"profile": "test-model"})

    assert bench[0] == "Benchmark test-model with Tetris no details"
    assert isinstance(stop, ToolError) and "not running" in str(stop)
    assert start[0] == "Start the router with CPU"
    assert isinstance(start_missing, ToolError)
    assert delete[0] == "Delete profile test-model"
    assert create[0] == "Create profile new-one"
    assert install == (
        "Install llama.cpp b9000 (llama.zip)",
        {"tag": "b9000", "asset_name": "llama.zip", "backend": "cuda"},
    )
    assert download[0] == "Download owner/Model-GGUF · Q4_K_M (4.0 GiB)"
    assert isinstance(disabled, ToolError) and "is disabled" in str(disabled)


def test_download_and_runtime_tools(tmp_path: Path, login: Login, monkeypatch: Any) -> None:
    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        catalog=FakeHub(),
        file_transfer=InstantTransfer(),
        assistant_client=ScriptedClient(),
        hardware_detector=lambda: HardwareProfile("Test CPU", 32 * 1024**3, (), 0),
    )
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        watched: list[tuple[str, str]] = []
        context = _context(client, watched)
        tools = {tool.name: tool for tool in TOOLS}

        async def queue(group_key: str) -> ToolResult:
            arguments = json.dumps({"repo_id": "owner/Model-GGUF", "group_key": group_key})
            return await call_tool(tools, context, "queue_download", arguments)

        queued = client.portal.call(queue, "Model-Q4_K_M")  # type: ignore[union-attr]
        bad = client.portal.call(queue, "nope")  # type: ignore[union-attr]

        async def release(tag: str) -> ReleaseInfo:
            return ReleaseInfo(
                "b9000",
                None,
                (
                    ReleaseAsset("llama-b9000-bin-win-cpu-x64.zip", "u", 1, None),
                    ReleaseAsset("llama-b9000-bin-win-cuda-12.4-x64.zip", "u", 1, None),
                ),
            )

        monkeypatch.setattr(app.state.release_client, "release", release)
        releases = _call(client, "list_runtime_releases", {})

        async def install(tag: str, asset_name: str, *, backend: str | None = None) -> Any:
            return app.state.runtime_registry.list()[0]

        monkeypatch.setattr(app.state.control_plane, "install_runtime", install)
        installed = _call(client, "install_runtime", {"tag": "b9000", "asset_name": "x.zip"})

    assert queued.ok is True, queued.text
    assert watched == [("download", json.loads(queued.text)["download_id"])]
    assert bad.ok is False
    builds = json.loads(releases.text)["builds"]
    assert builds["cpu"] == ["llama-b9000-bin-win-cpu-x64.zip"]
    assert builds["cuda"] == ["llama-b9000-bin-win-cuda-12.4-x64.zip"]
    assert json.loads(installed.text)["name"] == "CPU"


# End-of-turn checks -------------------------------------------------------------------------


def _result(seq: int, name: str, content: object, ok: bool = True) -> AssistantMessageRecord:
    return _message(
        "tool",
        seq,
        content=json.dumps(content) if not isinstance(content, str) else content,
        tool_name=name,
        summary={"ok": ok},
    )


def _check(seq: int, *reminders: str) -> AssistantMessageRecord:
    summary: dict[str, object] = {"kind": "check"}
    if reminders:
        summary["reminders"] = list(reminders)
    return _message("event", seq, content="reminder", summary=summary)


def _kinds(reminders: list[tuple[str, str]]) -> list[str]:
    return [kind for kind, _ in reminders]


def test_turn_checks_remind_once_about_each_forgotten_step() -> None:
    stopped = {"runtime_id": None}
    changed = [
        _result(1, "update_profile", {"profile": "a"}),
        _result(2, "create_profile", {"alias": "b"}),
        _result(3, "update_profile", {"profile": "d", "launch_changed": False}),
        _result(4, "update_profile", {"profile": "c"}, ok=False),
        _result(5, "update_profile", "not json"),
        _result(6, "update_profile", {"profile": "e"}),
        _result(7, "restore_snapshot", {"profile": "e"}),
    ]
    [(kind, reminder)] = turn_checks(changed, router_running=False, router_at_start=stopped)
    assert kind == "unmeasured"
    assert reminder.startswith("You changed a, b, a profile after its last benchmark")
    # A check from before reminders had kinds covered unmeasured changes.
    assert turn_checks([*changed, _check(8)], router_running=False, router_at_start=stopped) == []

    started = [_result(1, "start_router", {"router": "running"})]
    [(_, left_running)] = turn_checks(started, router_running=True, router_at_start=stopped)
    assert "Stop it with stop_router" in left_running
    assert (
        turn_checks([*started, _check(2, "router")], router_running=True, router_at_start=stopped)
        == []
    )
    assert turn_checks(started, router_running=True, router_at_start=None) == []
    assert turn_checks(started, router_running=True, router_at_start={"runtime_id": "x"}) == []

    # Benchmarks leave results to report; the operator starting the router is not the
    # assistant's to undo.
    benchmarked = [_result(1, "run_benchmark", {"profile": "a"})]
    assert _kinds(turn_checks(benchmarked, router_running=True, router_at_start=stopped)) == [
        "report"
    ]
    restore = turn_checks(benchmarked, router_running=False, router_at_start={"runtime_id": "rt-1"})
    assert _kinds(restore) == ["router", "report"]
    assert "running with runtime rt-1" in restore[0][1]
    reported = [*benchmarked, _result(2, "write_report", {"slug": "r"})]
    assert turn_checks(reported, router_running=False, router_at_start=stopped) == []
    reminded = [*benchmarked, _check(2, "report")]
    assert turn_checks(reminded, router_running=False, router_at_start=stopped) == []

    # Working autonomously, a question to the operator gets one nudge per message.
    asking = [*reported, _message("assistant", 3, content="Shall I try q8_0 next? **")]
    [(kind, nudge)] = turn_checks(
        asking, router_running=False, router_at_start=stopped, autonomous=True
    )
    assert (kind, "nobody will answer" in nudge) == ("question", True)
    assert turn_checks(asking, router_running=False, router_at_start=stopped) == []
    nudged = [*asking, _check(4, "question"), _message("assistant", 5, content="Or q4_0?")]
    assert turn_checks(nudged, router_running=False, router_at_start=stopped, autonomous=True) == []
    answered = [
        *nudged,
        _message("user", 6, content="go on"),
        _message("assistant", 7, content="Ok?"),
    ]
    assert _kinds(
        turn_checks(answered, router_running=False, router_at_start=stopped, autonomous=True)
    ) == ["question"]


def test_an_unmeasured_change_gets_one_reminder_before_the_turn_ends(
    tmp_path: Path, login: Login
) -> None:
    scripted = ScriptedClient(
        tool_reply(
            "update_profile",
            {"profile": "test-model", "set_arguments": [{"id": "ctx-size", "value": 16384}]},
        ),
        text_reply("Changed the context size."),
        text_reply("I will leave it unmeasured; the operator asked only for the edit."),
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        _approvals(client, profiles=False)
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "edit"}
        )
        _wait(client)
        detail = _detail(client, conversation_id)
        record = client.app.state.assistant_conversations.get(conversation_id)  # type: ignore[attr-defined]

    checks = [m for m in detail["messages"] if (m["summary"] or {}).get("kind") == "check"]
    assert len(checks) == 1
    assert "You changed test-model" in checks[0]["content"]
    assert len(scripted.requests) == 3
    assert scripted.requests[-1]["messages"][-1]["content"].startswith("[Check] You changed")
    assert _tool_results(detail)[0]["summary"]["summary"] == "updated test-model to v2"
    assert record.router_at_start == {"runtime_id": None}
    assert detail["state"] == "idle"


def test_profiles_cannot_change_while_a_benchmark_runs(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        app.state.benchmark_runner._active = "run-1"
        refused = _call(
            client,
            "update_profile",
            {"profile": "test-model", "set_arguments": [{"id": "ctx-size", "value": 4096}]},
        )
        deleted = _call(client, "delete_profile", {"profile": "test-model"})
        app.state.benchmark_runner._active = None

    assert "a benchmark is running" in refused.text
    assert "a benchmark is running" in deleted.text
