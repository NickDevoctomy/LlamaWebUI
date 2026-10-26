"""Reminders given as the assistant ends a turn, for steps it tends to forget.

Each reminder is given once, as a [Check] message; actions it already covered are not
raised again. Nothing is checked while a job the conversation started is still running,
because the assistant is then rightly waiting for its notification.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence

from llamawebui.models import AssistantMessageRecord

PROFILE_CHANGES = frozenset({"create_profile", "update_profile"})
# Whether each action leaves the router running once it is done.
ROUTER_ACTIONS = {"start_router": True, "stop_router": False, "run_benchmark": False}


def is_check(record: AssistantMessageRecord) -> bool:
    return record.role == "event" and (record.summary or {}).get("kind") == "check"


def _payload(record: AssistantMessageRecord) -> dict[str, object]:
    try:
        payload = json.loads(record.content)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _profile(record: AssistantMessageRecord) -> str:
    payload = _payload(record)
    name = payload.get("profile") or payload.get("alias")
    return name if isinstance(name, str) else "a profile"


def _reminded(record: AssistantMessageRecord) -> set[str]:
    """The kinds of reminder a check message gave."""
    reminders = (record.summary or {}).get("reminders")
    # Checks stored before reminders had kinds covered the first two.
    return set(reminders) if isinstance(reminders, list) else {"unmeasured", "router"}


def turn_checks(
    records: Sequence[AssistantMessageRecord],
    *,
    router_running: bool,
    router_at_start: Mapping[str, object] | None,
    autonomous: bool = False,
    measured: Callable[[str], bool] = lambda profile: False,
) -> list[tuple[str, str]]:
    """What the assistant still has to do, or explain, before it finishes: (kind, text).

    ``measured`` says whether a completed run already measured a profile's current
    settings, as after reverting a change.
    """
    unmeasured: dict[str, None] = {}
    # How the assistant's latest router action left the router (True: running).
    left_running: bool | None = None
    # A benchmark ran since the last report, so the work has results worth reporting.
    unreported = False
    # A question to the operator was already answered with a reminder this turn.
    asked = False
    for record in records:
        if record.role == "user":
            asked = False
        if is_check(record):
            reminded = _reminded(record)
            if "unmeasured" in reminded:
                unmeasured.clear()
            if "router" in reminded:
                left_running = None
            unreported = unreported and "report" not in reminded
            asked = asked or "question" in reminded
        if record.role != "tool" or not (record.summary or {}).get("ok"):
            continue
        name = record.tool_name or ""
        # An edit that leaves the launch as it was (a description, say) needs no benchmark.
        if name in PROFILE_CHANGES and _payload(record).get("launch_changed") is not False:
            unmeasured[_profile(record)] = None
        elif name in {"run_benchmark", "restore_snapshot"}:
            # Measured, or back to a state that already was.
            unmeasured.pop(_profile(record), None)
        if name in ROUTER_ACTIONS:
            left_running = ROUTER_ACTIONS[name]
        if name == "run_benchmark":
            unreported = True
        elif name == "write_report":
            unreported = False
    reminders: list[tuple[str, str]] = []
    unmeasured = {name: None for name in unmeasured if not measured(name)}
    if unmeasured:
        reminders.append(
            (
                "unmeasured",
                f"You changed {', '.join(unmeasured)} after its last benchmark and no "
                "benchmark is running. Start one with run_benchmark to measure the change, "
                "or say why not. Only report a benchmark as running when run_benchmark has "
                "just started it.",
            )
        )
    # Only the assistant's own router changes are its to undo; the operator may have
    # started or stopped the router since.
    if router_at_start is not None and left_running is router_running:
        runtime_id = router_at_start.get("runtime_id")
        if router_running and runtime_id is None:
            reminders.append(
                (
                    "router",
                    "The router was stopped when this conversation began and you left it "
                    "running. Stop it with stop_router before you finish, unless the "
                    "operator asked to keep it running.",
                )
            )
        elif not router_running and runtime_id is not None:
            reminders.append(
                (
                    "router",
                    f"The router was running with runtime {runtime_id} when this "
                    "conversation began and is stopped now. Start it again with start_router "
                    "before you finish, unless the operator said otherwise.",
                )
            )
    last = next((record for record in reversed(records) if record.role == "assistant"), None)
    # Ignoring markdown emphasis or code marks after the question mark.
    question = last is not None and last.content.rstrip().rstrip("*_` ").endswith("?")
    if autonomous and question and not asked:
        reminders.append(
            (
                "question",
                "You are working autonomously (see the brief), so nobody will answer. Choose "
                "what you would recommend, say why in a sentence, and carry on.",
            )
        )
    elif unreported:
        reminders.append(
            (
                "report",
                "You benchmarked changes since the last report. If you have finished, write "
                "the report with write_report so later work can build on it; otherwise carry "
                "on.",
            )
        )
    return reminders
