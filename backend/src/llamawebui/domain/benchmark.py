"""Bundled benchmark prompts: one plain-text prompt per file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

BENCHMARK_DIRECTORY = Path(__file__).resolve().parent.parent / "benchmarks"
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class BenchmarkError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Benchmark:
    id: str
    title: str
    prompt: str

    def to_payload(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title, "prompt": self.prompt}


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


def load_benchmarks(directory: Path = BENCHMARK_DIRECTORY) -> tuple[Benchmark, ...]:
    """Load every ``*.txt`` prompt; the file name, without extension, is its id."""
    return tuple(
        Benchmark(id=identifier, title=title, prompt=text)
        for identifier, title, text in _read_prompts(directory, "benchmark prompt")
    )


def load_system_prompts(
    directory: Path = BENCHMARK_DIRECTORY / "system",
) -> tuple[SystemPrompt, ...]:
    """Load the system prompts a benchmark can be run with, from ``benchmarks/system``."""
    return tuple(
        SystemPrompt(id=identifier, title=title, text=text)
        for identifier, title, text in _read_prompts(directory, "system prompt")
    )
