from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine
from test_benchmark_linter import WIN_ZIP, FakeCommands, FakeDownloads, _node_zip
from test_benchmarks import BENCHMARK, FakeBenchmarkClient, _database, _runner

from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.benchmark import (
    BenchmarkError,
    SystemPrompt,
    load_benchmarks,
    load_system_prompts,
)
from llamawebui.services import benchmark_runner
from llamawebui.services.benchmark_linter import BenchmarkLinter, CommandResult
from llamawebui.services.benchmark_runner import (
    TOOL_NAMES,
    BenchmarkRunRegistry,
    _call_tool,
    choose_tools,
    tool_system_prompt,
)
from llamawebui.services.benchmark_workspace import (
    BenchmarkWorkspace,
    WorkspaceError,
    remove_run_folder,
    run_folder,
)
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.router_client import RouterAPIError


def profile_results(engine: Engine, profile_id: str) -> dict[str, object] | None:
    return ProfileRegistry(engine, default_catalog()).get(profile_id).benchmark_results


def _tool_call(index: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": f"call-{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
            }
        ]
    }


def _round(
    parts: list[dict[str, object]], *, finish: str, generated: int, milliseconds: float
) -> list[dict[str, object]]:
    """One request's reply, closed by a chunk with llama.cpp's totals for that request."""
    timings = {
        "prompt_n": 10,
        "prompt_ms": 20.0,
        "predicted_n": generated,
        "predicted_ms": milliseconds,
    }
    return [*parts, {"choices": [{"delta": {}, "finish_reason": finish}], "timings": timings}]


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("C:/test.py", "absolute paths and drives are not allowed"),
        ("c:test.py", "absolute paths and drives are not allowed"),
        ("/etc/passwd", "absolute paths and drives are not allowed"),
        ("\\\\server\\share\\x.txt", "absolute paths and drives are not allowed"),
        ("~/notes.txt", "absolute paths and drives are not allowed"),
        ("../escape.txt", "'..' is not allowed"),
        ("game/../../escape.txt", "'..' is not allowed"),
        ("CON", "not a usable file or folder name"),
        ("logs/nul.txt", "not a usable file or folder name"),
        ("COM1.js", "not a usable file or folder name"),
        ("notes.txt:hidden", "not a usable file or folder name"),
        ("trailing.", "not a usable file or folder name"),
        ("what?.txt", "not a usable file or folder name"),
        ("", "give a file path"),
        ("./", "give a file path"),
    ],
)
def test_workspace_refuses_paths_outside_it(tmp_path: Path, path: str, message: str) -> None:
    workspace = BenchmarkWorkspace(tmp_path / "run")
    with pytest.raises(WorkspaceError, match=message):
        workspace.write(path, "x")
    assert list(tmp_path.rglob("*")) == []


def test_workspace_refuses_links_that_lead_out(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = BenchmarkWorkspace(tmp_path / "run")
    workspace.folder.mkdir()
    link = workspace.folder / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs extra rights on this machine")

    with pytest.raises(WorkspaceError, match="passes through a link"):
        workspace.write("link/escape.txt", "x")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "game/../../outside.txt",
        "OUTSIDE",  # replaced by the absolute path below
        "C:/outside.txt",
        "\\\\server\\share\\outside.txt",
        "~/outside.txt",
        "",
        ".",
        "./",
    ],
)
def test_delete_never_reaches_outside_the_workspace(tmp_path: Path, path: str) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    workspace = BenchmarkWorkspace(tmp_path / "run")
    workspace.write("game/index.html", "<html>")
    target = str(outside) if path == "OUTSIDE" else path

    with pytest.raises(WorkspaceError):
        workspace.delete(target)
    assert outside.read_text(encoding="utf-8") == "keep me"
    assert (workspace.folder / "game" / "index.html").exists()
    assert workspace.folder.is_dir()


def test_delete_removes_only_files_and_tidies_empty_folders(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(tmp_path / "run")
    workspace.write("game/levels/one.txt", "1")
    workspace.write("game/index.html", "<html>")

    with pytest.raises(WorkspaceError, match="is a folder; only files can be deleted"):
        workspace.delete("game")
    with pytest.raises(WorkspaceError, match="does not exist"):
        workspace.delete("game/missing.txt")
    assert workspace.delete("game/levels/one.txt") == "Deleted game/levels/one.txt (1 bytes)"
    # The emptied folder goes; the one still holding a file stays.
    assert not (workspace.folder / "game" / "levels").exists()
    assert (workspace.folder / "game" / "index.html").exists()
    assert workspace.delete("game/index.html") == "Deleted game/index.html (6 bytes)"
    # The workspace itself is never removed, even when it ends up empty.
    assert workspace.folder.is_dir()
    assert workspace.files() == []


def test_delete_refuses_links_out_of_the_workspace(tmp_path: Path) -> None:
    outside_folder = tmp_path / "outside"
    outside_folder.mkdir()
    secret = outside_folder / "secret.txt"
    secret.write_text("keep me", encoding="utf-8")
    workspace = BenchmarkWorkspace(tmp_path / "run")
    workspace.folder.mkdir()
    linked = 0
    try:
        os.symlink(secret, workspace.folder / "file-link.txt")
        linked += 1
    except OSError:
        pass
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside_folder), str(workspace.folder / "junction"))
        linked += 1
    else:
        os.symlink(outside_folder, workspace.folder / "junction", target_is_directory=True)
        linked += 1

    if (workspace.folder / "file-link.txt").exists():
        with pytest.raises(WorkspaceError, match="link"):
            workspace.delete("file-link.txt")
    with pytest.raises(WorkspaceError, match="link"):
        workspace.delete("junction/secret.txt")
    assert linked >= 1
    assert secret.read_text(encoding="utf-8") == "keep me"


