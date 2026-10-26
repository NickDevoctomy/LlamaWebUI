"""Tools that create, tweak, clone and delete model profiles.

Every change is validated before the operator is asked to approve it, so an approval
is never spent on a change that would be refused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.assistant.tools.system import find_profile
from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.model_profile import (
    ModelProfile,
    ProfileValidationError,
    render_command,
    validate_profile,
)
from llamawebui.domain.profile_arguments import (
    ArgumentValueError,
    build_arguments,
    errors_of,
    warnings_of,
)
from llamawebui.models import ModelProfileRecord, ProfileSnapshotRecord, RuntimeRecord
from llamawebui.services.control_plane import (
    ControlPlaneError,
    next_profile_version,
    router_diagnostics,
)
from llamawebui.services.profile_registry import (
    ProfileAliasExistsError,
    ProfileNotFoundError,
    configuration_payload,
)
from llamawebui.services.profile_snapshots import SnapshotNotFoundError
from llamawebui.services.runtime_registry import RuntimeNotFoundError, runtime_capabilities

ARGUMENT_ENTRY = {
    "type": "object",
    "description": "A catalog argument by id, or a raw llama-server flag.",
    "properties": {
        "id": {"type": "string", "description": "Catalog id, e.g. ctx-size."},
        "flag": {"type": "string", "description": "A flag not in the catalog, e.g. --foo."},
        "value": {"description": "The value; true for a switch."},
    },
    "required": ["value"],
}


@dataclass(frozen=True, slots=True)
class PlannedProfile:
    profile: ModelProfile
    runtime: RuntimeRecord
    entries: list[dict[str, object]]
    errors: list[str]
    warnings: list[str]


def _entries(value: object, name: str) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ToolError(f"{name} must be a list of objects")
    return [dict(item) for item in value]


def _key(entry: Mapping[str, object], catalog: ArgumentCatalog) -> str:
    """An argument's identity: its catalog id, even when given by flag."""
    if isinstance(entry.get("id"), str):
        return str(entry["id"])
    flag = str(entry.get("flag", ""))
    match = catalog.resolve(flag)
    return match.definition.id if match is not None else flag


def merge_arguments(
    current: Sequence[Mapping[str, object]],
    changes: Sequence[Mapping[str, object]],
    removals: Sequence[str],
    catalog: ArgumentCatalog,
) -> list[dict[str, object]]:
    """The current arguments with changed ones replaced, new ones added and some removed."""
    removed = {
        _key({"id": item} if not item.startswith("-") else {"flag": item}, catalog)
        for item in removals
    }
    changed = {_key(entry, catalog): dict(entry) for entry in changes}
    merged = []
    for entry in current:
        key = _key(entry, catalog)
        if key in removed:
            continue
        merged.append(changed.pop(key, dict(entry)))
    merged.extend(changed.values())
    return merged


def _runtime(context: ToolContext, runtime_id: str) -> RuntimeRecord:
    try:
        return context.services.runtimes.get(runtime_id)
    except RuntimeNotFoundError as error:
        raise ToolError(str(error)) from error


def _plan(
    context: ToolContext,
    *,
    alias: str,
    runtime_id: str,
    model_path: str,
    entries: list[dict[str, object]],
    acknowledged: Sequence[str] = (),
) -> PlannedProfile:
    catalog = context.services.catalog
    runtime = _runtime(context, runtime_id)
    try:
        arguments = build_arguments(entries, catalog)
    except ArgumentValueError as error:
        raise ToolError(f"invalid arguments: {error}") from error
    profile = ModelProfile(
        alias=alias,
        model_path=Path(model_path),
        arguments=arguments,
        acknowledged=frozenset(acknowledged),
    )
    issues = validate_profile(profile, catalog, runtime_capabilities(runtime))
    errors = [*errors_of(issues), *router_diagnostics(runtime)]
    return PlannedProfile(profile, runtime, entries, errors, list(warnings_of(issues)))


def _changes(
    before: Sequence[Mapping[str, object]],
    after: Sequence[Mapping[str, object]],
    catalog: ArgumentCatalog,
) -> list[dict[str, object]]:
    old = {_key(entry, catalog): entry.get("value") for entry in before}
    new = {_key(entry, catalog): entry.get("value") for entry in after}
    return [
        {"argument": key, "before": old.get(key), "after": new.get(key)}
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]


def _require_valid(planned: PlannedProfile) -> None:
    if planned.errors:
        raise ToolError("the profile would not be valid: " + "; ".join(planned.errors))


def _command(context: ToolContext, planned: PlannedProfile) -> str:
    return render_command(
        planned.profile, context.services.catalog, Path(planned.runtime.executable_path)
    )


