from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from test_benchmark_tools import _tool_call, profile_results
from test_benchmarks import BENCHMARK, FakeBenchmarkClient, _database, _runner

from llamawebui.services import benchmark_compaction as compaction
from llamawebui.services.router_client import RouterAPIError

FILE = "x" * 8000


def _reply(
    parts: list[dict[str, object]],
    *,
    prompt: int,
    generated: int,
    finish: str = "tool_calls",
) -> list[dict[str, object]]:
    """A reply whose closing chunk reports the whole prompt, as cache_n plus prompt_n."""
    timings = {
        "cache_n": 0,
        "prompt_n": prompt,
        "prompt_ms": 10.0,
        "predicted_n": generated,
        "predicted_ms": 100.0,
    }
    return [*parts, {"choices": [{"delta": {}, "finish_reason": finish}], "timings": timings}]


def _text(content: str, *, generated: int = 50) -> list[dict[str, object]]:
    return _reply(
        [{"choices": [{"delta": {"content": content}}]}],
        prompt=100,
        generated=generated,
        finish="stop",
    )


def _write(path: str, index: int = 0) -> dict[str, object]:
    return _tool_call(index, "write_file", {"path": path, "content": FILE})


def _finish() -> dict[str, object]:
    return _tool_call(0, "finish_job", {"final_message": "Done."})


def _history(rounds: int) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "tools"},
        {"role": "user", "content": "make tetris"},
    ]
    for number in range(1, rounds + 1):
        arguments = json.dumps({"path": f"f{number}.js", "content": FILE})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"w{number}",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": arguments},
                    },
                    {
                        "id": f"r{number}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.js"}'},
                    },
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"w{number}", "content": "Wrote it."})
        messages.append({"role": "tool", "tool_call_id": f"r{number}", "content": "1: " + FILE})
    return messages


def test_pruning_replaces_old_payloads_and_keeps_the_latest_rounds() -> None:
    history = _history(4)

    pruned = compaction.prune(history)

    assert len(pruned) == len(history)
    assert [message.get("tool_call_id") for message in pruned] == [
        message.get("tool_call_id") for message in history
    ]
    old = cast(list[dict[str, dict[str, str]]], pruned[2]["tool_calls"])
    arguments = json.loads(old[0]["function"]["arguments"])
    assert arguments["path"] == "f1.js"
    assert arguments["content"] == (
        "[8,000 bytes written to the workspace; read_file shows the file as it is now]"
    )
    assert old[0]["id"] == "w1"
    assert pruned[4]["content"] == (
        "[8,003 bytes of read_file a.js output cleared to save context; call it again if you "
        "need it]"
    )
    # Short results stay, and the two latest rounds are left whole.
    assert pruned[3]["content"] == "Wrote it."
    assert pruned[8:] == history[8:]
    assert compaction.history_characters(pruned) < compaction.history_characters(history) - 30000
    # The input is left as it was.
    assert "x" * 8000 in cast(list[dict[str, dict[str, str]]], history[2]["tool_calls"])[0][
        "function"
    ]["arguments"]


def test_pruning_leaves_arguments_that_are_not_json_alone() -> None:
    history = _history(3)
    calls = cast(list[dict[str, dict[str, str]]], history[2]["tool_calls"])
    calls[0]["function"]["arguments"] = "not json " + FILE

    pruned = compaction.prune(history)

    pruned_calls = cast(list[dict[str, dict[str, str]]], pruned[2]["tool_calls"])
    assert pruned_calls[0]["function"]["arguments"] == "not json " + FILE


def test_a_summary_request_drops_the_oldest_rounds_until_it_fits() -> None:
    history = _history(4)
    assert compaction.summary_request(history[:2], 10**6) is None

    whole = compaction.summary_request(history, 10**6)
    assert whole is not None
    assert whole[:-1] == history
    assert whole[-1] == {"role": "user", "content": compaction.SUMMARY_REQUEST}

    fitted = compaction.summary_request(history, 20000)
    assert fitted is not None
    assert fitted[:2] == history[:2]
    assert "were left out" in cast(str, fitted[2]["content"])
    assert fitted[3:-1] == history[-3:]


def test_a_compacted_history_carries_the_task_summary_and_files() -> None:
    history = _history(2)

    listing = "tetris/index.html (9 bytes)"
    result = compaction.compacted(history, " Built the board. ", listing, True)

    assert result[:2] == history[:2]
    assert result[2] == {
        "role": "assistant",
        "content": compaction.SUMMARY_LEAD + "Built the board.",
    }
    note = cast(str, result[3]["content"])
    assert result[3]["role"] == "user"
    assert "list_tasks" in note
    assert "tetris/index.html (9 bytes)" in note
    assert note.endswith("Continue with the next steps.")
    # A later summary keeps the same head, not the previous summary.
    again = compaction.compacted([*result, *history[2:]], "More.", None, False)
    assert again[:2] == history[:2]
    assert "list_tasks" not in cast(str, again[3]["content"])


def test_context_size_and_reserve() -> None:
    assert (
        compaction.context_size_from_error(
            "request (20001 tokens) exceeds the available context size (16384 tokens), try "
            "increasing it"
        )
        == 16384
    )
    assert compaction.context_size_from_error("exceeds the context") is None
    assert compaction.reserve_tokens(65536, 13000) == 13000
    assert compaction.reserve_tokens(65536, 100) == 8192
    assert compaction.reserve_tokens(16000, 100) == 4000
    assert compaction.reserve_tokens(16000, 12000) == 8000