def test_workspace_writes_reads_and_lists_nested_files(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(tmp_path / "run")
    assert workspace.files() == []
    assert workspace.listing() == "Your workspace is empty."

    assert workspace.write("tetris\\index.html", "<html>") == "Wrote 6 bytes to tetris/index.html"
    assert workspace.write("./notes.md", "# ok") == "Wrote 4 bytes to notes.md"
    assert workspace.read("tetris/index.html") == "<html>"
    assert workspace.files() == [
        {"path": "notes.md", "size": 4},
        {"path": "tetris/index.html", "size": 6},
    ]
    assert workspace.listing() == "notes.md (4 bytes)\ntetris/index.html (6 bytes)"
    with pytest.raises(WorkspaceError, match="is a folder"):
        workspace.write("tetris", "x")
    with pytest.raises(WorkspaceError, match="does not exist"):
        workspace.read("missing.txt")
    with pytest.raises(WorkspaceError, match="give the file's content as text"):
        workspace.write("data.bin", 7)


def test_workspace_limits_file_size_count_and_total(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(
        tmp_path / "run", max_file_bytes=10, max_files=2, max_total_bytes=15
    )
    with pytest.raises(WorkspaceError, match="files are limited to 10"):
        workspace.write("big.txt", "x" * 11)
    workspace.write("a.txt", "x" * 8)
    with pytest.raises(WorkspaceError, match="limited to 15 bytes in total"):
        workspace.write("b.txt", "x" * 8)
    workspace.write("b.txt", "x" * 7)
    with pytest.raises(WorkspaceError, match="limited to 2 files"):
        workspace.write("c.txt", "x")
    # Replacing a file counts its new size, not both.
    workspace.write("a.txt", "x" * 8)
    (workspace.folder / "huge.txt").write_text("x" * 20, encoding="utf-8")
    with pytest.raises(WorkspaceError, match="too large to read"):
        workspace.read("huge.txt")


def test_run_folders_are_only_ever_run_ids_inside_the_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid benchmark run id"):
        run_folder(tmp_path, "../data")
    run_id = "00000000-0000-0000-0000-000000000001"
    remove_run_folder(tmp_path, run_id)  # Nothing there: nothing to do.
    (tmp_path / run_id).mkdir()
    (tmp_path / run_id / "file.txt").write_text("x", encoding="utf-8")
    remove_run_folder(tmp_path, run_id)
    assert not (tmp_path / run_id).exists()


@pytest.mark.asyncio
async def test_file_tools_write_only_inside_the_run_folder(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [
                _tool_call(0, "write_file", {"path": "tetris/index.html", "content": "<html>"}),
                _tool_call(1, "write_file", {"path": "C:/test.py", "content": "print(1)"}),
                _tool_call(2, "write_file", {"path": "../escape.txt", "content": "x"}),
                _tool_call(3, "list_files", {}),
            ],
            finish="tool_calls",
            generated=40,
            milliseconds=2000.0,
        ),
        _round(
            [_tool_call(0, "read_file", {"path": "tetris/index.html"})],
            finish="tool_calls",
            generated=10,
            milliseconds=500.0,
        ),
        _round(
            [{"choices": [{"delta": {"content": "Saved tetris/index.html."}}]}],
            finish="stop",
            generated=10,
            milliseconds=500.0,
        ),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    folder = runs.workspace_root / record.id
    assert finished.state == "completed", finished.error
    assert finished.tools is True
    assert [item["path"] for item in runs.workspace(record.id).files()] == ["tetris/index.html"]
    assert (folder / "tetris" / "index.html").read_text(encoding="utf-8") == "<html>"
    assert not (runs.workspace_root / "escape.txt").exists()
    calls = finished.tool_calls
    assert [(call["round"], call["name"], call["ok"]) for call in calls] == [
        (1, "write_file", True),
        (1, "write_file", False),
        (1, "write_file", False),
        (1, "list_files", True),
        (2, "read_file", True),
    ]
    assert "absolute paths and drives are not allowed" in str(calls[1]["result"])
    assert "'..' is not allowed" in str(calls[2]["result"])
    assert finished.output == "Saved tetris/index.html."
    stats = finished.stats
    assert (stats["rounds"], stats["tool_calls"], stats["files_written"]) == (3, 5, 1)
    assert stats["finish_reason"] == "stop"
    # Totals add up every request: 60 tokens in 3 s of decoding.
    assert stats["generated_tokens"] == 60
    assert stats["decode_tokens_per_second"] == 20.0
    assert stats["prompt_tokens"] == 30

    # Every request offered the tools, and later ones carried the results back.
    first_messages, first_tools = client.requests[0]
    assert first_tools == [
        "write_file",
        "edit_file",
        "read_file",
        "delete_file",
        "list_files",
        "list_tasks",
        "create_task",
        "complete_task",
        "delete_task",
        "finish_job",
    ]
    # It stopped with an answer but never said it was done.
    assert stats["outcome"] == "unfinished"
    assert first_messages[0]["role"] == "system"
    second_messages, _ = client.requests[1]
    assistant, *results = second_messages[2:]
    assert assistant["role"] == "assistant"
    assert len(cast(list[object], assistant["tool_calls"])) == 4
    assert [item["tool_call_id"] for item in results] == ["call-0", "call-1", "call-2", "call-3"]
    assert results[0]["content"] == "Wrote 6 bytes to tetris/index.html"
    assert results[3]["content"] == "tetris/index.html (6 bytes)"
    third_messages, _ = client.requests[2]
    # Files are read back with numbered lines, as coding agents show them.
    assert third_messages[-1]["content"] == "1: <html>"

    # The timeline breaks generation down step by step, round by round.
    timeline = finished.timeline
    assert [(entry["round"], entry["kind"]) for entry in timeline] == [
        (1, "reading"),
        (1, "tool_call"),
        (1, "tool"),
        (1, "tool"),
        (1, "tool"),
        (1, "tool"),
        (2, "reading"),
        (2, "tool_call"),
        (2, "tool"),
        (3, "reading"),
        (3, "answer"),
    ]
    assert all(entry["end"] is not None for entry in timeline)
    assert all(
        cast(float, entry["start"]) <= cast(float, entry["end"]) for entry in timeline
    )
    assert [entry["prompt_tokens"] for entry in timeline if entry["kind"] == "reading"] == [
        10,
        10,
        10,
    ]
    first_call = timeline[1]
    assert first_call["names"] == ["write_file", "write_file", "write_file", "list_files"]
    assert first_call["tokens"] == 40
    # Every call's arguments, in bytes, as they arrived.
    assert first_call["bytes"] == sum(
        len(json.dumps(arguments).encode("utf-8"))
        for arguments in (
            {"path": "tetris/index.html", "content": "<html>"},
            {"path": "C:/test.py", "content": "print(1)"},
            {"path": "../escape.txt", "content": "x"},
            {},
        )
    )
    tools = [entry for entry in timeline if entry["kind"] == "tool"]
    assert [(entry["call"], entry["name"], entry["ok"]) for entry in tools] == [
        (0, "write_file", True),
        (1, "write_file", False),
        (2, "write_file", False),
        (3, "list_files", True),
        (4, "read_file", True),
    ]
    assert tools[0]["path"] == "tetris/index.html"
    answer = timeline[-1]
    assert answer["tokens"] == 10
    output = finished.output
    assert output[cast(int, answer["from"]) : cast(int, answer["to"])] == output

    # Deleting the run deletes its files too.
    runs.delete(record.id)
    assert not folder.exists()


@pytest.mark.asyncio
async def test_a_model_that_keeps_calling_tools_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(benchmark_runner, "MAX_TOOL_ROUNDS", 2)
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    looping = _round(
        [_tool_call(0, "list_files", {})], finish="tool_calls", generated=5, milliseconds=100.0
    )
    client.replies = [looping, looping, looping]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256, tools=True)
    await runner.wait()

    assert runs.get(record.id).stats["finish_reason"] == "tool_round_limit"
    assert runs.get(record.id).stats["round_limit"] == 2
    assert len(client.requests) == 2

    # A benchmark can allow more rounds than the default.
    client.replies = [looping] * 4
    longer = replace(BENCHMARK, max_rounds=3)
    record = runner.start(profile, longer, 256, tools=True)
    await runner.wait()
    assert runs.get(record.id).stats["round_limit"] == 3
    assert len(client.requests) == 2 + 3


def test_frontmatter_sets_the_round_limit(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("---\nmax_rounds: 80\n---\nMake it.", encoding="utf-8")
    long = load_benchmarks(tmp_path)[0]
    assert long.max_rounds == 80
    assert long.to_payload()["max_rounds"] == 80
    for bad in ("0", "501", "true", "lots"):
        (tmp_path / "long.txt").write_text(
            f"---\nmax_rounds: {bad}\n---\nMake it.", encoding="utf-8"
        )
        with pytest.raises(BenchmarkError, match="max_rounds must be a whole number"):
            load_benchmarks(tmp_path)


@pytest.mark.asyncio
async def test_tool_calls_stop_when_the_token_budget_is_spent(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_tool_call(0, "list_files", {})], finish="tool_calls", generated=300, milliseconds=1.0
        )
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256, tools=True)
    await runner.wait()

    assert runs.get(record.id).stats["finish_reason"] == "length"
    assert len(client.requests) == 1


