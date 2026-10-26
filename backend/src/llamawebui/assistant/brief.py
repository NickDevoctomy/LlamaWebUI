"""The optimisation brief: what a conversation works on, and the targets it aims for."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Target = Literal["decode", "prefill", "load", "memory"]
DEFAULT_PRIORITY: tuple[Target, ...] = ("decode", "prefill", "load", "memory")


class BriefModel(BaseModel):
    """A model to get working: a Hugging Face repository, and a quantization if wanted."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(min_length=3, max_length=400)
    quantization: str | None = Field(default=None, max_length=100)


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Literal["model", "profile"]
    model: BriefModel | None = None
    profile_id: str | None = Field(default=None, max_length=36)
    max_load_seconds: float | None = Field(default=None, gt=0, le=3600)
    min_prefill_tps: float | None = Field(default=None, gt=0, le=1_000_000)
    min_decode_tps: float | None = Field(default=None, gt=0, le=100_000)
    max_ram_gb: float | None = Field(default=None, gt=0, le=65_536)
    max_vram_gb: float | None = Field(default=None, gt=0, le=65_536)
    min_context_tokens: int | None = Field(default=None, ge=256, le=100_000_000)
    parallel_slots: int = Field(default=1, ge=1, le=256)
    # None until the API fills in the default benchmark.
    benchmark_id: str | None = Field(default=None, max_length=64)
    reasoning_level: str | None = Field(default=None, max_length=16)
    priority: list[Target] = Field(default_factory=lambda: list(DEFAULT_PRIORITY))

    @model_validator(mode="after")
    def _one_subject(self) -> Self:
        if self.subject == "model" and (self.model is None or self.profile_id is not None):
            raise ValueError("a model brief names a model and no profile")
        if self.subject == "profile" and (self.profile_id is None or self.model is not None):
            raise ValueError("a profile brief names a profile and no model")
        if sorted(self.priority) != sorted(DEFAULT_PRIORITY):
            raise ValueError(f"priority must list each of {', '.join(DEFAULT_PRIORITY)} once")
        return self


def brief_text(brief: Brief, *, profile_alias: str | None, benchmark_title: str) -> str:
    """The brief as the agent reads it in its system prompt."""
    if brief.model is not None:
        subject = f"Get this model working and optimise it: Hugging Face `{brief.model.repo_id}`"
        if brief.model.quantization:
            subject += f", quantization {brief.model.quantization}"
    else:
        subject = f"Optimise the existing profile `{profile_alias or brief.profile_id}`"
    targets = [
        f"- Model load time at most {brief.max_load_seconds:g} s"
        if brief.max_load_seconds is not None
        else None,
        f"- Average prefill (prompt processing) at least {brief.min_prefill_tps:g} tokens/s"
        if brief.min_prefill_tps is not None
        else None,
        f"- Average decode at least {brief.min_decode_tps:g} tokens/s"
        if brief.min_decode_tps is not None
        else None,
        f"- RAM use at most {brief.max_ram_gb:g} GB" if brief.max_ram_gb is not None else None,
        f"- VRAM use at most {brief.max_vram_gb:g} GB" if brief.max_vram_gb is not None else None,
        f"- Context length at least {brief.min_context_tokens} tokens; never go below it to "
        "meet another target"
        if brief.min_context_tokens is not None
        else None,
        f"- Must serve {brief.parallel_slots} requests at once (parallel slots)"
        if brief.parallel_slots > 1
        else None,
    ]
    lines = [subject, "", "Targets:"]
    chosen = [target for target in targets if target]
    lines.extend(chosen or ["- None set: improve speed and memory use without breaking the model."])
    reasoning = f", reasoning level {brief.reasoning_level}" if brief.reasoning_level else ""
    lines.extend(
        [
            "",
            f"Judge every result with the benchmark \"{benchmark_title}\"{reasoning}.",
            "When targets conflict, favour them in this order: "
            + ", ".join(brief.priority)
            + ".",
        ]
    )
    return "\n".join(lines)
