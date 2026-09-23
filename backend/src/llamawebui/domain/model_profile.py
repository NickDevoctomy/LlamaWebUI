"""Model profile validation and llama.cpp preset serialization."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.runtime_capabilities import RuntimeCapabilities

_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHARD_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$", re.IGNORECASE
)
_RESERVED_ALIASES = frozenset({"health", "models", "props", "slots"})


class ProfileValidationError(ValueError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def parse_command(command: str, *, platform: str | None = None) -> dict[str, object]:
    """Parse a llama-server command without executing it."""
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
    configuration: dict[str, object] = {"advanced": []}
    executable_present = not tokens[0].startswith("-")
    known: dict[str, tuple[str, type]] = {
        "m": ("model_path", str),
        "model": ("model_path", str),
        "ctx-size": ("ctx_size", int),
        "c": ("ctx_size", int),
        "n-gpu-layers": ("n_gpu_layers", int),
        "ngl": ("n_gpu_layers", int),
        "threads": ("threads", int),
        "t": ("threads", int),
        "batch-size": ("batch_size", int),
        "b": ("batch_size", int),
        "ubatch-size": ("ubatch_size", int),
        "ub": ("ubatch_size", int),
        "flash-attn": ("flash_attn", str),
        "fa": ("flash_attn", str),
        "load-mode": ("load_mode", str),
        "lazy-mode": ("lazy_mode", str),
        "lzm": ("lazy_mode", str),
        "cache-ram": ("cache_ram", int),
        "fit": ("fit", str),
        "cache-type-k": ("cache_type_k", str),
        "ctk": ("cache_type_k", str),
        "cache-type-v": ("cache_type_v", str),
        "ctv": ("cache_type_v", str),
        "override-tensor": ("override_tensor", str),
        "ot": ("override_tensor", str),
    }
    booleans = {"no-reasoning-preserve"}
    index = 1 if executable_present else 0
    option_only_model_seen = False
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            raise ValueError(f"unexpected command argument: {token}")
        name = token.lstrip("-")
        inline_value: str | None = None
        if "=" in name:
            name, inline_value = name.split("=", 1)
        if executable_present and name == "models-preset":
            if inline_value is None:
                index += 1
                if index >= len(tokens):
                    raise ValueError("--models-preset requires a value")
                index += 1
            continue
        if name in booleans:
            if inline_value is not None:
                raise ValueError(f"--{name} does not accept a value")
            configuration["no_reasoning_preserve"] = True
        elif name in known:
            field, value_type = known[name]
            if inline_value is None:
                index += 1
                if index >= len(tokens):
                    raise ValueError(f"--{name} requires a value")
                inline_value = tokens[index]
            if inline_value.startswith("-") and value_type is not str:
                raise ValueError(f"--{name} has an invalid value")
            try:
                parsed_value = value_type(inline_value)
            except ValueError as error:
                raise ValueError(f"--{name} has an invalid value") from error
            if field == "override_tensor":
                configuration.setdefault(field, [])
                cast_list = configuration[field]
                assert isinstance(cast_list, list)
                cast_list.append(parsed_value)
            else:
                if field == "model_path" and not executable_present and option_only_model_seen:
                    raise ValueError("command must include --model only once")
                configuration[field] = parsed_value
                if field == "model_path" and not executable_present:
                    option_only_model_seen = True
        else:
            value: str | bool = True if inline_value is None else inline_value
            if (
                inline_value is None
                and index + 1 < len(tokens)
                and not tokens[index + 1].startswith("-")
            ):
                index += 1
                value = tokens[index]
            advanced = configuration["advanced"]
            assert isinstance(advanced, list)
            advanced.append({"name": name, "value": value})
        index += 1
    if "model_path" not in configuration:
        raise ValueError("command must include --model")
    return configuration


@dataclass(frozen=True, slots=True)
class AdvancedOption:
    name: str
    value: str | int | bool = True


@dataclass(frozen=True, slots=True)
class ModelProfile:
    alias: str
    model_path: Path
    no_reasoning_preserve: bool = False
    n_gpu_layers: int | None = None
    ctx_size: int | None = None
    flash_attn: str | None = None
    load_mode: str | None = None
    lazy_mode: str | None = None
    cache_ram: int | None = None
    fit: str | None = None
    override_tensor: tuple[str, ...] = ()
    cache_type_k: str | None = None
    cache_type_v: str | None = None
    threads: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    advanced: tuple[AdvancedOption, ...] = ()

    def options(self) -> tuple[AdvancedOption, ...]:
        values: list[AdvancedOption] = [AdvancedOption("model", str(self.model_path.resolve()))]
        if self.no_reasoning_preserve:
            values.append(AdvancedOption("no-reasoning-preserve"))
        before_overrides: tuple[tuple[str, str | int | None], ...] = (
            ("n-gpu-layers", self.n_gpu_layers),
            ("ctx-size", self.ctx_size),
            ("flash-attn", self.flash_attn),
            ("load-mode", self.load_mode),
            ("lazy-mode", self.lazy_mode),
            ("cache-ram", self.cache_ram),
            ("fit", self.fit),
        )
        after_overrides: tuple[tuple[str, str | int | None], ...] = (
            ("cache-type-k", self.cache_type_k),
            ("cache-type-v", self.cache_type_v),
            ("threads", self.threads),
            ("batch-size", self.batch_size),
            ("ubatch-size", self.ubatch_size),
        )
        values.extend(
            AdvancedOption(name, value) for name, value in before_overrides if value is not None
        )
        values.extend(AdvancedOption("override-tensor", value) for value in self.override_tensor)
        values.extend(
            AdvancedOption(name, value) for name, value in after_overrides if value is not None
        )
        values.extend(self.advanced)
        return tuple(values)


def validate_profile(
    profile: ModelProfile, capabilities: RuntimeCapabilities
) -> tuple[str, ...]:
    errors: list[str] = []
    if not _ALIAS_PATTERN.fullmatch(profile.alias) or profile.alias in _RESERVED_ALIASES:
        errors.append(f"unsafe or reserved model alias: {profile.alias}")

    model_path = profile.model_path.resolve()
    if not model_path.is_file():
        errors.append(f"model file not found: {model_path}")
    else:
        errors.extend(_validate_shards(model_path))

    for option in profile.options():
        normalized_name = option.name.removeprefix("--").strip().lower()
        if not normalized_name or "\n" in normalized_name or "=" in normalized_name:
            errors.append(f"unsafe option name: {option.name}")
        elif not capabilities.supports(normalized_name):
            errors.append(f"runtime does not support --{normalized_name}")
        if "\n" in str(option.value) or "\r" in str(option.value):
            errors.append(f"option --{normalized_name} contains a newline")
    return tuple(errors)


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


def render_preset(profile: ModelProfile, capabilities: RuntimeCapabilities) -> str:
    errors = validate_profile(profile, capabilities)
    if errors:
        raise ProfileValidationError(errors)

    lines = ["version = 1", "", f"[{profile.alias}]"]
    for option in profile.options():
        value = str(option.value).lower() if isinstance(option.value, bool) else str(option.value)
        lines.append(f"{option.name.removeprefix('--')} = {value}")
    return "\n".join(lines) + "\n"


def render_command(
    profile: ModelProfile,
    executable: Path,
    *,
    platform: str | None = None,
) -> str:
    """Render a readable, non-executed command from the structured profile."""
    selected_platform = platform or os.name
    arguments: list[str] = [str(executable)]
    for option in profile.options():
        arguments.append(f"--{option.name.removeprefix('--')}")
        if option.value is not True:
            value = (
                str(option.value).lower()
                if isinstance(option.value, bool)
                else str(option.value)
            )
            arguments.append(value)
    if selected_platform == "nt":
        return subprocess.list2cmdline(arguments)
    return " ".join(shlex.quote(argument) for argument in arguments)


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
    destination: Path, profile: ModelProfile, capabilities: RuntimeCapabilities
) -> None:
    content = render_preset(profile, capabilities)
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