def test_failure_reasons_drop_echoed_input_and_stay_short() -> None:
    echoed = (
        "Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] parse "
        "error at line 1, column 35440: syntax error while parsing value - invalid string: "
        "missing closing quote; last read: '\"<!DOCTYPE html>" + "x" * 35000
    )
    assert benchmark_runner._short_error(echoed) == (
        "Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] parse "
        "error at line 1, column 35440: syntax error while parsing value - invalid string: "
        "missing closing quote"
    )
    long = benchmark_runner._short_error("y" * 1000)
    assert len(long) == benchmark_runner.MAX_ERROR_LENGTH
    assert long.endswith("…")
    assert benchmark_runner._short_error("model not found") == "model not found"


def _raw_tool_call(index: int, name: str, arguments: str) -> dict[str, object]:
    call = _tool_call(index, name, {})
    choice = cast(list[dict[str, dict[str, list[dict[str, dict[str, str]]]]]], call["choices"])[0]
    choice["delta"]["tool_calls"][0]["function"]["arguments"] = arguments
    return call


@pytest.mark.asyncio
async def test_a_call_cut_off_by_a_full_context_is_not_run(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_raw_tool_call(0, "write_file", '{"path": "index.html", "content": "<html>\n<he')],
            finish="length",
            generated=11727,
            milliseconds=1000.0,
        ),
        # Never requested: there is no room left for the model to act on anything.
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert len(client.requests) == 1
    assert (finished.stats["finish_reason"], finished.stats["outcome"]) == ("context", "unfinished")
    assert finished.tool_calls == [
        {
            "round": 1,
            "name": "write_file",
            "path": None,
            "ok": False,
            "result": "Not run: generation was cut off before the call was finished.",
        }
    ]
    assert runs.workspace(record.id).files() == []


