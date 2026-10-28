"""Reports: what one piece of optimisation work did and found, with charts.

A report records a run of work (the profile or model, the changes tried, the results
before and after) so later conversations can build on it instead of starting over. A
research document, by contrast, holds general knowledge with its sources. Charts are
fenced ```chart blocks of JSON in the body, checked when the report is written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from llamawebui.assistant.library import (
    DocumentLibrary,
    LibraryDocument,
    LibraryMeta,
    validation_message,
)

SHIPPED_DIRECTORY = Path(__file__).resolve().parent.parent / "reports"
_CHART_BLOCK = re.compile(r"^```chart[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)

Outcome = Literal["improved", "unchanged", "worse", "failed", "incomplete"]


class Series(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    values: list[float | None] = Field(min_length=1, max_length=60)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    value: float


class Chart(BaseModel):
    """A line or bar chart: one value per category for each series."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["line", "bar"]
    title: str = Field(min_length=1, max_length=120)
    # The x axis: e.g. the change each benchmark run measured.
    categories: list[str] = Field(min_length=1, max_length=60)
    series: list[Series] = Field(min_length=1, max_length=6)
    y_label: str = Field(min_length=1, max_length=60)
    x_label: str | None = Field(default=None, max_length=60)
    # Lines across the chart, such as the brief's targets.
    targets: list[Target] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _one_value_per_category(self) -> Chart:
        for series in self.series:
            if len(series.values) != len(self.categories):
                raise ValueError(
                    f"series {series.name!r} has {len(series.values)} values for "
                    f"{len(self.categories)} categories"
                )
        return self


class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=300)


class Result(BaseModel):
    """One headline figure, before and after the work."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=60)
    before: float | None = None
    after: float | None = None
    unit: str | None = Field(default=None, max_length=20)


class Alternative(BaseModel):
    """A setup measured along the way and kept as an option, without replacing the subject."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    profile: str | None = Field(default=None, max_length=64)
    summary: str = Field(default="", max_length=500)
    results: list[Result] = Field(default_factory=list, max_length=12)


def charts_in(body: str) -> list[Chart]:
    """The body's charts; raises ValueError naming the first one that is invalid."""
    charts = []
    for number, match in enumerate(_CHART_BLOCK.finditer(body), start=1):
        try:
            charts.append(Chart.model_validate(json.loads(match.group(1))))
        except json.JSONDecodeError as error:
            raise ValueError(f"chart {number} is not valid JSON: {error}") from error
        except ValidationError as error:
            raise ValueError(f"chart {number}: {validation_message(error)}") from error
    return charts


class ReportMeta(LibraryMeta):
    FIRST_DATE: ClassVar[str] = "created"

    created: date
    subject: Subject = Field(default_factory=Subject)
    hardware: list[str] = Field(default_factory=list, max_length=10)
    runtime: str | None = Field(default=None, max_length=100)
    benchmark: str | None = Field(default=None, max_length=100)
    outcome: Outcome
    results: list[Result] = Field(default_factory=list, max_length=12)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=6)
    # What the work left on the machine for the operator to keep or remove, such as
    # test profiles and downloads.
    left_behind: list[str] = Field(default_factory=list, max_length=20)

    def search_text(self) -> str:
        return " ".join(
            part
            for part in (self.subject.profile, self.subject.model, *self.hardware, self.outcome)
            if part
        )

    def index_details(self) -> str:
        subject = self.subject.profile or self.subject.model
        return f"outcome {self.outcome}" + (f"; {subject}" if subject else "")

    @classmethod
    def check_body(cls, body: str) -> None:
        charts_in(body)


ReportDocument = LibraryDocument[ReportMeta]


class ReportLibrary(DocumentLibrary[ReportMeta]):
    def __init__(
        self,
        local: Path,
        shipped: Path | None = None,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        # Read when built rather than bound as a default, so tests can point it elsewhere.
        super().__init__(
            local, shipped or SHIPPED_DIRECTORY, ReportMeta, noun="report", today=today
        )