@pytest.mark.asyncio
async def test_a_run_prunes_its_history_before_the_context_fills(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    # Each write adds about 2,600 tokens to a 16,000-token context.
    client.replies = [
        _reply([_write(f"f{number}.js")], prompt=1000 + 2600 * (number - 1), generated=2500)
        for number in range(1, 6)
    ]
    client.replies += [_reply([_finish()], prompt=6500, generated=20)]
    runner, runs, _ = _runner(engine, client, context=lambda alias: (1000, 16000))

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert finished.stats["outcome"] == "completed"
    assert finished.stats["compactions"] == 1
    assert finished.stats["compactions_pruned"] == 1
    assert "compactions_summarised" not in finished.stats
    assert finished.compact_context is True
    # No summary was asked for: every request offered the tools.
    assert len(client.requests) == 6
    assert all(names for _, names in client.requests)
    messages, _ = client.requests[5]
    first = cast(list[dict[str, dict[str, str]]], messages[2]["tool_calls"])[0]
    assert "bytes written to the workspace" in first["function"]["arguments"]
    latest = cast(list[dict[str, dict[str, str]]], messages[-2]["tool_calls"])[0]
    assert FILE in latest["function"]["arguments"]
    entry = next(item for item in finished.timeline if item["kind"] == "compacting")
    assert entry["round"] == 6
    assert entry["mode"] == "pruned"
    assert cast(int, entry["tokens_before"]) > 12000
    # The size after is what the next request really sent.
    assert entry["tokens_after"] == 6500
    results = cast(dict[str, dict[str, object]], profile_results(engine, profile.id))
    assert results["tetris"]["compactions"] == 1


@pytest.mark.asyncio
async def test_a_refused_request_is_summarised_and_retried(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _reply([_write("tetris/index.html")], prompt=1000, generated=2500),
        _reply([_write("tetris/game.js")], prompt=3600, generated=2500),
        RouterAPIError(
            400,
            "request (17000 tokens) exceeds the available context size (16384 tokens), try "
            "increasing it",
        ),
        _text("The board and pieces are done; scoring is left.", generated=300),
        _reply([_finish()], prompt=900, generated=20),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True, reasoning_level="high")
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert finished.stats["outcome"] == "completed"
    assert finished.stats["finish_reason"] == "finish_job"
    assert (finished.stats["compactions"], finished.stats["compactions_summarised"]) == (1, 1)
    summary_messages, summary_tools = client.requests[3]
    assert summary_tools == []
    assert summary_messages[-1] == {"role": "user", "content": compaction.SUMMARY_REQUEST}
    assert client.options[3] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert client.options[4] == {"reasoning_effort": "high"}
    retried, tools = client.requests[4]
    assert tools
    assert [message["role"] for message in retried] == ["system", "user", "assistant", "user"]
    assert retried[1]["content"] == "make tetris"
    assert "scoring is left" in cast(str, retried[2]["content"])
    assert "tetris/game.js (8,000 bytes)" in cast(str, retried[3]["content"])
    entry = next(item for item in finished.timeline if item["kind"] == "compacting")
    assert (entry["round"], entry["mode"], entry["tokens"]) == (3, "summarised", 300)
    # Summary tokens stay out of the run's generation figures.
    assert finished.stats["generated_tokens"] == 2500 + 2500 + 20


@pytest.mark.asyncio
async def test_a_call_cut_off_by_a_full_context_is_retried_after_compacting(
    tmp_path: Path,
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    cut = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "cut",
                            "function": {"name": "write_file", "arguments": '{"path": "a'},
                        }
                    ]
                }
            }
        ]
    }
    client.replies = [
        _reply([_write("a.js")], prompt=1000, generated=2500),
        _reply([_write("b.js")], prompt=3600, generated=2500),
        _reply([_write("c.js")], prompt=6200, generated=2500),
        _reply([cut], prompt=8800, generated=3600, finish="length"),
        _reply([_finish()], prompt=4000, generated=20),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    assert finished.stats["outcome"] == "completed"
    assert finished.stats["compactions_pruned"] == 1
    not_run = [call for call in finished.tool_calls if call["round"] == 4]
    assert not_run[0]["ok"] is False
    assert "compacted and the round tried again" in cast(str, not_run[0]["result"])
    assert len(client.requests) == 5


@pytest.mark.asyncio
async def test_a_run_gives_up_when_compaction_cannot_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    refused = RouterAPIError(
        400, "request (17000 tokens) exceeds the available context size (16384 tokens)"
    )
    client.replies = [
        _reply([_write("a.js")], prompt=1000, generated=2500),
        refused,
        # The summary comes back empty, so there is nothing to carry on from.
        _text(""),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert (finished.stats["finish_reason"], finished.stats["outcome"]) == (
        "context",
        "unfinished",
    )
    assert finished.stats["compactions"] == 0
    entry = next(item for item in finished.timeline if item["kind"] == "compacting")
    assert entry["failed"] is True

    # And a run that has used up its compactions ends at the next full context.
    monkeypatch.setattr(compaction, "MAX_COMPACTIONS", 0)
    client.replies = [_reply([_write("a.js")], prompt=1000, generated=2500), refused]
    record = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()
    assert runs.get(record.id).stats["finish_reason"] == "context"
    assert len(client.requests) == 5


@pytest.mark.asyncio
async def test_a_run_with_compaction_off_ends_when_the_context_fills(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _reply([_write("a.js")], prompt=1000, generated=2500),
        RouterAPIError(
            400, "request (17000 tokens) exceeds the available context size (16384 tokens)"
        ),
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, None, tools=True, compact_context=False)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.compact_context is False
    assert finished.stats["finish_reason"] == "context"
    assert "compactions" not in finished.stats
    assert len(client.requests) == 2