@pytest.mark.asyncio
async def test_a_spent_token_limit_cutting_off_a_call_reports_length(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_raw_tool_call(0, "write_file", '{"path": "a')],
            finish="length",
            generated=10,
            milliseconds=10.0,
        )
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 1000, tools=True)
    await runner.wait()

    assert runs.get(record.id).stats["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_a_malformed_call_is_refused_and_the_model_can_retry(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_raw_tool_call(0, "write_file", '{"path": "index.html", "content": "<html>')],
            finish="tool_calls",
            generated=20,
            milliseconds=100.0,
        ),
        _round(
            [_tool_call(0, "write_file", {"path": "index.html", "content": "<html>"})],
            finish="tool_calls",
            generated=20,
            milliseconds=100.0,
        ),
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert [call["ok"] for call in finished.tool_calls] == [False, True]
    # The server rejects a history holding arguments that are not JSON, so the broken
    # call goes back as an empty object, with its error as the result.
    retry_messages, _ = client.requests[1]
    assistant = retry_messages[-2]
    assert cast(list[dict[str, dict[str, str]]], assistant["tool_calls"])[0]["function"][
        "arguments"
    ] == "{}"
    assert retry_messages[-1]["content"] == "Error: the tool arguments were not a JSON object."


@pytest.mark.asyncio
async def test_calling_finish_job_ends_the_run_as_completed(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [
                _tool_call(0, "write_file", {"path": "index.html", "content": "<html>"}),
                _tool_call(1, "finish_job", {"final_message": "Wrote the game."}),
            ],
            finish="tool_calls",
            generated=20,
            milliseconds=100.0,
        ),
        # Never requested: the run is over once the agent says it is done.
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert len(client.requests) == 1
    assert finished.stats["outcome"] == "completed"
    assert finished.stats["finish_reason"] == "finish_job"
    assert finished.tool_calls[-1]["result"] == "Job marked finished. Wrote the game."
    results = cast(dict[str, dict[str, object]], profile_results(engine, profile.id))
    assert results["tetris"]["outcome"] == "completed"