# Updating ----------------------------------------------------------------------------------


def _planned_update(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[ModelProfileRecord, PlannedProfile, list[dict[str, object]]]:
    record = find_profile(context, text_argument(arguments, "profile"))
    catalog = context.services.catalog
    configuration = configuration_payload(record, catalog)
    current = _entries(configuration.get("arguments"), "the stored arguments")
    removals = arguments.get("remove_arguments") or []
    if not isinstance(removals, list) or not all(isinstance(item, str) for item in removals):
        raise ToolError("remove_arguments must be a list of argument ids or flags")
    entries = merge_arguments(
        current, _entries(arguments.get("set_arguments"), "set_arguments"), removals, catalog
    )
    acknowledged = configuration.get("acknowledged")
    planned = _plan(
        context,
        alias=record.alias,
        runtime_id=text_argument(arguments, "runtime_id", required=False) or record.runtime_id,
        model_path=text_argument(arguments, "model_path", required=False) or record.model_path,
        entries=entries,
        acknowledged=acknowledged if isinstance(acknowledged, list) else (),
    )
    return record, planned, _changes(current, entries, catalog)


async def preview_profile(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record, planned, changes = _planned_update(context, arguments)
    return json_result(
        {
            "profile": record.alias,
            "changes": changes,
            "valid": not planned.errors,
            "errors": planned.errors,
            "warnings": planned.warnings,
            "command": _command(context, planned),
        },
        "valid" if not planned.errors else f"{len(planned.errors)} errors",
    )


def _require_router_stopped(context: ToolContext, action: str) -> None:
    if context.services.benchmark_runner.active_run_id is not None:
        raise ToolError(
            f"a benchmark is running, so wait for its notification before {action}"
        )
    try:
        context.services.control_plane.require_router_stopped(action)
    except ControlPlaneError as error:
        raise ToolError(str(error)) from error


def _checked_update(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[ModelProfileRecord, PlannedProfile, list[dict[str, object]], dict[str, object]]:
    """A valid update that changes something, with the router stopped; or a ToolError."""
    _require_router_stopped(context, "editing a profile")
    record, planned, changes = _planned_update(context, arguments)
    _require_valid(planned)
    current = {
        "enabled": record.enabled,
        "exclusive": record.exclusive,
        "description": record.description,
    }
    other = {
        key: arguments[key]
        for key in current
        if key in arguments and arguments[key] != current[key]
    }
    moved = (
        planned.runtime.id != record.runtime_id
        or str(planned.profile.model_path) != record.model_path
    )
    if not changes and not other and not moved:
        raise ToolError("nothing would change")
    return record, planned, changes, other


async def describe_update(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    record, _, changes, other = _checked_update(context, arguments)
    return f"Change profile {record.alias}", {"changes": changes, **other}


def snapshot_once(context: ToolContext, record: ModelProfileRecord, name: str) -> str | None:
    """Snapshot a profile the first time this conversation touches it; the new id, if any."""
    snapshots = context.services.snapshots
    if not context.conversation_id or snapshots.list(
        profile_id=record.id, conversation_id=context.conversation_id
    ):
        return None
    return snapshots.create(
        record.id, name, created_by="assistant", conversation_id=context.conversation_id
    ).id


async def update_profile(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    services = context.services
    record, planned, changes, _ = _checked_update(context, arguments)
    snapshot_id = snapshot_once(context, record, f"{record.alias} before the assistant changed it")
    enabled = arguments.get("enabled")
    exclusive = arguments.get("exclusive")
    description = arguments.get("description")
    exclusive_value = exclusive if isinstance(exclusive, bool) else record.exclusive
    try:
        saved = services.profiles.update(
            record.id,
            profile=planned.profile,
            runtime_id=planned.runtime.id,
            enabled=enabled if isinstance(enabled, bool) else record.enabled,
            exclusive=exclusive_value,
            description=description if isinstance(description, str) else record.description,
            version=next_profile_version(
                record,
                planned.profile,
                requested_version=None,
                runtime_id=planned.runtime.id,
                exclusive=exclusive_value,
                catalog=services.catalog,
            ),
        )
    except (ProfileValidationError, ProfileNotFoundError, RuntimeNotFoundError) as error:
        raise ToolError(str(error)) from error
    # Whether the model launches differently; a description or enabled change does not.
    launch_changed = (
        bool(changes)
        or planned.runtime.id != record.runtime_id
        or str(planned.profile.model_path) != record.model_path
    )
    payload: dict[str, object] = {
        "profile": saved.alias,
        "version": saved.version,
        "changes": changes,
        "warnings": planned.warnings,
        "launch_changed": launch_changed,
    }
    if launch_changed:
        payload["note"] = "Not measured yet: benchmark this change before judging it."
    if snapshot_id:
        payload["snapshot_id"] = snapshot_id
        payload["snapshot_note"] = "Snapshot of the profile before this change; restore it "
        "with restore_snapshot if the work does not pay off."
    return json_result(
        payload,
        f"updated {saved.alias}"
        + (f" to v{saved.version}" if saved.version != record.version else ""),
    )


# Creating, cloning and deleting ------------------------------------------------------------


def _planned_create(context: ToolContext, arguments: Mapping[str, object]) -> PlannedProfile:
    return _plan(
        context,
        alias=text_argument(arguments, "alias"),
        runtime_id=text_argument(arguments, "runtime_id"),
        model_path=text_argument(arguments, "model_path"),
        entries=_entries(arguments.get("arguments"), "arguments"),
    )


async def describe_create(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    planned = _planned_create(context, arguments)
    _require_valid(planned)
    if any(item.alias == planned.profile.alias for item in context.services.profiles.list()):
        raise ToolError(f"model alias is already registered: {planned.profile.alias}")
    return (
        f"Create profile {planned.profile.alias}",
        {
            "runtime": planned.runtime.name,
            "model": planned.profile.model_path.name,
            "arguments": planned.entries,
        },
    )


async def create_profile(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    planned = _planned_create(context, arguments)
    _require_valid(planned)
    description = arguments.get("description")
    try:
        record = context.services.profiles.create(
            profile=planned.profile,
            runtime_id=planned.runtime.id,
            enabled=arguments.get("enabled") is not False,
            exclusive=arguments.get("exclusive") is True,
            description=description if isinstance(description, str) else None,
        )
    except (ProfileAliasExistsError, ProfileValidationError, RuntimeNotFoundError) as error:
        raise ToolError(str(error)) from error
    return json_result(
        {
            "profile_id": record.id,
            "alias": record.alias,
            "warnings": planned.warnings,
            "command": _command(context, planned),
        },
        f"created {record.alias}",
    )


async def describe_clone(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    record = find_profile(context, text_argument(arguments, "profile"))
    alias = text_argument(arguments, "alias")
    return f"Copy profile {record.alias} as {alias}", {"from": record.alias, "alias": alias}


async def clone_profile(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = find_profile(context, text_argument(arguments, "profile"))
    try:
        clone = context.services.profiles.clone(record.id, text_argument(arguments, "alias"))
    except (ProfileAliasExistsError, ProfileNotFoundError, ValueError) as error:
        raise ToolError(str(error)) from error
    return json_result(
        {"profile_id": clone.id, "alias": clone.alias, "enabled": clone.enabled},
        f"copied as {clone.alias}",
    )


async def describe_delete(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    _require_router_stopped(context, "deleting a model profile")
    record = find_profile(context, text_argument(arguments, "profile"))
    return f"Delete profile {record.alias}", {"profile": record.alias, "model": record.model_path}


async def delete_profile(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    _require_router_stopped(context, "deleting a model profile")
    record = find_profile(context, text_argument(arguments, "profile"))
    context.services.profiles.remove(record.id)
    return json_result({"deleted": record.alias}, f"deleted {record.alias}")


PROFILE_REFERENCE = {"type": "string", "description": "The profile's id or alias."}

# Snapshots ---------------------------------------------------------------------------------


def _snapshot_summary(context: ToolContext, record: ProfileSnapshotRecord) -> dict[str, object]:
    try:
        alias = context.services.profiles.get(record.profile_id).alias
    except ProfileNotFoundError:
        alias = record.profile_id
    configuration = record.configuration
    return {
        "snapshot_id": record.id,
        "profile": alias,
        "name": record.name,
        "taken": record.created_at,
        "version": record.version,
        "arguments": configuration.get("arguments") if isinstance(configuration, dict) else None,
    }


async def list_snapshots(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    reference = text_argument(arguments, "profile", required=False)
    profile_id = find_profile(context, reference).id if reference else None
    records = context.services.snapshots.list(profile_id=profile_id)[:20]
    return json_result(
        [_snapshot_summary(context, record) for record in records], f"{len(records)} snapshots"
    )


async def create_snapshot(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    record = find_profile(context, text_argument(arguments, "profile"))
    snapshot = context.services.snapshots.create(
        record.id,
        text_argument(arguments, "name"),
        created_by="assistant",
        conversation_id=context.conversation_id or None,
    )
    return json_result(
        {"snapshot_id": snapshot.id, "profile": record.alias, "name": snapshot.name},
        f"snapshot of {record.alias}",
    )


def _snapshot(context: ToolContext, arguments: Mapping[str, object]) -> ProfileSnapshotRecord:
    try:
        return context.services.snapshots.get(text_argument(arguments, "snapshot_id"))
    except SnapshotNotFoundError as error:
        raise ToolError(str(error)) from error


async def describe_restore(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    _require_router_stopped(context, "restoring a profile snapshot")
    snapshot = _snapshot(context, arguments)
    summary = _snapshot_summary(context, snapshot)
    return (
        f"Restore {summary['profile']} to snapshot “{snapshot.name}”",
        {"profile": summary["profile"], "snapshot": snapshot.name, "taken": snapshot.created_at},
    )


async def restore_snapshot(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    _require_router_stopped(context, "restoring a profile snapshot")
    snapshot = _snapshot(context, arguments)
    try:
        saved = context.services.snapshots.restore(snapshot.id)
    except (ProfileValidationError, ProfileNotFoundError, RuntimeNotFoundError) as error:
        raise ToolError(str(error)) from error
    return json_result(
        {"profile": saved.alias, "version": saved.version, "restored": snapshot.name},
        f"restored {saved.alias}",
    )


PROFILE_TOOLS = (
    Tool(
        "preview_profile",
        "Check a change to a profile without saving it: the arguments that would change, "
        "errors, warnings and the resulting llama-server command.",
        object_schema(
            {
                "profile": PROFILE_REFERENCE,
                "set_arguments": {"type": "array", "items": ARGUMENT_ENTRY},
                "remove_arguments": {"type": "array", "items": {"type": "string"}},
                "runtime_id": {"type": "string"},
                "model_path": {"type": "string"},
            },
            ["profile"],
        ),
        "profiles.read",
        preview_profile,
    ),
    Tool(
        "update_profile",
        "Change a profile: set or add arguments (others are kept), remove arguments, switch "
        "its runtime or model file, enable or disable it. The router must be stopped.",
        object_schema(
            {
                "profile": PROFILE_REFERENCE,
                "set_arguments": {"type": "array", "items": ARGUMENT_ENTRY},
                "remove_arguments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Catalog ids or flags to remove.",
                },
                "runtime_id": {"type": "string"},
                "model_path": {"type": "string"},
                "enabled": {"type": "boolean"},
                "exclusive": {"type": "boolean"},
                "description": {"type": "string"},
            },
            ["profile"],
        ),
        "profiles.write",
        update_profile,
        approval="profiles",
        describe=describe_update,
    ),
    Tool(
        "create_profile",
        "Create a profile for a downloaded model file (a path from list_library).",
        object_schema(
            {
                "alias": {"type": "string", "description": "Lowercase letters, digits, . _ -"},
                "runtime_id": {"type": "string"},
                "model_path": {"type": "string"},
                "arguments": {"type": "array", "items": ARGUMENT_ENTRY},
                "enabled": {"type": "boolean"},
                "exclusive": {"type": "boolean"},
                "description": {"type": "string"},
            },
            ["alias", "runtime_id", "model_path"],
        ),
        "profiles.write",
        create_profile,
        approval="profiles",
        describe=describe_create,
    ),
    Tool(
        "clone_profile",
        "Copy a profile under a new alias, to try changes without touching the original. "
        "The copy starts disabled.",
        object_schema(
            {"profile": PROFILE_REFERENCE, "alias": {"type": "string"}}, ["profile", "alias"]
        ),
        "profiles.write",
        clone_profile,
        approval="profiles",
        describe=describe_clone,
    ),
    Tool(
        "delete_profile",
        "Delete a profile. The model file stays in the library. The router must be stopped.",
        object_schema({"profile": PROFILE_REFERENCE}, ["profile"]),
        "profiles.write",
        delete_profile,
        approval="profiles",
        describe=describe_delete,
    ),
    Tool(
        "list_snapshots",
        "Saved snapshots of profiles, newest first, with the arguments each holds. Profiles "
        "are snapshotted automatically before you first change them.",
        object_schema({"profile": PROFILE_REFERENCE}),
        "profiles.read",
        list_snapshots,
    ),
    Tool(
        "create_snapshot",
        "Save a profile's current settings under a name, for example a good configuration "
        "you may want to return to.",
        object_schema(
            {"profile": PROFILE_REFERENCE, "name": {"type": "string"}}, ["profile", "name"]
        ),
        "profiles.write",
        create_snapshot,
    ),
    Tool(
        "restore_snapshot",
        "Put a profile back to a snapshot's settings, for example the state before you began "
        "when your changes did not help. The router must be stopped.",
        object_schema({"snapshot_id": {"type": "string"}}, ["snapshot_id"]),
        "profiles.write",
        restore_snapshot,
        approval="profiles",
        describe=describe_restore,
    ),
)
