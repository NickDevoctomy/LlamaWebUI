"""Catalog-backed profile arguments: coercion, legacy migration, emission, and validation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from llamawebui.domain.argument_catalog import ArgumentCatalog, ArgumentDefinition, ValueSchema
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities

SCHEMA_VERSION = 2
REDACTED = "******"
RawOrigin = Literal["runtime", "custom", "catalog-mismatch", "deprecated"]
RAW_ORIGINS: frozenset[str] = frozenset({"runtime", "custom", "catalog-mismatch", "deprecated"})
ListItem = str | int | float
ArgumentValue = str | int | float | bool | tuple[ListItem, ...]
IssueLevel = Literal["error", "warning"]

_RAW_FLAG_PATTERN = re.compile(r"^--[a-z0-9][a-z0-9-]*$")
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
# Newlines and other control characters would break the preset file; tabs are harmless.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
# Defence in depth: no shell is ever invoked, but raw values must not carry command
# substitution or whitespace-delimited chaining/redirection operators. Values such as
# ``top_k;top_p`` or the regex ``(up|down)`` remain valid.
_SHELL_SYNTAX = re.compile(r"`|\$[({]|(?:^|\s)(?:\d?>>?|<|&&|\|\||[;&|])(?=\s|$)|[;&|]\s")

# Legacy flat profile fields, in the order the legacy serializer emitted them.
_LEGACY_BEFORE_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("n_gpu_layers", "n-gpu-layers"),
    ("ctx_size", "ctx-size"),
    ("flash_attn", "flash-attn"),
    ("load_mode", "load-mode"),
    ("lazy_mode", "lazy-mode"),
    ("cache_ram", "cache-ram"),
    ("fit", "fit"),
)
_LEGACY_AFTER_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("cache_type_k", "cache-type-k"),
    ("cache_type_v", "cache-type-v"),
    ("threads", "threads"),
    ("batch_size", "batch-size"),
    ("ubatch_size", "ubatch-size"),
)
LEGACY_FIELDS: frozenset[str] = frozenset(
    {
        "no_reasoning_preserve",
        "override_tensor",
        "advanced",
        *(name for name, _ in _LEGACY_BEFORE_OVERRIDES),
        *(name for name, _ in _LEGACY_AFTER_OVERRIDES),
    }
)


class ArgumentValueError(ValueError):
    """A value does not satisfy its catalog schema."""


@dataclass(frozen=True, slots=True)
class ProfileArgument:
    """One stored launch argument: catalog-backed when ``catalog_id`` is set, otherwise raw."""

    flag: str
    value: ArgumentValue
    catalog_id: str | None = None
    origin: str | None = None

    @property
    def is_raw(self) -> bool:
        return self.catalog_id is None

    def to_payload(self) -> dict[str, object]:
        value: object = list(self.value) if isinstance(self.value, tuple) else self.value
        if self.catalog_id is not None:
            return {"id": self.catalog_id, "value": value}
        return {"flag": self.flag, "value": value, "origin": self.origin or "custom"}


@dataclass(frozen=True, slots=True)
class ProfileConfiguration:
    """The structured (schema version 2) launch configuration of a profile."""

    arguments: tuple[ProfileArgument, ...] = ()
    acknowledged: frozenset[str] = frozenset()

    def to_payload(self, catalog: ArgumentCatalog) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": catalog.catalog_version,
            "arguments": [argument.to_payload() for argument in self.arguments],
            "acknowledged": sorted(self.acknowledged),
        }


@dataclass(frozen=True, slots=True)
class EmittedOption:
    """One option exactly as it is written to the preset and launch vector."""

    name: str
    value: str | bool = True
    sensitive: bool = False
    arity: int = 1

    @property
    def key(self) -> str:
        return self.name.removeprefix("--")

    def preset_value(self) -> str:
        return "true" if self.value is True else str(self.value)

    def argv(self, *, redact: bool = False) -> tuple[str, ...]:
        if self.value is True:
            return (self.name,)
        if redact and self.sensitive:
            return (self.name, REDACTED)
        value = str(self.value)
        return (self.name, *value.split()) if self.arity > 1 else (self.name, value)


@dataclass(frozen=True, slots=True)
class ArgumentIssue:
    level: IssueLevel
    code: str
    message: str
    argument: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "argument": self.argument,
        }


@dataclass(slots=True)
class _IssueCollector:
    issues: list[ArgumentIssue] = field(default_factory=list)

    def error(self, code: str, message: str, argument: str | None = None) -> None:
        self.issues.append(ArgumentIssue("error", code, message, argument))

    def warning(self, code: str, message: str, argument: str | None = None) -> None:
        self.issues.append(ArgumentIssue("warning", code, message, argument))


# --- value coercion ---------------------------------------------------------------


def coerce_catalog_value(definition: ArgumentDefinition, raw: object) -> ArgumentValue:
    """Convert API, import, or stored input to the definition's canonical value."""
    schema = definition.value
    flag = definition.flag
    if schema.type == "boolean":
        return _coerce_boolean(flag, raw)
    if schema.type == "list":
        return _coerce_list(flag, schema, raw)
    if schema.type in {"integer", "number"}:
        return _coerce_number(flag, schema, raw)
    text = _coerce_text(flag, raw)
    if schema.type == "enum":
        allowed = [option.value for option in schema.options]
        if text not in allowed:
            raise ArgumentValueError(f"{flag} must be one of: {', '.join(allowed)}")
    elif schema.type == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as error:
            raise ArgumentValueError(f"{flag} must be valid JSON: {error.msg}") from error
    _check_pattern(flag, schema, text)
    return text


