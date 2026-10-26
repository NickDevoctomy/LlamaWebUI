"""Research documents: what the assistant has learned about models, hardware and llama.cpp.

Each cites its sources, so a later reader can check it. See ``library`` for how documents
are stored, merged and published.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from llamawebui.assistant.library import (
    Author,
    DocumentLibrary,
    DocumentNotFoundError,
    LibraryDocument,
    LibraryError,
    LibraryMeta,
    document_payload,
    document_summary,
    render_document,
    source_checkout_problem,
)
from llamawebui.assistant.library import parse_document as parse_library_document

SHIPPED_DIRECTORY = Path(__file__).resolve().parent.parent / "research"

# The names the research tools and routes have used since before reports shared the library.
ResearchError = LibraryError
ResearchNotFoundError = DocumentNotFoundError

__all__ = [
    "Author",
    "ResearchDocument",
    "ResearchError",
    "ResearchLibrary",
    "ResearchMeta",
    "ResearchNotFoundError",
    "document_payload",
    "document_summary",
    "parse_document",
    "render_document",
    "source_checkout_problem",
]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    # A web page, or none for the assistant's own measurements (name the run in the title).
    url: str | None = Field(default=None, max_length=2000)
    accessed: date | None = None

    @field_validator("url")
    @classmethod
    def _web_address(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("a source url must start with http:// or https://")
        return value


class AppliesTo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(default_factory=list, max_length=20)
    hardware: list[str] = Field(default_factory=list, max_length=20)
    runtime: str | None = Field(default=None, max_length=100)


class ResearchMeta(LibraryMeta):
    FIRST_DATE: ClassVar[str] = "researched"

    researched: date
    sources: list[Source] = Field(min_length=1, max_length=40)
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    confidence: Literal["high", "medium", "low"]

    def search_text(self) -> str:
        return " ".join(self.applies_to.models + self.applies_to.hardware)

    def index_details(self) -> str:
        return f"confidence {self.confidence}"


ResearchDocument = LibraryDocument[ResearchMeta]


def parse_document(text: str) -> tuple[ResearchMeta, str]:
    return parse_library_document(text, ResearchMeta)


class ResearchLibrary(DocumentLibrary[ResearchMeta]):
    def __init__(
        self,
        local: Path,
        shipped: Path | None = None,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        # Read when built rather than bound as a default, so tests can point it elsewhere.
        super().__init__(
            local,
            shipped or SHIPPED_DIRECTORY,
            ResearchMeta,
            noun="research document",
            today=today,
        )
