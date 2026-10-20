"""Model profile validation, llama.cpp preset serialization, and command import/export."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.argument_catalog import ArgumentCatalog, ArgumentDefinition
from llamawebui.domain.profile_arguments import (
    REDACTED,
    ArgumentIssue,
    ArgumentValue,
    ArgumentValueError,
    EmittedOption,
    ProfileArgument,
    ProfileConfiguration,
    coerce_catalog_value,
    emit_arguments,
    errors_of,
    validate_arguments,
)
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities

_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHARD_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$", re.IGNORECASE
)
_RESERVED_ALIASES = frozenset({"health", "models", "props", "slots"})
_NEGATIVE_NUMBER = re.compile(r"^-\d+(?:\.\d+)?$")


class ProfileValidationError(ValueError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class ModelProfile:
    alias: str
    model_path: Path
    arguments: tuple[ProfileArgument, ...] = ()
    acknowledged: frozenset[str] = frozenset()

    @property
    def configuration(self) -> ProfileConfiguration:
        return ProfileConfiguration(self.arguments, self.acknowledged)

    def options(self, catalog: ArgumentCatalog) -> tuple[EmittedOption, ...]:
        """The ordered launch options: the managed model path, then stored arguments."""
        model = EmittedOption("--model", str(self.model_path.resolve()))
        return (model, *emit_arguments(self.arguments, catalog))


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """A tokenized llama-server command mapped onto catalog and custom arguments."""

    model_path: str
    arguments: tuple[ProfileArgument, ...]
    ignored: tuple[str, ...] = ()


def validate_profile(
    profile: ModelProfile, catalog: ArgumentCatalog, capabilities: RuntimeCapabilities
) -> tuple[ArgumentIssue, ...]:
    """Return blocking errors and advisory warnings for a whole profile."""
    issues: list[ArgumentIssue] = []
    if not _ALIAS_PATTERN.fullmatch(profile.alias) or profile.alias in _RESERVED_ALIASES:
        issues.append(
            ArgumentIssue(
                "error", "unsafe-alias", f"unsafe or reserved model alias: {profile.alias}"
            )
        )
    model_path = profile.model_path.resolve()
    if not model_path.is_file():
        issues.append(
            ArgumentIssue("error", "model-missing", f"model file not found: {model_path}")
        )
    else:
        issues.extend(
            ArgumentIssue("error", "model-shards", message)
            for message in _validate_shards(model_path)
        )
    issues.extend(
        validate_arguments(profile.arguments, catalog, capabilities, profile.acknowledged)
    )
    return tuple(issues)


def _validate_shards(model_path: Path) -> tuple[str, ...]:
    match = _SHARD_PATTERN.match(model_path.name)
    if match is None:
        return ()
    if int(match.group("index")) != 1:
        return (f"primary model must be the first shard: {model_path}",)

    count = int(match.group("count"))
    missing = [
        model_path.with_name(f"{match.group('prefix')}-{index:05d}-of-{count:05d}.gguf")
        for index in range(1, count + 1)
        if not model_path.with_name(
            f"{match.group('prefix')}-{index:05d}-of-{count:05d}.gguf"
        ).is_file()
    ]
    if not missing:
        return ()
    return ("missing model shards: " + ", ".join(path.name for path in missing),)


def launch_vector(
    profile: ModelProfile,
    catalog: ArgumentCatalog,
    executable: Path,
    *,
    redact: bool = True,
) -> tuple[str, ...]:
    """The preset-equivalent argument vector for one model, as llama-server receives it."""
    arguments: list[str] = [str(executable)]
    for option in profile.options(catalog):
        arguments.extend(option.argv(redact=redact))
    return tuple(arguments)


def render_preset(
    profile: ModelProfile,
    catalog: ArgumentCatalog,
    capabilities: RuntimeCapabilities,
) -> str:
    errors = errors_of(validate_profile(profile, catalog, capabilities))
    if errors:
        raise ProfileValidationError(errors)
    return render_preset_unchecked(profile, catalog)


def render_preset_unchecked(
    profile: ModelProfile, catalog: ArgumentCatalog, *, redact: bool = False
) -> str:
    lines = ["version = 1", "", f"[{profile.alias}]"]
    for option in profile.options(catalog):
        value = REDACTED if redact and option.sensitive else option.preset_value()
        lines.append(f"{option.key} = {value}")
    return "\n".join(lines) + "\n"


def redact_preset(preset: str, catalog: ArgumentCatalog) -> str:
    """Mask sensitive option values in stored preset text before it is displayed."""
    sensitive = {
        flag.removeprefix("--")
        for definition in catalog.arguments
        if definition.safety is not None and definition.safety.sensitive
        for flag in definition.flags
        if flag.startswith("--")
    }
    lines = []
    for line in preset.splitlines():
        key, separator, _ = line.partition(" = ")
        lines.append(f"{key} = {REDACTED}" if separator and key in sensitive else line)
    return "\n".join(lines) + ("\n" if preset.endswith("\n") else "")


def render_command(
    profile: ModelProfile,
    catalog: ArgumentCatalog,
    executable: Path,
    *,
    platform: str | None = None,
    redact: bool = True,
) -> str:
    """Render a readable, non-executed command from the structured profile."""
    selected_platform = platform or os.name
    arguments = launch_vector(profile, catalog, executable, redact=redact)
    if selected_platform == "nt":
        return subprocess.list2cmdline(arguments)
    return " ".join(shlex.quote(argument) for argument in arguments)


def parse_command(
    command: str,
    catalog: ArgumentCatalog,
    *,
    platform: str | None = None,
    capabilities: RuntimeCapabilities | None = None,
) -> ParsedCommand:
    """Parse a llama-server command without executing it.

    Catalogued options become typed arguments, options advertised only by the runtime become
    uncatalogued runtime options, and anything else is kept as a custom parameter marked
    ``catalog-mismatch``. Options that LlamaWebUI manages itself are skipped and reported.
    """
    if not command.strip():
        raise ValueError("command must not be empty")
    selected_platform = platform or os.name
    tokens = (
        _split_windows_command_line(command)
        if selected_platform == "nt"
        else shlex.split(command, posix=True)
    )
    if not tokens:
        raise ValueError("command must not be empty")
    executable_present = not tokens[0].startswith("-")
    runtime_flags = _runtime_flag_index(capabilities)
    model_path: str | None = None
    arguments: list[ProfileArgument] = []
    positions: dict[str, int] = {}
    ignored: list[str] = []
    index = 1 if executable_present else 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-") or token == "-":
            raise ValueError(f"unexpected command argument: {token}")
        spelling, has_inline, inline_value = token.partition("=")
        inline = inline_value if has_inline else None
        match = catalog.resolve(spelling) or catalog.resolve(spelling.lower())
        if match is None:
            index, raw = _parse_raw_option(tokens, index, spelling, inline, runtime_flags)
            arguments.append(raw)
            continue
        definition = match.definition
        value: object
        if definition.value.type == "boolean":
            enabled = True if inline is None else _import_value(definition, spelling, inline)
            value = enabled != match.negated
            index += 1
        else:
            index, values = _take_values(tokens, index, spelling, inline, definition)
            value = " ".join(values)
        if definition.id == "model":
            if model_path is not None:
                raise ValueError("command must include --model only once")
            model_path = str(value)
            continue
        if not definition.editable:
            sensitive = definition.safety is not None and definition.safety.sensitive
            shown = REDACTED if sensitive else str(value)
            boolean = definition.value.type == "boolean"
            ignored.append(spelling if boolean else f"{spelling} {shown}")
            continue
        existing = positions.get(definition.id)
        if existing is not None and definition.value.repeat:
            previous = arguments[existing].value
            assert isinstance(previous, tuple)
            merged = _import_value(definition, spelling, [*previous, value])
            arguments[existing] = ProfileArgument(definition.flag, merged, catalog_id=definition.id)
            continue
        if existing is not None:
            raise ValueError(f"{definition.flag} is specified more than once")
        positions[definition.id] = len(arguments)
        coerced = _import_value(
            definition, spelling, [value] if definition.value.repeat else value
        )
        arguments.append(ProfileArgument(definition.flag, coerced, catalog_id=definition.id))
    if model_path is None:
        raise ValueError("command must include --model")
    return ParsedCommand(model_path=model_path, arguments=tuple(arguments), ignored=tuple(ignored))


def _import_value(definition: ArgumentDefinition, spelling: str, value: object) -> ArgumentValue:
    try:
        return coerce_catalog_value(definition, value)
    except ArgumentValueError as error:
        raise ValueError(f"{spelling} has an invalid value: {error}") from error


def _take_values(
    tokens: Sequence[str],
    index: int,
    spelling: str,
    inline: str | None,
    definition: ArgumentDefinition,
) -> tuple[int, list[str]]:
    arity = definition.value.arity
    if inline is not None:
        if arity > 1:
            raise ValueError(f"{spelling} requires {arity} separate values")
        return index + 1, [inline]
    values = list(tokens[index + 1 : index + 1 + arity])
    if len(values) < arity:
        raise ValueError(f"{spelling} requires a value")
    return index + 1 + arity, values


def _parse_raw_option(
    tokens: Sequence[str],
    index: int,
    spelling: str,
    inline: str | None,
    runtime_flags: dict[str, str],
) -> tuple[int, ProfileArgument]:
    canonical = runtime_flags.get(spelling) or runtime_flags.get(spelling.lower())
    origin = "runtime"
    if canonical is None:
        if not spelling.startswith("--"):
            raise ValueError(
                f"unknown short option {spelling}; use its long form so it can be preserved"
            )
        canonical = spelling.lower()
        origin = "catalog-mismatch"
    value: str | bool = True if inline is None else inline
    index += 1
    if inline is None and index < len(tokens) and (
        not tokens[index].startswith("-") or _NEGATIVE_NUMBER.fullmatch(tokens[index])
    ):
        value = tokens[index]
        index += 1
    return index, ProfileArgument(canonical, value, origin=origin)


def _runtime_flag_index(capabilities: RuntimeCapabilities | None) -> dict[str, str]:
    """Map every advertised spelling to the long flag LlamaWebUI should store."""
    if capabilities is None:
        return {}
    index: dict[str, str] = {f"--{option}": f"--{option}" for option in capabilities.options}
    for option in capabilities.details:
        if not option.flag.startswith("--"):
            continue
        for spelling in option.flags:
            index.setdefault(spelling, option.flag)
    return index


def _split_windows_command_line(command: str) -> list[str]:
    """Split a Windows CRT-style command line without invoking a shell."""
    arguments: list[str] = []
    length = len(command)
    index = 0
    while index < length:
        while index < length and command[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        value: list[str] = []
        quoted = False
        while index < length and (quoted or command[index] not in " \t\r\n"):
            backslashes = 0
            while index < length and command[index] == "\\":
                backslashes += 1
                index += 1
            if index < length and command[index] == '"':
                value.extend("\\" for _ in range(backslashes // 2))
                if backslashes % 2:
                    value.append('"')
                else:
                    quoted = not quoted
                index += 1
                continue
            value.extend("\\" for _ in range(backslashes))
            if index < length and (quoted or command[index] not in " \t\r\n"):
                value.append(command[index])
                index += 1
        if quoted:
            raise ValueError("command contains an unterminated quoted argument")
        arguments.append("".join(value))
    return arguments


def write_preset_atomic(
    destination: Path,
    profile: ModelProfile,
    catalog: ArgumentCatalog,
    capabilities: RuntimeCapabilities,
) -> None:
    content = render_preset(profile, catalog, capabilities)
    _write_text_atomic(destination, content)


def combine_presets(presets: Sequence[str]) -> str:
    if not presets:
        raise ValueError("at least one enabled model profile is required")

    sections: list[str] = []
    names: set[str] = set()
    for preset in presets:
        lines = preset.splitlines()
        section_headers = [line for line in lines if line.startswith("[") and line.endswith("]")]
        if not lines or lines[0] != "version = 1" or len(section_headers) != 1:
            raise ValueError("stored model preset is malformed")
        name = section_headers[0][1:-1]
        if not name or name in names:
            raise ValueError(f"duplicate or empty model preset section: {name}")
        names.add(name)
        section_start = lines.index(section_headers[0])
        sections.append("\n".join(lines[section_start:]))
    return "version = 1\n\n" + "\n\n".join(sections) + "\n"


def write_combined_preset_atomic(destination: Path, presets: Sequence[str]) -> None:
    _write_text_atomic(destination, combine_presets(presets))


def _write_text_atomic(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=destination.parent, delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