def _coerce_boolean(flag: str, raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in {"true", "1", "on", "yes"}:
        return True
    if isinstance(raw, str) and raw.strip().lower() in {"false", "0", "off", "no"}:
        return False
    raise ArgumentValueError(f"{flag} must be true or false")


def _coerce_text(flag: str, raw: object) -> str:
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise ArgumentValueError(f"{flag} requires a text value")
    text = raw if isinstance(raw, str) else _format_number(raw)
    if not text:
        raise ArgumentValueError(f"{flag} requires a value")
    if _CONTROL_CHARACTERS.search(text):
        raise ArgumentValueError(f"{flag} must not contain newlines or control characters")
    return text


def _coerce_number(flag: str, schema: ValueSchema, raw: object) -> int | float | str:
    if isinstance(raw, str) and raw.strip() in schema.special_values:
        return raw.strip()
    integer = schema.type == "integer"
    value: int | float
    if isinstance(raw, bool):
        raise ArgumentValueError(f"{flag} must be a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if integer and not raw.is_integer():
            raise ArgumentValueError(f"{flag} must be a whole number")
        value = int(raw) if integer else raw
    elif isinstance(raw, str) and _INTEGER_PATTERN.fullmatch(raw.strip()):
        value = int(raw.strip())
    elif isinstance(raw, str) and not integer:
        try:
            value = float(raw.strip())
        except ValueError as error:
            raise ArgumentValueError(f"{flag} must be a number") from error
    else:
        special = f" or one of: {', '.join(schema.special_values)}" if schema.special_values else ""
        kind = "a whole number" if integer else "a number"
        raise ArgumentValueError(f"{flag} must be {kind}{special}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ArgumentValueError(f"{flag} must be a finite number")
    if schema.minimum is not None and value < schema.minimum:
        raise ArgumentValueError(f"{flag} must be at least {_format_number(schema.minimum)}")
    if schema.maximum is not None and value > schema.maximum:
        raise ArgumentValueError(f"{flag} must be at most {_format_number(schema.maximum)}")
    return value


def _coerce_list(flag: str, schema: ValueSchema, raw: object) -> tuple[ListItem, ...]:
    if isinstance(raw, list | tuple):
        items: list[object] = list(raw)
    elif isinstance(raw, str) and schema.separator:
        items = [item for item in raw.split(schema.separator)]
    else:
        items = [raw]
    if not items:
        raise ArgumentValueError(f"{flag} requires at least one value")
    coerced: list[ListItem] = []
    for item in items:
        if schema.item_type in {"integer", "number"}:
            item_schema = ValueSchema(type="integer" if schema.item_type == "integer" else "number")
            coerced.append(_coerce_number(flag, item_schema, item))
            continue
        text = _coerce_text(flag, item)
        if schema.separator and schema.separator in text:
            raise ArgumentValueError(f"{flag} items must not contain '{schema.separator}'")
        _check_pattern(flag, schema, text)
        coerced.append(text)
    return tuple(coerced)


def _check_pattern(flag: str, schema: ValueSchema, text: str) -> None:
    if schema.pattern is not None and re.search(schema.pattern, text) is None:
        raise ArgumentValueError(f"{flag} has an invalid format: {text}")


def _format_number(value: int | float) -> str:
    return str(value)


def _format_value(value: ArgumentValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return ",".join(_format_value(item) for item in value)
    return str(value)


def normalize_raw_flag(flag: str) -> str:
    stripped = flag.strip()
    return stripped if stripped.startswith("-") else f"--{stripped}"


def normalize_raw_value(value: object) -> str | bool:
    """Raw passthrough values are text, or ``True`` for a flag without a value."""
    if value is True:
        return True
    if value is False:
        return "false"
    if isinstance(value, int | float):
        return _format_number(value)
    if isinstance(value, str):
        return value
    raise ArgumentValueError("raw option values must be text or true")


# --- emission -----------------------------------------------------------------------


def emit_arguments(
    arguments: Iterable[ProfileArgument], catalog: ArgumentCatalog
) -> tuple[EmittedOption, ...]:
    """Expand stored arguments into the exact ordered options passed to llama-server."""
    emitted: list[EmittedOption] = []
    for argument in arguments:
        emitted.extend(emit_argument(argument, catalog))
    return tuple(emitted)


def emit_argument(argument: ProfileArgument, catalog: ArgumentCatalog) -> tuple[EmittedOption, ...]:
    definition = catalog.get(argument.catalog_id) if argument.catalog_id else None
    if definition is None:
        match = catalog.resolve(argument.flag)
        sensitive = bool(match and match.definition.safety and match.definition.safety.sensitive)
        raw: str | bool = True if argument.value is True else _format_value(argument.value)
        return (EmittedOption(argument.flag, raw, sensitive=sensitive),)
    schema = definition.value
    sensitive = bool(definition.safety and definition.safety.sensitive)
    if schema.type == "boolean":
        if argument.value is True:
            return (EmittedOption(definition.flag, True),)
        if argument.value is False and schema.negation:
            return (EmittedOption(schema.negation, True),)
        return ()
    if schema.type == "list" and isinstance(argument.value, tuple):
        if schema.repeat:
            return tuple(
                EmittedOption(definition.flag, _format_value(item), sensitive=sensitive)
                for item in argument.value
            )
        separator = schema.separator or ","
        joined = separator.join(_format_value(item) for item in argument.value)
        return (EmittedOption(definition.flag, joined, sensitive=sensitive),)
    return (
        EmittedOption(
            definition.flag, _format_value(argument.value), sensitive=sensitive, arity=schema.arity
        ),
    )


# --- structured storage and legacy migration ---------------------------------------


def is_structured(configuration: Mapping[str, object]) -> bool:
    return configuration.get("schema_version") == SCHEMA_VERSION


def normalize_configuration(
    configuration: Mapping[str, object], catalog: ArgumentCatalog
) -> ProfileConfiguration:
    """Read a stored configuration in either the structured or the legacy flat format."""
    if is_structured(configuration):
        return parse_structured_configuration(configuration, catalog)
    return migrate_legacy_configuration(configuration, catalog)


def parse_structured_configuration(
    configuration: Mapping[str, object], catalog: ArgumentCatalog
) -> ProfileConfiguration:
    entries = configuration.get("arguments")
    arguments: list[ProfileArgument] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        arguments.append(_stored_argument(entry, catalog))
    acknowledged = configuration.get("acknowledged")
    return ProfileConfiguration(
        arguments=tuple(arguments),
        acknowledged=frozenset(
            item for item in acknowledged if isinstance(item, str)
        ) if isinstance(acknowledged, list) else frozenset(),
    )


def _stored_argument(entry: Mapping[str, object], catalog: ArgumentCatalog) -> ProfileArgument:
    value = entry.get("value", True)
    argument_id = entry.get("id")
    if isinstance(argument_id, str):
        definition = catalog.get(argument_id)
        if definition is None:
            # Written by a newer catalog: keep it, unchanged, as a custom parameter.
            return ProfileArgument(
                flag=f"--{argument_id}", value=_stored_raw_value(value), origin="catalog-mismatch"
            )
        stored: ArgumentValue
        if isinstance(value, list):
            stored = tuple(item for item in value if isinstance(item, str | int | float))
        elif isinstance(value, str | int | float | bool):
            stored = value
        else:
            stored = str(value)
        return ProfileArgument(flag=definition.flag, value=stored, catalog_id=definition.id)
    origin = entry.get("origin")
    return ProfileArgument(
        flag=str(entry.get("flag", "")),
        value=_stored_raw_value(value),
        origin=origin if isinstance(origin, str) and origin in RAW_ORIGINS else "custom",
    )


def _stored_raw_value(value: object) -> str | bool:
    try:
        return normalize_raw_value(value)
    except ArgumentValueError:
        return json.dumps(value)


def migrate_legacy_configuration(
    configuration: Mapping[str, object], catalog: ArgumentCatalog
) -> ProfileConfiguration:
    """Convert a legacy flat configuration without changing a single emitted option.

    Each legacy option becomes a typed catalog argument only when that argument emits exactly
    the same option text; anything else is preserved as a raw custom parameter so that no key
    is dropped and the launch vector stays byte-for-byte identical.
    """
    arguments: list[ProfileArgument] = []
    if configuration.get("no_reasoning_preserve") is True:
        arguments.append(
            _migrate_option(catalog, (EmittedOption("--no-reasoning-preserve", True),))
        )
    for field_name, flag in _LEGACY_BEFORE_OVERRIDES:
        arguments.extend(_migrate_field(catalog, configuration, field_name, flag))
    overrides = configuration.get("override_tensor")
    if isinstance(overrides, list | tuple) and overrides:
        legacy = tuple(EmittedOption("--override-tensor", str(item)) for item in overrides)
        migrated = _migrate_option(catalog, legacy)
        if migrated.catalog_id is not None:
            arguments.append(migrated)
        else:
            arguments.extend(
                ProfileArgument(option.name, option.value, origin="catalog-mismatch")
                for option in legacy
            )
    for field_name, flag in _LEGACY_AFTER_OVERRIDES:
        arguments.extend(_migrate_field(catalog, configuration, field_name, flag))
    advanced = configuration.get("advanced")
    for entry in advanced if isinstance(advanced, list) else []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
            continue
        name = "--" + str(entry["name"]).strip().lstrip("-")
        value = _legacy_raw_value(entry.get("value", True))
        arguments.append(_migrate_option(catalog, (EmittedOption(name, value),)))
    return ProfileConfiguration(arguments=tuple(arguments))


def _migrate_field(
    catalog: ArgumentCatalog, configuration: Mapping[str, object], field_name: str, flag: str
) -> tuple[ProfileArgument, ...]:
    value = configuration.get(field_name)
    if value is None:
        return ()
    return (_migrate_option(catalog, (EmittedOption(f"--{flag}", _legacy_raw_value(value)),)),)


def _legacy_raw_value(value: object) -> str | bool:
    if value is True:
        return True
    if isinstance(value, bool):
        return "false"
    return str(value)


def _migrate_option(
    catalog: ArgumentCatalog, legacy: tuple[EmittedOption, ...]
) -> ProfileArgument:
    first = legacy[0]
    match = catalog.resolve(first.name)
    fallback = ProfileArgument(first.name, first.value, origin="catalog-mismatch")
    if match is None:
        return fallback
    definition = match.definition
    if definition.control == "removed":
        return ProfileArgument(first.name, first.value, origin="deprecated")
    if not definition.editable:
        return fallback
    try:
        if definition.value.type == "boolean":
            if first.value is not True:
                return fallback
            candidate_value: object = not match.negated
        elif definition.value.type == "list" and definition.value.repeat:
            candidate_value = [str(option.value) for option in legacy]
        else:
            candidate_value = first.value
        value = coerce_catalog_value(definition, candidate_value)
    except ArgumentValueError:
        return fallback
    candidate = ProfileArgument(definition.flag, value, catalog_id=definition.id)
    expected = tuple((option.name, option.preset_value()) for option in legacy)
    actual = tuple(
        (option.name, option.preset_value()) for option in emit_argument(candidate, catalog)
    )
    return candidate if actual == expected else fallback


def build_arguments(
    entries: Sequence[Mapping[str, object]], catalog: ArgumentCatalog
) -> tuple[ProfileArgument, ...]:
    """Build stored arguments from API entries, raising with every type error found."""
    arguments: list[ProfileArgument] = []
    errors: list[str] = []
    for entry in entries:
        argument_id = entry.get("id")
        if isinstance(argument_id, str) and argument_id:
            definition = catalog.get(argument_id)
            if definition is None:
                errors.append(f"unknown catalog argument: {argument_id}")
                continue
            try:
                value = coerce_catalog_value(definition, entry.get("value"))
            except ArgumentValueError as error:
                errors.append(str(error))
                continue
            arguments.append(ProfileArgument(definition.flag, value, catalog_id=definition.id))
            continue
        flag = entry.get("flag")
        if not isinstance(flag, str) or not flag.strip():
            errors.append("custom parameters require a flag")
            continue
        origin = entry.get("origin") or "custom"
        if not isinstance(origin, str) or origin not in RAW_ORIGINS:
            errors.append(f"invalid custom parameter origin: {origin}")
            continue
        try:
            raw_value = normalize_raw_value(entry.get("value", True))
        except ArgumentValueError as error:
            errors.append(f"{flag}: {error}")
            continue
        arguments.append(ProfileArgument(normalize_raw_flag(flag), raw_value, origin=origin))
    if errors:
        raise ArgumentValueError("; ".join(errors))
    return tuple(arguments)


# --- validation --------------------------------------------------------------------


def argument_identity(argument: ProfileArgument, catalog: ArgumentCatalog) -> str:
    """The catalog id an argument controls, or its flag when uncatalogued."""
    if argument.catalog_id is not None:
        return argument.catalog_id
    match = catalog.resolve(argument.flag)
    return match.definition.id if match is not None else argument.flag


def validate_arguments(
    arguments: Sequence[ProfileArgument],
    catalog: ArgumentCatalog,
    capabilities: RuntimeCapabilities,
    acknowledged: frozenset[str] = frozenset(),
) -> tuple[ArgumentIssue, ...]:
    """Return blocking errors and non-blocking warnings for a profile's arguments."""
    issues = _IssueCollector()
    present: dict[str, ArgumentDefinition] = {}
    seen: dict[str, str] = {}
    for argument in arguments:
        definition = (
            catalog.get(argument.catalog_id)
            if argument.catalog_id
            else _resolved_definition(argument, catalog)
        )
        identity = argument_identity(argument, catalog)
        label = argument.flag
        if identity in seen:
            issues.error(
                "duplicate-flag",
                f"{label} is set more than once (also as {seen[identity]})",
                identity,
            )
        else:
            seen[identity] = label
        if argument.is_raw:
            _validate_raw(argument, definition, issues)
        elif definition is None:
            issues.error("unknown-argument", f"{label} is not in the argument catalog", identity)
            continue
        else:
            _validate_typed(argument, definition, issues)
        if definition is not None:
            present[definition.id] = definition
            _validate_control(argument, definition, acknowledged, issues)
        _validate_runtime(argument, definition, catalog, capabilities, issues)
    _validate_relationships(present, catalog, issues)
    return tuple(issues.issues)


def _resolved_definition(
    argument: ProfileArgument, catalog: ArgumentCatalog
) -> ArgumentDefinition | None:
    match = catalog.resolve(argument.flag)
    return match.definition if match is not None else None


def _validate_raw(
    argument: ProfileArgument, definition: ArgumentDefinition | None, issues: _IssueCollector
) -> None:
    flag = argument.flag
    if not _RAW_FLAG_PATTERN.fullmatch(flag):
        issues.error(
            "unsafe-flag",
            f"unsafe option name: {flag} (use a long option such as --example-option)",
            flag,
        )
    value = argument.value
    if value is not True:
        text = str(value)
        if _CONTROL_CHARACTERS.search(text):
            issues.error("control-characters", f"option {flag} contains a newline", flag)
        elif _SHELL_SYNTAX.search(text):
            issues.error(
                "shell-syntax",
                f"option {flag} contains shell syntax, which is never interpreted and is blocked",
                flag,
            )
    if argument.origin == "deprecated" and (definition is None or definition.deprecated is None):
        issues.warning(
            "deprecated",
            f"{flag} was removed from current llama-server builds; it is passed through unchanged",
            flag,
        )
    elif argument.origin == "catalog-mismatch":
        reason = (
            f"does not match the catalog definition of {definition.flag}"
            if definition is not None
            else "is not in the argument catalog"
        )
        issues.warning(
            "catalog-mismatch", f"{flag} {reason}; it is passed through unchanged", flag
        )
    elif definition is not None and definition.editable:
        issues.warning(
            "catalogued-custom",
            f"{flag} has a typed control ({definition.label}); "
            "the custom value bypasses its checks",
            flag,
        )


def _validate_typed(
    argument: ProfileArgument, definition: ArgumentDefinition, issues: _IssueCollector
) -> None:
    raw: object = list(argument.value) if isinstance(argument.value, tuple) else argument.value
    try:
        coerce_catalog_value(definition, raw)
    except ArgumentValueError as error:
        issues.error("invalid-value", str(error), definition.id)
        return
    schema = definition.value
    if schema.type == "path" or (schema.type == "list" and schema.item_type == "path"):
        values = argument.value if isinstance(argument.value, tuple) else (argument.value,)
        for item in values:
            _validate_path(definition, str(item), issues)
    if definition.id == "cors-origins" and isinstance(argument.value, tuple) and "*" in [
        str(item).strip() for item in argument.value
    ]:
        issues.warning(
            "open-cors",
            "--cors-origins includes *, which lets any web page call the native API",
            definition.id,
        )


def _validate_path(definition: ArgumentDefinition, value: str, issues: _IssueCollector) -> None:
    path = Path(value).expanduser()
    if not path.is_absolute():
        issues.warning(
            "relative-path",
            f"{definition.flag} is relative and resolves against llama-server's working directory",
            definition.id,
        )
        return
    kind = definition.value.path_kind
    if kind == "file" and not path.is_file():
        issues.warning("path-not-found", f"{definition.flag} file not found: {path}", definition.id)
    elif kind == "directory" and not path.is_dir():
        issues.warning(
            "path-not-found", f"{definition.flag} directory not found: {path}", definition.id
        )


def _validate_control(
    argument: ProfileArgument,
    definition: ArgumentDefinition,
    acknowledged: frozenset[str],
    issues: _IssueCollector,
) -> None:
    if definition.control in {"managed", "informational"}:
        reason = definition.control_reason or "it is controlled by LlamaWebUI"
        issues.error(
            "managed-flag",
            f"{argument.flag} cannot be set in a profile: {reason}",
            definition.id,
        )
        return
    if definition.control == "removed" and argument.origin != "deprecated":
        issues.warning(
            "deprecated",
            f"{argument.flag} was removed from current llama-server builds",
            definition.id,
        )
    if definition.deprecated is not None:
        replacement = (
            f" Use --{definition.deprecated.replacement} instead."
            if definition.deprecated.replacement
            else ""
        )
        issues.warning(
            "deprecated", f"{argument.flag}: {definition.deprecated.message}{replacement}",
            definition.id,
        )
    safety = definition.safety
    if safety is None:
        return
    if safety.requires_confirmation and definition.id not in acknowledged:
        issues.error(
            "unconfirmed-security",
            f"{argument.flag} requires a security acknowledgement: {safety.warning}",
            definition.id,
        )
    elif safety.level in {"warning", "danger"}:
        issues.warning("security", f"{argument.flag}: {safety.warning}", definition.id)


def _validate_runtime(
    argument: ProfileArgument,
    definition: ArgumentDefinition | None,
    catalog: ArgumentCatalog,
    capabilities: RuntimeCapabilities,
    issues: _IssueCollector,
) -> None:
    if not capabilities.help_parsed:
        return
    if definition is not None and not definition.availability.requires_advertised_flag:
        return
    for option in emit_argument(argument, catalog):
        if not capabilities.supports(option.name):
            issues.warning(
                "not-advertised",
                f"runtime does not advertise {option.name}; it will still be passed to "
                "llama-server",
                argument_identity(argument, catalog),
            )
            return


def _validate_relationships(
    present: Mapping[str, ArgumentDefinition], catalog: ArgumentCatalog, issues: _IssueCollector
) -> None:
    reported: set[frozenset[str]] = set()
    for definition in present.values():
        for other in definition.conflicts:
            pair = frozenset({definition.id, other})
            if other in present and pair not in reported:
                reported.add(pair)
                issues.warning(
                    "conflict",
                    f"{definition.flag} conflicts with {present[other].flag}; set only one",
                    definition.id,
                )
        if definition.requires and not any(item in present for item in definition.requires):
            needed = " or ".join(
                required.flag
                for required in (catalog.get(item) for item in definition.requires)
                if required is not None
            )
            issues.warning(
                "missing-dependency",
                f"{definition.flag} has no effect without {needed}",
                definition.id,
            )


def errors_of(issues: Iterable[ArgumentIssue]) -> tuple[str, ...]:
    return tuple(issue.message for issue in issues if issue.level == "error")


def warnings_of(issues: Iterable[ArgumentIssue]) -> tuple[str, ...]:
    return tuple(issue.message for issue in issues if issue.level == "warning")
