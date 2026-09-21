"""Pure parsing logic for runtime-provided llama.cpp capability output."""

from __future__ import annotations

import re
from dataclasses import dataclass

ROUTER_REQUIRED_OPTIONS = frozenset({"models-preset"})

_OPTION_PATTERN = re.compile(r"(?<!\w)--([a-z][a-z0-9-]*)")
_BUILD_PATTERN = re.compile(r"\b(?:build|version)\s*[:=]?\s*([\w.-]+)", re.IGNORECASE)
_COMMIT_PATTERN = re.compile(r"\bcommit\s*[:=]?\s*([0-9a-f]{7,40})\b", re.IGNORECASE)
_PAREN_COMMIT_PATTERN = re.compile(r"\(([0-9a-f]{7,40})\)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Capabilities advertised by a specific llama-server executable."""

    options: frozenset[str]
    raw_help: str

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
    """Extract long option names while retaining output for diagnostics."""

    options = frozenset(match.group(1).lower() for match in _OPTION_PATTERN.finditer(output))
    return RuntimeCapabilities(options=options, raw_help=output)


def parse_version_output(output: str) -> RuntimeVersion:
    """Extract build and commit identifiers without assuming one output layout."""

    build_match = _BUILD_PATTERN.search(output)
    commit_match = _COMMIT_PATTERN.search(output) or _PAREN_COMMIT_PATTERN.search(output)
    return RuntimeVersion(
        build=build_match.group(1) if build_match else None,
        commit=commit_match.group(1).lower() if commit_match else None,
        raw=output,
    )
