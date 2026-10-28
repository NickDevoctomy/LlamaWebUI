"""Profile snapshots, reports and auto-publishing: how the assistant remembers and undoes work."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import Login
from fastapi.testclient import TestClient
from test_assistant import LOCAL, ScriptedClient, text_reply, tool_reply
from test_assistant_actions import _app, _approvals, _call, _setup, _wait

from llamawebui.assistant.library import LibraryError
from llamawebui.assistant.reports import ReportLibrary, charts_in
from llamawebui.assistant.research import ResearchLibrary
from llamawebui.assistant.setup import snapshots_text

CHART = {
    "type": "line",
    "title": "Decode speed per change",
    "categories": ["baseline", "n-max 4"],
    "series": [{"name": "decode", "values": [112.2, 120.6]}],
    "y_label": "tok/s",
    "targets": [{"label": "target", "value": 115}],
}
REPORT: dict[str, Any] = {
    "slug": "qwen-27b-tuning",
    "title": "Tuning Qwen3.8 27B",
    "summary": "MTP draft depth 4 and a larger batch raised decode by 7%.",
    "subject": {"profile": "test-model"},
    "hardware": ["RTX 4090"],
    "outcome": "improved",
    "results": [{"metric": "decode", "before": 112.2, "after": 120.6, "unit": "tok/s"}],
}


def _body(*charts: object) -> str:
    blocks = "\n\n".join(f"```chart\n{json.dumps(chart)}\n```" for chart in charts)
    return f"## Changes\n\n{blocks}\n"


# Snapshots ------------------------------------------------------------------------------------


def test_snapshots_can_be_taken_listed_restored_and_deleted(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, ScriptedClient()), client=LOCAL) as client:
        _setup(client, login, tmp_path)
        profile = client.get("/api/profiles").json()[0]
        base = f"/api/profiles/{profile['id']}/snapshots"
        taken = client.post(base, json={"name": "Known good"})
        edited = dict(profile)
        changed = client.put(
            f"/api/profiles/{profile['id']}",
            json={
                "alias": "test-model",
                "runtime_id": profile["runtime_id"],
                "model_path": profile["model_path"],
                "arguments": [{"id": "ctx-size", "value": 4096}],
                "description": "edited",
            },
        )
        listed = client.get(base).json()
        restored = client.post(f"{base}/{taken.json()['id']}/restore")
        missing = client.post(f"{base}/nope/restore")
        other = client.get("/api/profiles/nope/snapshots")
        no_name = client.post(base, json={"name": ""})
        no_profile = client.post("/api/profiles/nope/snapshots", json={"name": "x"})
        deleted = client.delete(f"{base}/{taken.json()['id']}")
        after = client.get(base).json()

    assert taken.status_code == 201
    assert changed.status_code == 200, changed.text
    assert [item["name"] for item in listed] == ["Known good"]
    assert restored.status_code == 200
    assert restored.json()["description"] == edited["description"]
    assert restored.json()["version"] == profile["version"]
    assert [argument["value"] for argument in restored.json()["configuration"]["arguments"]] == [
        8192
    ]
    assert missing.status_code == 404
    assert other.status_code == 404
    assert no_name.status_code == 422
    assert no_profile.status_code == 404
    assert deleted.status_code == 204
    assert after == []


def test_the_assistant_snapshots_before_changing_and_can_restore(
    tmp_path: Path, login: Login
) -> None:
    scripted = ScriptedClient(
        tool_reply(
            "update_profile",
            {"profile": "test-model", "set_arguments": [{"id": "ctx-size", "value": 16384}]},
        ),
        text_reply("Changed."),
    )
    app = _app(tmp_path, scripted)
    with TestClient(app, client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        _approvals(client, profiles=False)
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "go"}
        )
        _wait(client)
        services = app.state.assistant_engine.services
        snapshots = services.snapshots.list(conversation_id=conversation_id)
        prompt = scripted.requests[0]["messages"][0]["content"]
        listed = json.loads(_call(client, "list_snapshots", {"profile": "test-model"}).text)
        made = _call(client, "create_snapshot", {"profile": "test-model", "name": "Mine"})
        restore = _call(client, "restore_snapshot", {"snapshot_id": snapshots[0].id})
        unknown = _call(client, "restore_snapshot", {"snapshot_id": "nope"})
        profile = app.state.profile_registry.list()[0]
        text = snapshots_text(services, conversation_id)
        app.state.profile_registry.remove(profile.id)
        gone = snapshots_text(services, conversation_id)

    # The brief's profile is snapshotted as the conversation begins; the edit then finds
    # that snapshot and takes no second one.
    assert [snapshot.name for snapshot in snapshots] == ["Before “Optimise test-model”"]
    assert snapshots[0].id in prompt
    assert listed[0]["arguments"][0]["value"] == 8192
    assert made.summary == "snapshot of test-model"
    assert restore.summary == "restored test-model"
    assert profile.configuration["arguments"][0]["value"] == 8192
    assert unknown.ok is False
    assert "test-model" in text
    assert gone == "None yet."


def test_a_profile_changed_mid_conversation_is_snapshotted_once(
    tmp_path: Path, login: Login
) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        services = app.state.assistant_engine.services
        from llamawebui.assistant.tools import ToolContext
        from llamawebui.assistant.tools.profiles import snapshot_once

        profile = app.state.profile_registry.list()[0]
        context = ToolContext(services, frozenset(), conversation_id="c-1")
        first = snapshot_once(context, profile, "before")
        second = snapshot_once(context, profile, "before")
        anonymous = snapshot_once(ToolContext(services, frozenset()), profile, "before")

    assert first is not None
    assert second is None
    assert anonymous is None


def test_restoring_waits_for_the_router_and_benchmarks(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        profile = app.state.profile_registry.list()[0]
        snapshot = app.state.profile_snapshots.create(profile.id, "x", created_by="t")
        app.state.benchmark_runner._active = "run-1"
        busy = _call(client, "restore_snapshot", {"snapshot_id": snapshot.id})
        engine = app.state.assistant_engine

        async def describe() -> Any:
            try:
                return await engine.tool("restore_snapshot").approval_request(
                    __import__("test_assistant_actions")._context(client),
                    {"snapshot_id": snapshot.id},
                )
            except Exception as error:  # noqa: BLE001 - the refusal is the result
                return error

        refused = client.portal.call(describe)
        app.state.benchmark_runner._active = None
        title, details = client.portal.call(describe)

    assert "a benchmark is running" in busy.text
    assert "a benchmark is running" in str(refused)
    assert title == "Restore test-model to snapshot “x”"
    assert details["snapshot"] == "x"


# Reports --------------------------------------------------------------------------------------


def test_report_charts_are_checked() -> None:
    assert charts_in(_body(CHART, {**CHART, "type": "bar"}))[1].type == "bar"
    with pytest.raises(ValueError, match="chart 1 is not valid JSON"):
        charts_in("```chart\n{nope\n```")
    with pytest.raises(ValueError, match="has 1 values for 2 categories"):
        charts_in(_body({**CHART, "series": [{"name": "decode", "values": [1]}]}))


def test_reports_are_written_searched_and_published(tmp_path: Path) -> None:
    shipped = tmp_path / "repo" / "reports"
    shipped.mkdir(parents=True)
    library = ReportLibrary(tmp_path / "local", shipped, today=lambda: date(2026, 10, 26))
    written = library.write(REPORT, _body(CHART))
    with pytest.raises(LibraryError, match="chart 1"):
        library.write({**REPORT, "slug": "bad"}, "```chart\n[]\n```")
    not_checkout = library.write({**REPORT, "slug": "second"}, "Body.", publish=True)
    (tmp_path / "repo" / ".git").mkdir()
    published = library.write({**REPORT, "slug": "second"}, "Body, again.", publish=True)

    assert written.document.meta.created == date(2026, 10, 26)
    assert written.published is False
    assert (
        not_checkout.publish_problem == "publishing needs LlamaWebUI to run from a source checkout"
    )
    assert published.published is True
    assert published.document.origin == "shipped"
    assert not (tmp_path / "local" / "second.md").exists()
    assert {item.meta.slug for item, _ in library.search("4090")} == {"qwen-27b-tuning", "second"}
    assert "outcome improved; test-model" in library.index_text()


def test_report_tools_and_routes(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        # Never the package's own folder: publishing must not write into the checkout.
        shipped = tmp_path / "repo" / "reports"
        shipped.mkdir(parents=True)
        library = ReportLibrary(tmp_path / "local-reports", shipped)
        app.state.report_library = library
        object.__setattr__(app.state.assistant_engine.services, "reports", library)
        written = _call(client, "write_report", {**REPORT, "body": _body(CHART)})
        no_body = _call(client, "write_report", REPORT)
        found = json.loads(_call(client, "search_reports", {"query": "qwen"}).text)
        read = _call(client, "read_report", {"slug": "qwen-27b-tuning"})
        missing = _call(client, "read_report", {"slug": "nope"})
        listed = client.get("/api/assistant/reports").json()
        one = client.get("/api/assistant/reports/qwen-27b-tuning").json()
        refused = client.post("/api/assistant/reports/publish", json={"slugs": ["qwen-27b-tuning"]})
        (tmp_path / "repo" / ".git").mkdir()
        published = client.post(
            "/api/assistant/reports/publish", json={"slugs": ["qwen-27b-tuning"]}
        )
        _call(client, "write_report", {**REPORT, "slug": "scratch", "body": "Body."})
        deleted = client.delete("/api/assistant/reports/scratch")

    assert json.loads(written.text) == {
        "slug": "qwen-27b-tuning",
        "created": True,
        "published": False,
    }
    assert no_body.ok is False
    assert found[0]["outcome"] == "improved"
    assert "```chart" in read.text
    assert missing.ok is False
    assert listed["documents"][0]["results"][0]["after"] == 120.6
    assert listed["publish_problem"] == "publishing needs LlamaWebUI to run from a source checkout"
    assert one["body"].startswith("## Changes")
    assert refused.status_code == 409
    assert published.json()["published"] == ["qwen-27b-tuning"]
    assert (shipped / "qwen-27b-tuning.md").is_file()
    assert deleted.status_code == 204


def test_auto_publish_writes_into_the_shipped_folder(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        shipped = tmp_path / "repo" / "research"
        shipped.mkdir(parents=True)
        (tmp_path / "repo" / ".git").mkdir()
        library = ResearchLibrary(tmp_path / "local-research", shipped)
        object.__setattr__(app.state.assistant_engine.services, "research", library)
        settings = client.get("/api/settings/assistant").json()
        switched = client.put("/api/settings/assistant", json={**settings, "auto_publish": True})
        written = _call(
            client,
            "write_research",
            {
                "slug": "flash-attention",
                "title": "Flash attention",
                "summary": "On for CUDA.",
                "sources": [{"title": "README", "url": "https://example.com"}],
                "confidence": "high",
                "body": "Turn it on.",
            },
        )
    with TestClient(app, client=("10.0.0.5", 50000)) as remote:
        login(remote)
        client_settings = {**settings, "auto_publish": False}
        remote.put("/api/settings/assistant", json=client_settings)
        refused = remote.put("/api/settings/assistant", json={**settings, "auto_publish": True})

    assert switched.json()["auto_publish"] is True
    assert json.loads(written.text)["published"] is True
    assert (shipped / "flash-attention.md").is_file()
    assert refused.status_code == 403
