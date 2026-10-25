"""Bundled recommended profiles: tuned settings for a model on known hardware."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
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
# The argument a recommendation's draft model is passed with.
DRAFT_MODEL_ARGUMENT = "spec-draft-model"

Fit = Literal["fits", "tight", "insufficient"]


class RecommendationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    repo_id: str
    quantization: str
    revision: str | None
    # A draft model group in the same repository, such as an MTP head. Applying the
    # recommendation downloads it too and points spec-draft-model at it.
    draft_group: str | None = None
    # The exact model group to use, for a repository with more than one group per
    # quantization (such as a plain and an MTP build).
    group: str | None = None


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
    # Variants of one recommendation share the base's id here; a plain one has its own.
    family: str = ""
    # The short name a variant is picked by, such as "16 GB VRAM".
    label: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "family": self.family or self.id,
            "label": self.label,
            "model": {
                "repo_id": self.model.repo_id,
                "quantization": self.model.quantization,
                "revision": self.model.revision,
                "draft_group": self.model.draft_group,
                "group": self.model.group,
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


def _merged_arguments(
    base: object, changes: Mapping[str, object]
) -> list[object]:
    """The base arguments with a variant's applied: same id replaces, new ids append."""
    if not isinstance(base, list):
        raise RecommendationError("profile.arguments must be a list of argument objects")
    removed = changes.get("remove_arguments", [])
    if not isinstance(removed, list) or not all(isinstance(item, str) for item in removed):
        raise RecommendationError("profile.remove_arguments must list argument ids")
    added = changes.get("arguments", [])
    if not isinstance(added, list) or not all(isinstance(item, dict) for item in added):
        raise RecommendationError("profile.arguments must be a list of argument objects")
    present = {item.get("id") for item in base if isinstance(item, dict)}
    unknown = sorted(set(removed) - present)
    if unknown:
        raise RecommendationError(f"profile.remove_arguments names no base argument: {unknown[0]}")
    replaced = {item.get("id"): item for item in added if item.get("id") is not None}
    merged = [
        replaced.pop(item.get("id"), item) if isinstance(item, dict) else item
        for item in base
        if not (isinstance(item, dict) and item.get("id") in removed)
    ]
    return merged + [item for item in added if item.get("id") is None or item.get("id") in replaced]


def _variant_document(
    base: Mapping[str, object], variant: Mapping[str, object]
) -> dict[str, object]:
    """A variant as a full document: the base with the variant's changes laid on top.

    Top-level text replaces the base's. ``model``, ``tested_on``, ``requirements`` and
    ``profile`` merge key by key, and profile arguments merge by id.
    """
    document = {key: value for key, value in base.items() if key != "variants"}
    for key, value in variant.items():
        if key in {"model", "tested_on", "requirements", "profile"}:
            if not isinstance(value, dict):
                raise RecommendationError(f"recommendation field '{key}' must be an object")
            merged = {**_section(base, key), **value}
            if key == "profile":
                merged.pop("remove_arguments", None)
                merged["arguments"] = _merged_arguments(_section(base, key).get("arguments"), value)
            document[key] = merged
        elif key in {"version", "variants"}:
            raise RecommendationError(f"a variant cannot set '{key}'; it shares its base's")
        else:
            document[key] = value
    return document


def parse_recommendations(document: Mapping[str, object]) -> tuple[Recommendation, ...]:
    """A recommendation file: the base and each of its variants."""
    base = parse_recommendation(document)
    variants = document.get("variants", [])
    if not isinstance(variants, list) or not all(isinstance(item, dict) for item in variants):
        raise RecommendationError("variants must be a list of objects")
    if variants and base.label is None:
        raise RecommendationError("a recommendation with variants needs a 'label'")
    parsed = [base]
    for index, variant in enumerate(variants):
        try:
            item = parse_recommendation(_variant_document(document, variant))
        except RecommendationError as error:
            raise RecommendationError(f"variants[{index}]: {error}") from error
        if item.id == base.id or variant.get("label") is None:
            raise RecommendationError(
                f"variants[{index}]: a variant needs its own 'id' and 'label'"
            )
        parsed.append(replace(item, family=base.id))
    return tuple(parsed)


def parse_recommendation(document: Mapping[str, object]) -> Recommendation:
    """One recommendation, ignoring any variants; see ``parse_recommendations``."""
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
    draft_group = _optional_text(model, "draft_group")
    if draft_group is not None and any(
        item.get("id") == DRAFT_MODEL_ARGUMENT for item in arguments
    ):
        raise RecommendationError(
            f"profile.arguments must not set {DRAFT_MODEL_ARGUMENT} when model.draft_group does"
        )
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
            draft_group=draft_group,
            group=_optional_text(model, "group"),
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
        family=identifier,
        label=_optional_text(document, "label"),
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
            loaded.extend(parse_recommendations(document))
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


def select_draft_group(groups: Iterable[GgufGroup], key: str) -> GgufGroup:
    """Pick the draft model group named by a recommendation, ignoring case."""
    match = next((group for group in groups if group.key.lower() == key.lower()), None)
    if match is None:
        raise RecommendationError(f"the repository has no draft model {key}")
    if not match.complete:
        raise RecommendationError(f"the repository's draft model {key} is missing shards")
    return match


def select_model_group(groups: Iterable[GgufGroup], model: RecommendedModel) -> GgufGroup:
    """The model group a recommendation names exactly, or the one for its quantization."""
    if model.group is None:
        return select_group(groups, model.quantization)
    match = next((group for group in groups if group.key.lower() == model.group.lower()), None)
    if match is None:
        raise RecommendationError(f"the repository has no model {model.group}")
    if not match.complete:
        raise RecommendationError(f"the repository's model {model.group} is missing shards")
    return match


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
