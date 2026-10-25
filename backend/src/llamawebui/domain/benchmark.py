"""Bundled benchmark prompts: one plain-text prompt per file.

A benchmark prompt may start with YAML frontmatter between ``---`` lines. Its
``tasks`` key lists the tasks an agent with the planning tools starts with, and its
``typescript`` key makes it a TypeScript benchmark, where the agent writes TypeScript
modules and builds the page from them:

    typescript:
      strict: true   # optional; type checks are non-strict by default
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BENCHMARK_DIRECTORY = Path(__file__).resolve().parent.parent / "benchmarks"
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Frontmatter: YAML between a first line of --- and the next line of ---.
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*$", re.DOTALL | re.MULTILINE)
FRONTMATTER_KEYS = frozenset({"tasks", "typescript"})
TYPESCRIPT_KEYS = frozenset({"strict"})


class BenchmarkError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Benchmark:
    id: str
    title: str
    prompt: str
    # Tasks an agent with the planning tools starts with, from the file's frontmatter.
    tasks: tuple[str, ...] = field(default=())
    # None for a plain benchmark; for a TypeScript one, whether type checks are strict.
    typescript_strict: bool | None = None

    @property
    def typescript(self) -> bool:
        return self.typescript_strict is not None

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "tasks": list(self.tasks),
            "typescript": (
                {"strict": self.typescript_strict} if self.typescript_strict is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SystemPrompt:
    """Instructions sent ahead of a benchmark prompt, such as a coding agent's."""

    id: str
    title: str
    text: str

    def to_payload(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title, "text": self.text}


def _read_prompts(directory: Path, kind: str) -> list[tuple[str, str, str]]:
    """(id, title, text) for every ``*.txt`` file; the file name is the id."""
    if not directory.is_dir():
        return []
    prompts: list[tuple[str, str, str]] = []
    for path in sorted(directory.glob("*.txt")):
        identifier = path.stem.lower()
        if not _ID_PATTERN.fullmatch(identifier):
            raise BenchmarkError(f"{path.name}: invalid {kind} file name")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise BenchmarkError(f"{path.name}: {kind} is empty")
        title = re.sub(r"[-_.]+", " ", identifier).strip().capitalize()
        prompts.append((identifier, title, text))
    return prompts


def _split_frontmatter(name: str, text: str) -> tuple[dict[str, object], str]:
    """A prompt's frontmatter settings and the prompt after it; no frontmatter gives {}."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}, text
    try:
        settings = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise BenchmarkError(f"{name}: invalid frontmatter: {error}") from error
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise BenchmarkError(f"{name}: frontmatter must be a mapping of settings")
    unknown = sorted(str(key) for key in settings if key not in FRONTMATTER_KEYS)
    if unknown:
        raise BenchmarkError(f"{name}: unknown frontmatter settings: {', '.join(unknown)}")
    body = text[match.end() :].strip()
    if not body:
        raise BenchmarkError(f"{name}: benchmark prompt is empty")
    return settings, body


def _tasks(name: str, value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise BenchmarkError(f"{name}: tasks must be a list of task descriptions")
    return tuple(" ".join(item.split()) for item in value)


def _typescript_strict(name: str, value: object) -> bool | None:
    """None when the key is absent; true or an empty mapping turns TypeScript on."""
    if value is None or value is False:
        return None
    if value is True:
        return False
    if not isinstance(value, dict):
        raise BenchmarkError(f"{name}: typescript must be true or a mapping of settings")
    unknown = sorted(str(key) for key in value if key not in TYPESCRIPT_KEYS)
    if unknown:
        raise BenchmarkError(f"{name}: unknown typescript settings: {', '.join(unknown)}")
    strict = value.get("strict", False)
    if not isinstance(strict, bool):
        raise BenchmarkError(f"{name}: typescript strict must be true or false")
    return strict


def load_benchmarks(directory: Path = BENCHMARK_DIRECTORY) -> tuple[Benchmark, ...]:
    """Load every ``*.txt`` prompt; the file name, without extension, is its id."""
    benchmarks: list[Benchmark] = []
    for identifier, title, text in _read_prompts(directory, "benchmark prompt"):
        name = f"{identifier}.txt"
        settings, prompt = _split_frontmatter(name, text)
        benchmarks.append(
            Benchmark(
                id=identifier,
                title=title,
                prompt=prompt,
                tasks=_tasks(name, settings.get("tasks")),
                typescript_strict=_typescript_strict(name, settings.get("typescript")),
            )
        )
    return tuple(benchmarks)


def load_system_prompts(
    directory: Path = BENCHMARK_DIRECTORY / "system",
) -> tuple[SystemPrompt, ...]:
    """Load the system prompts a benchmark can be run with, from ``benchmarks/system``."""
    return tuple(
        SystemPrompt(id=identifier, title=title, text=text)
        for identifier, title, text in _read_prompts(directory, "system prompt")
    )
