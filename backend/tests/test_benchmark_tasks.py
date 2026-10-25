from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_benchmark_tools import _round, _tool_call
from test_benchmarks import BENCHMARK, FakeBenchmarkClient, _database, _runner

from llamawebui.domain.benchmark import BenchmarkError, load_benchmarks
from llamawebui.services import benchmark_tasks
from llamawebui.services.benchmark_runner import choose_tools, tool_system_prompt
from llamawebui.services.benchmark_tasks import TaskError


def test_frontmatter_seeds_tasks_and_is_left_out_of_the_prompt(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text("---\nnot frontmatter, just a rule\n", encoding="utf-8")
    (tmp_path / "planned.txt").write_text(
        "---\n# Comments are fine.\ntasks:\n  - Build the board\n  - >-\n    Add the\n    pieces\n"
        "---\n\nMake tetris.\n",
        encoding="utf-8",
    )
    (tmp_path / "blank.txt").write_text("---\n---\nJust the prompt.", encoding="utf-8")
    loaded = {item.id: item for item in load_benchmarks(tmp_path)}
    assert loaded["planned"].prompt == "Make tetris."
    assert loaded["planned"].tasks == ("Build the board", "Add the pieces")
    assert loaded["planned"].to_payload()["tasks"] == ["Build the board", "Add the pieces"]
    assert loaded["blank"].prompt == "Just the prompt."
    assert loaded["blank"].tasks == ()
    # Without a closing line there is no frontmatter: the whole file is the prompt.
    assert loaded["plain"].prompt == "---\nnot frontmatter, just a rule"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\ntasks: [\n---\nx", "invalid frontmatter"),
        ("---\n- a list\n---\nx", "frontmatter must be a mapping"),
        ("---\ntitle: Tetris\ntask: [a]\n---\nx", "unknown frontmatter settings: task, title"),
        ("---\ntasks: one task\n---\nx", "tasks must be a list"),
        ("---\ntasks: ['', b]\n---\nx", "tasks must be a list"),
        ("---\ntasks: [a]\n---\n\n", "benchmark prompt is empty"),
    ],
)
def test_bad_frontmatter_is_refused(tmp_path: Path, text: str, message: str) -> None:
    (tmp_path / "bad.txt").write_text(text, encoding="utf-8")
    with pytest.raises(BenchmarkError, match=f"bad.txt: {message}"):
        load_benchmarks(tmp_path)


def test_the_task_list_tools_keep_deleted_tasks_on_the_record() -> None:
    tasks = benchmark_tasks.seed_tasks(["Board", "Pieces"])
    assert benchmark_tasks.create_task(tasks, "  Add   sound ") == "Created task #3: Add sound"
    assert benchmark_tasks.complete_task(tasks, "#1") == "Completed task #1. 2 still open."
    assert benchmark_tasks.complete_task(tasks, 1) == "Task #1 was already done."
    assert benchmark_tasks.delete_task(tasks, "3") == "Deleted task #3."
    assert benchmark_tasks.delete_task(tasks, 3) == "Task #3 was already deleted."
    assert benchmark_tasks.list_tasks(tasks) == "[x] #1 Board\n[ ] #2 Pieces\n1 of 2 done."
    assert tasks[2] == {"id": 3, "title": "Add sound", "status": "deleted", "source": "agent"}
    assert benchmark_tasks.unfinished_reason(tasks) is not None
    assert benchmark_tasks.complete_task(tasks, 2) == "Completed task #2. Every task is done."
    assert benchmark_tasks.unfinished_reason(tasks) is None
    assert benchmark_tasks.list_tasks([]) == "There are no tasks. Use create_task to plan the work."

    for call, message in (
        (lambda: benchmark_tasks.create_task(tasks, " "), "title is required"),
        (lambda: benchmark_tasks.create_task(tasks, "x" * 501), "longer than 500"),
        (lambda: benchmark_tasks.complete_task(tasks, True), "id must be a task number"),
        (lambda: benchmark_tasks.complete_task(tasks, None), "id must be a task number"),
        (lambda: benchmark_tasks.complete_task(tasks, 9), "there is no task #9"),
        (lambda: benchmark_tasks.complete_task(tasks, 3), "task #3 was deleted"),
        (lambda: benchmark_tasks.delete_task(tasks, 1), "only open tasks can be deleted"),
    ):
        with pytest.raises(TaskError, match=message):
            call()


def test_the_old_name_for_finish_job_is_still_accepted() -> None:
    assert choose_tools(["task_complete", "write_file"]) == ["write_file", "finish_job"]
    assert "refused while any task" in tool_system_prompt(["complete_task", "finish_job"])
    assert "refused while any task" not in tool_system_prompt(["finish_job"])
    # Seeded tasks are stated as a fact, but only to an agent that can read them.
    assert "already holds 1 task prepared" in tool_system_prompt(["list_tasks"], 1)
    assert "already holds" not in tool_system_prompt(["list_tasks"])
    assert "already holds" not in tool_system_prompt(["finish_job"], 3)


@pytest.mark.asyncio
async def test_finish_job_waits_for_the_seeded_tasks(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [
                _tool_call(0, "list_tasks", {}),
                _tool_call(1, "create_task", {"title": "Add sound"}),
            ],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round(
            [
                _tool_call(0, "complete_task", {"id": 1}),
                _tool_call(1, "finish_job", {"final_message": "Built the board."}),
            ],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round(
            [
                _tool_call(0, "delete_task", {"id": "#3"}),
                _tool_call(1, "complete_task", {"id": 2}),
                _tool_call(2, "finish_job", {"final_message": "Built it all."}),
            ],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
    ]
    runner, runs, _ = _runner(engine, client)
    planned = replace(BENCHMARK, tasks=("Draw the board", "Add the pieces"))

    record = runner.start(profile, planned, None, tools=True)
    assert [task["status"] for task in runs.get(record.id).tasks] == ["open", "open"]
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.state == "completed", finished.error
    messages, _ = client.requests[0]
    assert "Your task list already holds 2 tasks prepared for this job." in str(
        messages[0]["content"]
    )
    listing, created, _, refused, deleted, _, accepted = finished.tool_calls
    assert listing["result"] == "[ ] #1 Draw the board\n[ ] #2 Add the pieces\n0 of 2 done."
    assert created["result"] == "Created task #3: Add sound"
    assert refused["ok"] is False
    # The refusal names what is left, in full, so the agent knows what to finish.
    assert refused["result"] == (
        "Error: the job is not finished: 2 tasks are still open: #2 Add the pieces; "
        "#3 Add sound. Finish each one and mark it done with complete_task, or delete_task "
        "one that is no longer needed, then call finish_job again"
    )
    assert deleted["result"] == "Deleted task #3."
    assert accepted["result"] == "Job marked finished. Built it all."
    assert [(task["id"], task["status"], task["source"]) for task in finished.tasks] == [
        (1, "done", "prompt"),
        (2, "done", "prompt"),
        (3, "deleted", "agent"),
    ]
    assert finished.stats["outcome"] == "completed"


@pytest.mark.asyncio
async def test_tasks_are_not_seeded_without_complete_task(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_tool_call(0, "finish_job", {"final_message": "Done."})],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        )
    ]
    runner, runs, _ = _runner(engine, client)
    planned = replace(BENCHMARK, tasks=("Draw the board",))

    record = runner.start(
        profile, planned, None, tools=True, tool_names=["list_tasks", "finish_job"]
    )
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.tasks == []
    assert finished.tool_calls[0]["ok"] is True
    assert finished.stats["outcome"] == "completed"
