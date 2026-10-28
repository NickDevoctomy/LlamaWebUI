"""What a profile launches with, so benchmark runs of the same settings can be matched.

A profile's version does not identify its settings: a recommended profile keeps the
version of its recommendation however it is edited. Runs instead record the model file,
runtime and arguments they measured, and a hash of them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from llamawebui.domain.profile_arguments import is_structured
from llamawebui.models import ModelProfileRecord

# Legacy flat configurations keep these beside the arguments; they are not launch settings.
_NOT_ARGUMENTS = frozenset({"alias", "model_path", "acknowledged", "schema_version"})


def launch_settings(profile: ModelProfileRecord) -> dict[str, object]:
    """The model file, runtime and arguments the profile launches with."""
    configuration = profile.configuration or {}
    if is_structured(configuration):
        raw = configuration.get("arguments")
        arguments = [
            {"id": item["id"], "value": item.get("value")}
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
    else:
        arguments = [
            {"id": key, "value": value}
            for key, value in configuration.items()
            if key not in _NOT_ARGUMENTS
        ]
    return {
        "model_path": profile.model_path,
        "runtime_id": profile.runtime_id,
        "arguments": sorted(arguments, key=lambda item: str(item["id"])),
    }


def fingerprint(settings: Mapping[str, object]) -> str:
    text = json.dumps(settings, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def profile_fingerprint(profile: ModelProfileRecord) -> str:
    return fingerprint(launch_settings(profile))


def _values(settings: Mapping[str, object]) -> dict[str, object]:
    arguments = settings.get("arguments")
    return {
        str(item["id"]): item.get("value")
        for item in (arguments if isinstance(arguments, list) else [])
        if isinstance(item, dict) and "id" in item
    }


def settings_changes(
    before: Mapping[str, object], after: Mapping[str, object]
) -> list[dict[str, object]]:
    """What differs between two sets of launch settings, one entry per setting."""
    changes: list[dict[str, object]] = [
        {"setting": key, "before": before.get(key), "after": after.get(key)}
        for key in ("model_path", "runtime_id")
        if before.get(key) != after.get(key)
    ]
    old, new = _values(before), _values(after)
    changes += [
        {"argument": key, "before": old.get(key), "after": new.get(key)}
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]
    return changes
