"""Pure parsing logic for runtime-provided llama.cpp capability output."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

ROUTER_REQUIRED_OPTIONS = frozenset({"models-preset"})

_OPTION_PATTERN = re.compile(r"(?<!\w)--([a-z][a-z0-9-]*)")
_BUILD_PATTERN = re.compile(r"\b(?:build|version)\s*[:=]?\s*([\w.-]+)", re.IGNORECASE)
_COMMIT_PATTERN = re.compile(r"\bcommit\s*[:=]?\s*([0-9a-f]{7,40})\b", re.IGNORECASE)
_PAREN_COMMIT_PATTERN = re.compile(r"\(([0-9a-f]{7,40})\)", re.IGNORECASE)
_HELP_FORM_PATTERN = re.compile(
    r"^(?P<flag>-{1,2}[A-Za-z0-9][A-Za-z0-9-]*)(?:[ =](?P<value>.+))?$"
)
_HELP_DEFAULT_PATTERN = re.compile(
    r"\(default:\s*(?P<value>[^)]*)\)|default:\s*(?P<bare>[^,)]+)"
)
# Option rows start near the left margin; wrapped descriptions are indented to a column.
_HELP_OPTION_MAX_INDENT = 12


@dataclass(frozen=True, slots=True)
class RuntimeOption:
    """One option row parsed from ``llama-server --help``."""

    flag: str
    aliases: tuple[str, ...]
    syntax: str
    help: str
    default: str | None = None

    @property
    def flags(self) -> tuple[str, ...]:
        return (self.flag, *self.aliases)

    def to_payload(self) -> dict[str, object]:
        return {
            "flag": self.flag,
            "aliases": list(self.aliases),
            "syntax": self.syntax,
            "help": self.help,
            "default": self.default,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> RuntimeOption:
        aliases = payload.get("aliases")
        default = payload.get("default")
        return cls(
            flag=str(payload.get("flag", "")),
            aliases=tuple(str(alias) for alias in aliases) if isinstance(aliases, list) else (),
            syntax=str(payload.get("syntax", "")),
            help=str(payload.get("help", "")),
            default=default if isinstance(default, str) else None,
        )


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Capabilities advertised by a specific llama-server executable."""

    options: frozenset[str]
    raw_help: str
    details: tuple[RuntimeOption, ...] = ()

    @property
    def help_parsed(self) -> bool:
        """Whether ``--help`` produced usable option names for compatibility checks."""

        return bool(self.options)

    @property
    def flags_sha256(self) -> str:
        """Fingerprint of the advertised flag surface, independent of help wording."""

        return flags_fingerprint(self.options, self.details)

    def supports(self, option: str) -> bool:
        """Return whether a long option is advertised, accepting either dash form."""

        normalized = option.removeprefix("--").strip().lower()
        return normalized in self.options

    @property
    def missing_router_options(self) -> tuple[str, ...]:
        """Return required router options that this executable does not advertise."""

        return tuple(sorted(ROUTER_REQUIRED_OPTIONS - self.options))

    @property
    def router_compatible(self) -> bool:
        return not self.missing_router_options


@dataclass(frozen=True, slots=True)
class RuntimeVersion:
    """Best-effort normalized fields from version output."""

    build: str | None
    commit: str | None
    raw: str


def parse_help_output(output: str) -> RuntimeCapabilities:
    """Extract long option names and option rows while retaining output for diagnostics."""

    options = frozenset(match.group(1).lower() for match in _OPTION_PATTERN.finditer(output))
    return RuntimeCapabilities(options=options, raw_help=output, details=parse_help_options(output))


def parse_help_options(output: str) -> tuple[RuntimeOption, ...]:
    """Parse llama.cpp option rows into canonical flags, aliases, syntax, and help text."""

    parsed: list[RuntimeOption] = []
    current: tuple[list[str], str, list[str]] | None = None

    def finish() -> None:
        if current is None:
            return
        flags, syntax, text = current
        long_flags = [flag for flag in flags if flag.startswith("--")]
        positive = [flag for flag in long_flags if not flag.startswith("--no-")]
        canonical = (positive or long_flags or flags)[0]
        description = " ".join(part for part in text if part)
        default_match = _HELP_DEFAULT_PATTERN.search(description)
        default = None
        if default_match is not None:
            default = (default_match.group("value") or default_match.group("bare") or "").strip()
            default = default.strip("'\"") or None
        parsed.append(
            RuntimeOption(
                flag=canonical,
                aliases=tuple(flag for flag in flags if flag != canonical),
                syntax=syntax,
                help=description,
                default=default,
            )
        )

    for line in output.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("-") and not stripped.startswith("---") and (
            indent <= _HELP_OPTION_MAX_INDENT
        ):
            # Short aliases are padded after their comma; only split on a gap that ends the forms.
            parts = re.split(r"(?<![,\s])\s{2,}", stripped, maxsplit=1)
            forms_text = parts[0]
            description = parts[1] if len(parts) > 1 else ""
            flags: list[str] = []
            for form in (part.strip() for part in re.split(r",\s*(?=-)", forms_text)):
                match = _HELP_FORM_PATTERN.match(form)
                if match is None:
                    flags = []
                    break
                flags.append(match.group("flag"))
            if not flags:
                finish()
                current = None
                continue
            finish()
            current = (flags, " ".join(forms_text.split()), [description.strip()])
        elif current is not None and stripped and indent > _HELP_OPTION_MAX_INDENT:
            current[2].append(stripped)
        else:
            finish()
            current = None
    finish()
    return tuple(parsed)


def flags_fingerprint(options: frozenset[str], details: tuple[RuntimeOption, ...]) -> str:
    """Hash the sorted option names and alias groups as canonical JSON."""

    payload = {
        "options": sorted(options),
        "groups": sorted([option.flag, *sorted(option.aliases)] for option in details),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_version_output(output: str) -> RuntimeVersion:
    """Extract build and commit identifiers without assuming one output layout."""

    build_match = _BUILD_PATTERN.search(output)
    commit_match = _COMMIT_PATTERN.search(output) or _PAREN_COMMIT_PATTERN.search(output)
    return RuntimeVersion(
        build=build_match.group(1) if build_match else None,
        commit=commit_match.group(1).lower() if commit_match else None,
        raw=output,
    )
