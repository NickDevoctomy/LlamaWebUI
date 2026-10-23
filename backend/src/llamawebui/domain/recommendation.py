"""Bundled recommended profiles: tuned settings for a model on known hardware."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from llamawebui.domain.model_manifest import GgufGroup

RECOMMENDATION_DIRECTORY = Path(__file__).resolve().parent.parent / "recommendations"
RECOMMENDATION_FORMAT = "llamawebui.recommendation.v1"
GIB = 1024**3

# Memory figures are compared with some slack: installed memory is never fully reported.
_FITS_RATIO = 0.92
_TIGHT_RATIO = 0.75
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")

Fit = Literal["fits", "tight", "insufficient"]


class RecommendationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    repo_id: str
    quantization: str
    revision: str | None


@dataclass(frozen=True, slots=True)
class TestedHardware:
    label: str
    cpu_pattern: str | None
    gpu_pattern: str | None
    memory_gb: int
    unified_memory: bool
    backend: str
    notes: str | None


@dataclass(frozen=True, slots=True)
class Recommendation:
    id: str
    title: str
    summary: str
    model: RecommendedModel
    tested_on: TestedHardware
    memory_gb: int
    backends: tuple[str, ...]
    vram_gb: int | None
    alias: str
    arguments: tuple[Mapping[str, object], ...]
    description: str | None = None
    # These settings need the machine to themselves; nothing loads beside the profile.
    exclusive: bool = False
    # Bumped whenever the bundled settings change, so added profiles can be upgraded.
    version: int = 1

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "model": {
                "repo_id": self.model.repo_id,
                "quantization": self.model.quantization,
                "revision": self.model.revision,
            },
            "tested_on": {
                "label": self.tested_on.label,
                "memory_gb": self.tested_on.memory_gb,
                "unified_memory": self.tested_on.unified_memory,
                "backend": self.tested_on.backend,
                "notes": self.tested_on.notes,
            },
            "requirements": {
                "memory_gb": self.memory_gb,
                "vram_gb": self.vram_gb,
                "backends": list(self.backends),
            },
            "profile": {
                "alias": self.alias,
                "description": self.description,
                "exclusive": self.exclusive,
                "arguments": [dict(item) for item in self.arguments],
            },
        }


@dataclass(frozen=True, slots=True)
class Assessment:
    fit: Fit
    hardware_match: bool
    available_memory_gb: float


def _text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecommendationError(f"recommendation field '{key}' must be a non-empty string")
    return value.strip()


def _optional_text(document: Mapping[str, object], key: str) -> str | None:
    return None if document.get(key) is None else _text(document, key)


def _section(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise RecommendationError(f"recommendation field '{key}' must be an object")
    return value


def _positive_int(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RecommendationError(f"recommendation field '{key}' must be a positive integer")
    return value


def parse_recommendation(document: Mapping[str, object]) -> Recommendation:
    if document.get("format") != RECOMMENDATION_FORMAT:
        raise RecommendationError("unsupported recommendation format")
    identifier = _text(document, "id")
    if not _ID_PATTERN.fullmatch(identifier):
        raise RecommendationError(f"invalid recommendation id: {identifier}")
    model = _section(document, "model")
    tested = _section(document, "tested_on")
    requirements = _section(document, "requirements")
    profile = _section(document, "profile")
    backends = requirements.get("backends")
    if not isinstance(backends, list) or not backends or not all(
        isinstance(item, str) and item for item in backends
    ):
        raise RecommendationError("requirements.backends must list at least one backend")
    arguments = profile.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(item, dict) for item in arguments):
        raise RecommendationError("profile.arguments must be a list of argument objects")
    exclusive = profile.get("exclusive", False)
    if not isinstance(exclusive, bool):
        raise RecommendationError("profile.exclusive must be true or false")
    patterns = {key: _optional_text(tested, key) for key in ("cpu_pattern", "gpu_pattern")}
    for key, pattern in patterns.items():
        if pattern is None:
            continue
        try:
            re.compile(pattern)
        except re.error as error:
            raise RecommendationError(f"tested_on.{key} is invalid: {error}") from error
    return Recommendation(
        id=identifier,
        title=_text(document, "title"),
        summary=_text(document, "summary"),
        model=RecommendedModel(
            repo_id=_text(model, "repo_id"),
            quantization=_text(model, "quantization"),
            revision=_optional_text(model, "revision"),
        ),
        tested_on=TestedHardware(
            label=_text(tested, "label"),
            cpu_pattern=patterns["cpu_pattern"],
            gpu_pattern=patterns["gpu_pattern"],
            memory_gb=_positive_int(tested, "memory_gb"),
            unified_memory=tested.get("unified_memory") is True,
            backend=_text(tested, "backend"),
            notes=_optional_text(tested, "notes"),
        ),
        memory_gb=_positive_int(requirements, "memory_gb"),
        backends=tuple(item.lower() for item in backends),
        vram_gb=(
            None
            if requirements.get("vram_gb") is None
            else _positive_int(requirements, "vram_gb")
        ),
        alias=_text(profile, "alias"),
        arguments=tuple(arguments),
        description=_optional_text(profile, "description"),
        exclusive=exclusive,
        version=1 if document.get("version") is None else _positive_int(document, "version"),
    )


def load_recommendations(directory: Path = RECOMMENDATION_DIRECTORY) -> tuple[Recommendation, ...]:
    """Load every bundled recommendation; a malformed file is a packaging bug and fails loudly."""
    if not directory.is_dir():
        return ()
    loaded: list[Recommendation] = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RecommendationError(f"{path.name}: {error}") from error
        if not isinstance(document, dict):
            raise RecommendationError(f"{path.name}: recommendation must be a JSON object")
        try:
            loaded.append(parse_recommendation(document))
        except RecommendationError as error:
            raise RecommendationError(f"{path.name}: {error}") from error
    for field, values in (
        ("ids", [item.id for item in loaded]),
        # Applying one recommendation must never collide with another's profile.
        ("profile aliases", [item.alias for item in loaded]),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise RecommendationError(f"duplicate recommendation {field}: {', '.join(duplicates)}")
    return tuple(loaded)


def assess(
    recommendation: Recommendation,
    *,
    cpu: str | None,
    memory_bytes: int,
    discrete_vram_bytes: int = 0,
    gpus: tuple[str, ...] = (),
) -> Assessment:
    """Compare a recommendation's memory needs and test machine with this host.

    The host matches when everything the recommendation declares holds: the processor
    name for ``cpu_pattern``, any graphics adapter for ``gpu_pattern``, and discrete
    VRAM for ``vram_gb``.
    """
    available = (memory_bytes + discrete_vram_bytes) / GIB
    ratio = available / recommendation.memory_gb
    fit: Fit = (
        "fits" if ratio >= _FITS_RATIO else "tight" if ratio >= _TIGHT_RATIO else "insufficient"
    )
    tested = recommendation.tested_on
    checks = []
    if tested.cpu_pattern is not None:
        checks.append(bool(cpu and re.search(tested.cpu_pattern, cpu, re.IGNORECASE)))
    if tested.gpu_pattern is not None:
        pattern = tested.gpu_pattern
        checks.append(any(re.search(pattern, gpu, re.IGNORECASE) for gpu in gpus))
    if recommendation.vram_gb is not None:
        checks.append(discrete_vram_bytes / GIB >= recommendation.vram_gb * _FITS_RATIO)
    hardware_match = bool(checks) and all(checks)
    return Assessment(fit=fit, hardware_match=hardware_match, available_memory_gb=available)


def select_group(groups: Iterable[GgufGroup], quantization: str) -> GgufGroup:
    """Pick the one complete model group for a quantization, ignoring projector files."""
    wanted = quantization.upper()
    candidates = [
        group
        for group in groups
        if (group.quantization or "").upper() == wanted and "mmproj" not in group.key.lower()
    ]
    if not candidates:
        raise RecommendationError(f"the repository has no {quantization} files")
    complete = [group for group in candidates if group.complete]
    if not complete:
        raise RecommendationError(f"the repository's {quantization} files are missing shards")
    if len(complete) > 1:
        keys = ", ".join(group.key for group in complete)
        raise RecommendationError(f"more than one {quantization} group matches: {keys}")
    return complete[0]
