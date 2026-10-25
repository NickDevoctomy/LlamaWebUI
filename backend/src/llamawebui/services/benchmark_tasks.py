"""A benchmark agent's task list: the plan it works through with the planning tools.

Tasks are kept as plain dicts, so the list is stored on the run as it is. Deleting a
task only marks it deleted, so the run still shows what the agent dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

# open, done or deleted.
OPEN, DONE, DELETED = "open", "done", "deleted"
MAX_TITLE_LENGTH = 500


class TaskError(ValueError):
    pass


def seed_tasks(titles: Sequence[str]) -> list[dict[str, object]]:
    """The tasks a run starts with, from its benchmark prompt."""
    return [
        {"id": number, "title": title, "status": OPEN, "source": "prompt"}
        for number, title in enumerate(titles, start=1)
    ]


def open_tasks(tasks: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [task for task in tasks if task["status"] == OPEN]


def _line(task: dict[str, object]) -> str:
    mark = "x" if task["status"] == DONE else " "
    return f"[{mark}] #{task['id']} {task['title']}"


def list_tasks(tasks: Sequence[dict[str, object]]) -> str:
    shown = [task for task in tasks if task["status"] != DELETED]
    if not shown:
        return "There are no tasks. Use create_task to plan the work."
    done = sum(task["status"] == DONE for task in shown)
    lines = [_line(task) for task in shown]
    lines.append(f"{done} of {len(shown)} done.")
    return "\n".join(lines)


def create_task(tasks: list[dict[str, object]], title: object) -> str:
    if not isinstance(title, str) or not title.strip():
        raise TaskError("title is required")
    clean = " ".join(title.split())
    if len(clean) > MAX_TITLE_LENGTH:
        raise TaskError(f"title is longer than {MAX_TITLE_LENGTH} characters")
    number = max((int(str(task["id"])) for task in tasks), default=0) + 1
    tasks.append({"id": number, "title": clean, "status": OPEN, "source": "agent"})
    return f"Created task #{number}: {clean}"


def _find(tasks: Sequence[dict[str, object]], identifier: object) -> dict[str, object]:
    # Models often send a number as a string, or with the list's # in front.
    text = str(identifier).strip().lstrip("#") if isinstance(identifier, int | str) else ""
    if isinstance(identifier, bool) or not text.isdigit():
        raise TaskError("id must be a task number from list_tasks")
    task = next((task for task in tasks if task["id"] == int(text)), None)
    if task is None:
        raise TaskError(f"there is no task #{text}")
    return task


def complete_task(tasks: Sequence[dict[str, object]], identifier: object) -> str:
    task = _find(tasks, identifier)
    if task["status"] == DELETED:
        raise TaskError(f"task #{task['id']} was deleted")
    if task["status"] == DONE:
        return f"Task #{task['id']} was already done."
    task["status"] = DONE
    remaining = len(open_tasks(tasks))
    left = f"{remaining} still open." if remaining else "Every task is done."
    return f"Completed task #{task['id']}. {left}"


def delete_task(tasks: Sequence[dict[str, object]], identifier: object) -> str:
    task = _find(tasks, identifier)
    if task["status"] == DELETED:
        return f"Task #{task['id']} was already deleted."
    if task["status"] == DONE:
        raise TaskError(f"task #{task['id']} is done; only open tasks can be deleted")
    task["status"] = DELETED
    return f"Deleted task #{task['id']}."


def unfinished_reason(tasks: Sequence[dict[str, object]]) -> str | None:
    """Why the job cannot be finished yet, or None when no task is still open."""
    remaining = open_tasks(tasks)
    if not remaining:
        return None
    listed = "; ".join(f"#{task['id']} {task['title']}" for task in remaining)
    count = "1 task is" if len(remaining) == 1 else f"{len(remaining)} tasks are"
    return (
        f"the job is not finished: {count} still open: {listed}. Finish each one and mark it "
        "done with complete_task, or delete_task one that is no longer needed, then call "
        "finish_job again"
    )