@pytest.mark.asyncio
async def test_finish_job_needs_the_final_message_and_keeps_it_whole(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    final = "Wrote index.html. " + "I had every tool I needed. " * 20
    client.replies = [
        _round(
            [_tool_call(0, "finish_job", {"final_message": "  "})],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round(
            [_tool_call(0, "finish_job", {"final_message": final})],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    refused, accepted = finished.tool_calls
    assert refused["ok"] is False
    assert str(refused["result"]).startswith("Error: final_message is required.")
    assert "what other tools would have helped" in str(refused["result"])
    assert accepted["result"] == f"Job marked finished. {final.strip()}"
    assert finished.stats["outcome"] == "completed"


# Every tool a run gets by default while ESLint is not installed.
UNLINTED = [
    name
    for name in TOOL_NAMES
    if name not in ("check_file", "run_tests") and not name.endswith("_typescript")
]


def test_the_workspace_note_names_only_the_tools_a_run_was_given() -> None:
    every = tool_system_prompt()
    assert "Use write_file to save files you create, edit_file to change part" in every
    assert "list_files to see what is there, check_file to lint" in every
    assert "complete_task to mark a task done and delete_task to drop" in every
    assert "call finish_job" in every

    assert tool_system_prompt(["read_file"]) == (
        "You have a workspace folder for this task. Use read_file to read a file back. "
        "Paths are relative to the workspace, like 'tetris/index.html'. You cannot read or "
        "write anything outside it."
    )
    only_done = tool_system_prompt(["finish_job"])
    assert "Use " not in only_done
    assert only_done.endswith("counts as unfinished.")


def test_tools_are_chosen_by_name_in_offer_order() -> None:
    plain = [
        name for name in TOOL_NAMES if not name.endswith("_typescript") and name != "run_tests"
    ]
    assert choose_tools(None) == plain
    typed = [name for name in TOOL_NAMES if name != "run_tests"]
    assert choose_tools(None, typescript=True) == typed
    assert choose_tools(None, typescript=True, tests=True) == list(TOOL_NAMES)
    # Tests are only for a TypeScript benchmark that turns them on.
    assert choose_tools(None, tests=True) == plain
    with pytest.raises(ValueError, match="run_tests only works on a benchmark with tests"):
        choose_tools(["run_tests"], typescript=True)
    assert choose_tools(["run_tests"], typescript=True, tests=True) == ["run_tests"]
    with pytest.raises(ValueError, match="TypeScript tools are not installed"):
        choose_tools(None, typed, typescript=True, tests=True)
    assert choose_tools(["list_files", "write_file", "write_file"]) == ["write_file", "list_files"]
    assert choose_tools([]) == []
    with pytest.raises(ValueError, match="unknown tools: run_shell"):
        choose_tools(["write_file", "run_shell"])


@pytest.mark.asyncio
async def test_a_run_offers_only_its_chosen_tools_and_refuses_the_rest(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [
                _tool_call(0, "write_file", {"path": "index.html", "content": "<html>"}),
                _tool_call(1, "delete_file", {"path": "index.html"}),
                _tool_call(2, "finish_job", {"final_message": "Done."}),
            ],
            finish="tool_calls",
            generated=20,
            milliseconds=100.0,
        ),
        _round(
            [{"choices": [{"delta": {"content": "Done."}}]}],
            finish="stop",
            generated=5,
            milliseconds=50.0,
        ),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True, tool_names=["write_file"])
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert finished.tool_names == ["write_file"]
    messages, offered = client.requests[0]
    assert offered == ["write_file"]
    assert "finish_job" not in str(messages[0]["content"])
    assert [(call["name"], call["ok"]) for call in finished.tool_calls] == [
        ("write_file", True),
        ("delete_file", False),
        ("finish_job", False),
    ]
    assert finished.tool_calls[1]["result"] == "Error: there is no tool called 'delete_file'"
    assert (runs.workspace_root / record.id / "index.html").exists()
    # Without finish_job the agent cannot say it is done, so no outcome is kept.
    assert "outcome" not in finished.stats
    assert finished.stats["finish_reason"] == "stop"


PAGE = "<script>a=</script>"


@pytest.mark.asyncio
async def test_an_agent_lints_its_page_once_eslint_is_installed(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [
                _tool_call(0, "write_file", {"path": "index.html", "content": PAGE}),
                _tool_call(1, "check_file", {"path": "index.html"}),
                _tool_call(2, "check_file", {"path": "missing.js"}),
            ],
            finish="tool_calls",
            generated=20,
            milliseconds=100.0,
        ),
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)
    with pytest.raises(ValueError, match="check_file needs ESLint"):
        runner.start(profile, BENCHMARK, None, tools=True, tool_names=["check_file"])
    assert "check_file" not in runner.available_tools()

    messages = [{"line": 1, "column": 11, "severity": 2, "message": "Parsing error"}]
    commands = FakeCommands(eslint=CommandResult(1, json.dumps([{"messages": messages}]), ""))
    linter = BenchmarkLinter(
        tmp_path / "data",
        downloader=FakeDownloads({WIN_ZIP: _node_zip()}),
        runner=commands,
        platform="win32",
        machine="AMD64",
    )
    linter.start_install()
    await linter.wait()
    runner._linter = linter
    assert "check_file" in runner.available_tools()

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert "check_file" in cast(list[str], finished.tool_names)
    assert "check_file to lint" in str(client.requests[0][0][0]["content"])
    assert [(call["name"], call["ok"]) for call in finished.tool_calls] == [
        ("write_file", True),
        ("check_file", True),
        ("check_file", False),
    ]
    assert finished.tool_calls[1]["result"] == "index.html: 1 error\n1:11 error Parsing error"
    assert commands.calls[-1][3] == PAGE
    assert "does not exist" in str(finished.tool_calls[2]["result"])


def test_check_file_without_a_linter_is_refused(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(tmp_path)
    workspace.write("a.js", "x")
    result, entry = _call_tool(workspace, "check_file", json.dumps({"path": "a.js"}))
    assert (result, entry["ok"]) == ("Error: the linter is not installed", False)


@pytest.mark.asyncio
async def test_a_run_given_no_tools_runs_without_them(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256, tools=True, tool_names=[])
    await runner.wait()

    finished = runs.get(record.id)
    assert (finished.tools, finished.tool_names) == (False, None)
    assert client.requests[0][1] == []
    with pytest.raises(ValueError, match="unknown tools"):
        runner.start(profile, BENCHMARK, 256, tools=True, tool_names=["nope"])


@pytest.mark.asyncio
async def test_an_agent_writes_then_edits_a_file_by_line(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    page = "<p>score</p>\n<p>score</p>\n"
    client.replies = [
        _round(
            [_tool_call(0, "write_file", {"path": "index.html", "content": page})],
            finish="tool_calls",
            generated=10,
            milliseconds=10.0,
        ),
        _round(
            [
                _tool_call(
                    0, "edit_file", {"path": "index.html", "old_string": "score", "new_string": "x"}
                ),
                _tool_call(
                    1,
                    "edit_file",
                    {"path": "index.html", "old_string": "score", "new_string": "lines", "line": 2},
                ),
                _tool_call(2, "finish_job", {"final_message": "Done."}),
            ],
            finish="tool_calls",
            generated=10,
            milliseconds=10.0,
        ),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    edits = [call for call in finished.tool_calls if call["name"] == "edit_file"]
    # The first is ambiguous and refused, telling the model where the matches are.
    assert edits[0]["ok"] is False
    assert "starting on lines 1 and 2" in str(edits[0]["result"])
    assert edits[1]["ok"] is True
    content = (runs.workspace_root / record.id / "index.html").read_text(encoding="utf-8")
    assert content == "<p>score</p>\n<p>lines</p>\n"
    assert finished.stats["outcome"] == "completed"


@pytest.mark.asyncio
async def test_a_full_context_ends_the_run_as_unfinished(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_tool_call(0, "list_files", {})], finish="tool_calls", generated=5, milliseconds=10.0
        ),
        RouterAPIError(400, "the request exceeds the available context size, try increasing it"),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert finished.stats["finish_reason"] == "context"
    assert finished.stats["outcome"] == "unfinished"
    assert finished.stats["rounds"] == 2
    # Without a limit, no max_tokens was sent at all.
    assert all(call.endswith(":None") for call in client.calls if call.startswith("prompt:"))


@pytest.mark.asyncio
async def test_other_router_errors_still_fail_the_run(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [RouterAPIError(500, "the model crashed")]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "failed"
    assert finished.error == "the model crashed"
    assert finished.stats["outcome"] == "unfinished"


@pytest.mark.asyncio
async def test_a_tool_call_being_written_shows_its_size_live(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    arguments = json.dumps({"path": "notes.md", "content": "héllo wörld"})
    pieces = [arguments[:10], arguments[10:25], arguments[25:]]
    client.replies = [
        _round(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-0" if position == 0 else None,
                                        "function": {
                                            "name": "write_file" if position == 0 else None,
                                            "arguments": piece,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
                for position, piece in enumerate(pieces)
            ],
            finish="tool_calls",
            generated=12,
            milliseconds=100.0,
        )
    ]
    runner, runs, _ = _runner(engine, client)
    seen: list[object] = []
    client.on_chunk = lambda: seen.append(
        (runner.progress(runner.active_run_id or "") or {}).get("tool_call")
    )

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    sizes = [len(piece.encode("utf-8")) for piece in pieces]
    assert seen[:3] == [
        {"name": "write_file", "bytes": sizes[0]},
        {"name": "write_file", "bytes": sizes[0] + sizes[1]},
        {"name": "write_file", "bytes": sum(sizes)},
    ]
    timeline = runs.get(record.id).timeline
    assert next(entry for entry in timeline if entry["kind"] == "tool_call")["bytes"] == sum(sizes)


@pytest.mark.asyncio
async def test_a_run_without_tools_offers_none_and_leaves_no_folder(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    messages, tools = client.requests[0]
    assert tools == []
    assert [item["role"] for item in messages] == ["user"]
    assert runs.get(record.id).tool_calls == []
    assert not (runs.workspace_root / record.id).exists()


def test_bad_tool_arguments_are_reported_to_the_model(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(tmp_path / "run")
    reply, entry = _call_tool(workspace, "write_file", "{not json")
    assert reply == "Error: the tool arguments were not a JSON object."
    assert entry["ok"] is False
    reply, _ = _call_tool(workspace, "delete_everything", "{}")
    assert reply == "Error: there is no tool called 'delete_everything'"
    reply, _ = _call_tool(workspace, "read_file", json.dumps({"path": "missing.txt"}))
    assert "does not exist" in reply
    workspace.write("big.txt", "x" * 1000)
    reply, entry = _call_tool(workspace, "read_file", json.dumps({"path": "big.txt"}))
    assert reply == "1: " + "x" * 1000
    assert len(str(entry["result"])) == 301
    reply, entry = _call_tool(
        workspace,
        "edit_file",
        json.dumps({"path": "big.txt", "old_string": "x" * 1000, "new_string": "y"}),
    )
    assert reply == "Edited big.txt (1 replacement; now 1 bytes)"
    assert entry["ok"] is True
    assert entry["path"] == "big.txt"
    reply, entry = _call_tool(workspace, "delete_file", json.dumps({"path": "big.txt"}))
    assert reply == "Deleted big.txt (1 bytes)"
    assert (entry["ok"], entry["path"]) == (True, "big.txt")
    reply, entry = _call_tool(workspace, "delete_file", json.dumps({"path": "../app.db"}))
    assert "'..' is not allowed" in reply
    assert entry["ok"] is False
    assert "arguments" not in entry


def test_a_verbose_log_keeps_the_arguments_and_the_whole_result(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(tmp_path / "run")
    workspace.write("big.txt", "x" * 1000)
    sent = json.dumps({"path": "big.txt", "old_string": "y\t", "new_string": "z"})
    reply, entry = _call_tool(workspace, "edit_file", sent, verbose=True)
    assert reply.startswith("Error: Could not find old_string")
    assert entry["arguments"] == sent
    reply, entry = _call_tool(
        workspace, "read_file", json.dumps({"path": "big.txt"}), verbose=True
    )
    assert entry["result"] == reply == "1: " + "x" * 1000
    # Arguments that are not JSON are kept as sent, since they are why the call failed.
    _, entry = _call_tool(workspace, "write_file", '{"path": "a', verbose=True)
    assert entry["arguments"] == '{"path": "a'


@pytest.mark.asyncio
async def test_a_run_keeps_a_verbose_log_only_when_asked(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    write = {"path": "index.html", "content": "<html>"}
    client.replies = [
        _round([_tool_call(0, "write_file", write)], finish="tool_calls", generated=5,
               milliseconds=10.0),
        _round([], finish="stop", generated=1, milliseconds=1.0),
        _round([_tool_call(0, "write_file", write)], finish="tool_calls", generated=5,
               milliseconds=10.0),
        _round([], finish="stop", generated=1, milliseconds=1.0),
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)

    verbose = runner.start(profile, BENCHMARK, None, tools=True, verbose_log=True)
    await runner.wait()
    plain = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()
    # Only tool calls are logged, so a run without tools does not keep the setting.
    untooled = runner.start(profile, BENCHMARK, None, tools=False, verbose_log=True)
    await runner.wait()

    assert runs.get(verbose.id).verbose_log is True
    assert runs.get(verbose.id).tool_calls[0]["arguments"] == json.dumps(write)
    assert runs.get(plain.id).verbose_log is False
    assert "arguments" not in runs.get(plain.id).tool_calls[0]
    assert runs.get(untooled.id).verbose_log is False


def test_startup_prunes_folders_of_runs_that_no_longer_exist(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    runs = BenchmarkRunRegistry(engine, tmp_path / "benchmark-runs")
    kept = runs.create(profile, BENCHMARK, 256, tools=True)
    runs.workspace(kept.id).write("keep.txt", "x")
    orphan = runs.workspace_root / "00000000-0000-0000-0000-000000000000"
    orphan.mkdir()
    (runs.workspace_root / "notes").mkdir()

    assert runs.prune_folders() == 1
    assert (runs.workspace_root / kept.id / "keep.txt").exists()
    assert not orphan.exists()
    assert (runs.workspace_root / "notes").exists()
    assert BenchmarkRunRegistry(engine, tmp_path / "missing").prune_folders() == 0


def test_system_prompts_are_bundled_files(tmp_path: Path) -> None:
    bundled = {item.id: item for item in load_system_prompts()}
    assert bundled["coding-agent"].title == "Coding agent"
    assert "finish_job" in bundled["coding-agent"].text
    assert load_system_prompts(tmp_path / "missing") == ()
    (tmp_path / "terse.txt").write_text(" Answer briefly. \n", encoding="utf-8")
    assert load_system_prompts(tmp_path) == (
        SystemPrompt(id="terse", title="Terse", text="Answer briefly."),
    )
    (tmp_path / "blank.txt").write_text("", encoding="utf-8")
    with pytest.raises(BenchmarkError, match="blank.txt: system prompt is empty"):
        load_system_prompts(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("system", "tools", "expected"),
    [
        (True, True, ["agent framing", tool_system_prompt(UNLINTED)]),
        (True, False, ["agent framing"]),
        (False, True, [tool_system_prompt(UNLINTED)]),
        (False, False, None),
    ],
)
async def test_the_system_message_combines_the_chosen_prompt_and_the_tool_note(
    tmp_path: Path, system: bool, tools: bool, expected: list[str] | None
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, _ = _runner(engine, client)
    prompt = SystemPrompt(id="agent", title="Agent", text="agent framing")

    record = runner.start(
        profile, BENCHMARK, 256, tools=tools, system_prompt=prompt if system else None
    )
    await runner.wait()

    messages, _ = client.requests[0]
    if expected is None:
        assert [item["role"] for item in messages] == ["user"]
    else:
        assert messages[0] == {"role": "system", "content": "\n\n".join(expected)}
    finished = runs.get(record.id)
    assert finished.system_prompt_id == ("agent" if system else None)
    assert finished.system_prompt == ("agent framing" if system else None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("system", "extra", "expected"),
    [
        (True, " Reasoning strength: high\n", ["agent framing", "Reasoning strength: high"]),
        (False, "Reasoning strength: high", ["Reasoning strength: high"]),
        (True, "  \n", ["agent framing"]),
    ],
)
async def test_added_text_follows_the_chosen_prompt_before_the_tool_note(
    tmp_path: Path, system: bool, extra: str, expected: list[str]
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, _ = _runner(engine, client)
    prompt = SystemPrompt(id="agent", title="Agent", text="agent framing")

    record = runner.start(
        profile,
        BENCHMARK,
        256,
        tools=True,
        system_prompt=prompt if system else None,
        system_prompt_extra=extra,
    )
    await runner.wait()

    messages, _ = client.requests[0]
    assert messages[0] == {
        "role": "system",
        "content": "\n\n".join([*expected, tool_system_prompt(UNLINTED)]),
    }
    # Kept trimmed, and not at all when it was blank.
    assert runs.get(record.id).system_prompt_extra == (extra.strip() or None)


@pytest.mark.parametrize(
    ("level", "options"),
    [
        (None, {}),
        ("off", {"chat_template_kwargs": {"enable_thinking": False}}),
        ("low", {"reasoning_effort": "low"}),
        ("xhigh", {"reasoning_effort": "xhigh"}),
    ],
)
def test_reasoning_levels_become_request_fields(
    level: str | None, options: dict[str, object]
) -> None:
    assert benchmark_runner.reasoning_options(level) == options


@pytest.mark.asyncio
async def test_a_run_asks_for_its_reasoning_level_and_records_it(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, _ = _runner(engine, client)

    with pytest.raises(ValueError, match="unknown reasoning level: extreme"):
        runner.start(profile, BENCHMARK, 256, reasoning_level="extreme")
    record = runner.start(profile, BENCHMARK, 256, reasoning_level="low")
    await runner.wait()

    assert client.options == [{"reasoning_effort": "low"}]
    finished = runs.get(record.id)
    assert finished.reasoning_level == "low"
    results = cast(dict[str, dict[str, object]], profile_results(engine, profile.id))
    assert results["tetris"]["reasoning_level"] == "low"


@pytest.mark.asyncio
async def test_a_call_repeated_in_one_reply_runs_once(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    read = {"path": "a.txt"}
    client.replies = [
        _round(
            [_tool_call(0, "write_file", {"path": "a.txt", "content": "one"})],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round(
            [
                _tool_call(0, "read_file", read),
                _tool_call(1, "read_file", {"path": "a.txt"}),
                _raw_tool_call(2, "read_file", '{ "path":"a.txt" }'),
                _tool_call(3, "list_files", {}),
                _tool_call(
                    4, "edit_file", {"path": "a.txt", "old_string": "one", "new_string": "two"}
                ),
                # After the edit the file reads differently, so this read runs again.
                _tool_call(5, "read_file", read),
                _tool_call(6, "read_file", read),
            ],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    calls = [call for call in finished.tool_calls if call["round"] == 2]
    assert [call["ok"] for call in calls] == [True, False, False, True, True, True, False]
    assert calls[1] == {
        "round": 2,
        "name": "read_file",
        "path": "a.txt",
        "ok": False,
        "result": "Error: duplicate of call #1 in this reply, which was run once; use its "
        "result instead of calling it again.",
    }
    assert "call #6" in cast(str, calls[6]["result"])
    assert "two" in cast(str, calls[5]["result"])
    # Every call still gets its own result, so the history stays valid.
    history, _ = client.requests[2]
    results = [message for message in history if message["role"] == "tool"]
    assert len(results) == 1 + 7
    assert cast(str, results[2]["content"]).startswith("Error: duplicate of call #1")
