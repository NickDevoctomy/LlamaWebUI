"""Libraries of markdown documents with YAML frontmatter: research documents and reports.

Documents shipped with the app are read-only. The assistant writes new ones to the data
folder, and a local document replaces a shipped one with the same slug. Publishing moves
local documents into the shipped folder of a source checkout, for the maintainer to
review and commit, so later releases start with them.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LOGGER = logging.getLogger(__name__)

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
MAX_BODY_CHARACTERS = 60_000
# Documents listed in the system prompt; the rest are found by searching.
INDEX_LIMIT = 40
_WORD = re.compile(r"[a-z0-9][a-z0-9.+_-]*")

Origin = Literal["shipped", "local"]


class LibraryError(ValueError):
    """A document that is invalid, missing, or cannot be changed."""


class DocumentNotFoundError(LibraryError):
    pass


class Author(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=300)
    conversation: str | None = Field(default=None, max_length=36)


class LibraryMeta(BaseModel):
    """The frontmatter every document has; each library adds its own fields."""

    model_config = ConfigDict(extra="forbid")

    # The frontmatter field holding when the document was first written.
    FIRST_DATE: ClassVar[str]

    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=80, pattern=SLUG_PATTERN)
    summary: str = Field(min_length=1, max_length=600)
    tags: list[str] = Field(default_factory=list, max_length=12)
    updated: date
    author: Author | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        tags = [tag.strip().lower() for tag in value if tag.strip()]
        if any(len(tag) > 40 for tag in tags):
            raise ValueError("a tag is at most 40 characters")
        return list(dict.fromkeys(tags))

    def first_date(self) -> date:
        value = getattr(self, self.FIRST_DATE)
        assert isinstance(value, date)
        return value

    def search_text(self) -> str:
        """More words a search should find the document by."""
        return ""

    def index_details(self) -> str:
        """What the system prompt's index says after the tags."""
        return ""

    @classmethod
    def check_body(cls, body: str) -> None:
        """Raise ValueError if the body is not acceptable for this kind of document."""


@dataclass(frozen=True, slots=True)
class LibraryDocument[M: LibraryMeta]:
    meta: M
    body: str
    origin: Origin
    # A local document that takes the place of a shipped one with the same slug.
    replaces_shipped: bool = False

    def text(self) -> str:
        return render_document(self.meta, self.body)


@dataclass(frozen=True, slots=True)
class WriteResult[M: LibraryMeta]:
    document: LibraryDocument[M]
    created: bool
    # Written straight into the shipped folder, because auto-publish is on.
    published: bool = False
    # Why it was not published although auto-publish is on.
    publish_problem: str | None = None


def validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'document'}: {item['msg']}"
        for item in error.errors()
    )


def parse_document[M: LibraryMeta](text: str, meta_model: type[M]) -> tuple[M, str]:
    """A document's frontmatter and body; raises LibraryError if either is invalid."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise LibraryError("a document starts with --- and YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as error:
        raise LibraryError("the frontmatter is not closed with ---") from error
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        raise LibraryError(f"the frontmatter is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise LibraryError("the frontmatter must be a YAML mapping")
    try:
        meta = meta_model.model_validate(data)
    except ValidationError as error:
        raise LibraryError(validation_message(error)) from error
    return meta, "\n".join(lines[end + 1 :]).strip()


def render_document(meta: LibraryMeta, body: str) -> str:
    frontmatter = yaml.safe_dump(
        meta.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    return f"---\n{frontmatter}---\n\n{body.strip()}\n"


def source_checkout_problem(directory: Path) -> str | None:
    """Why documents cannot be published into ``directory``, or None if they can."""
    if not any((parent / ".git").exists() for parent in directory.parents):
        return "publishing needs LlamaWebUI to run from a source checkout"
    if not directory.is_dir() or not os.access(directory, os.W_OK):
        return f"the folder is not writable: {directory}"
    return None


class DocumentLibrary[M: LibraryMeta]:
    def __init__(
        self,
        local: Path,
        shipped: Path,
        meta_model: type[M],
        *,
        noun: str,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._local = local
        self._shipped = shipped
        self._meta_model = meta_model
        # What one document is called in messages, e.g. "research document".
        self._noun = noun
        self._today = today

    @property
    def shipped_directory(self) -> Path:
        return self._shipped

    def _load(self, directory: Path, origin: Origin) -> dict[str, LibraryDocument[M]]:
        documents: dict[str, LibraryDocument[M]] = {}
        if not directory.is_dir():
            return documents
        for path in sorted(directory.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            try:
                meta, body = parse_document(path.read_text(encoding="utf-8"), self._meta_model)
            except (OSError, UnicodeDecodeError, LibraryError) as error:
                LOGGER.warning(
                    "library_document_skipped", extra={"path": str(path), "error": str(error)}
                )
                continue
            documents[meta.slug] = LibraryDocument(meta, body, origin)
        return documents

    def documents(self) -> list[LibraryDocument[M]]:
        """Every document, newest first; a local one replaces a shipped one."""
        shipped = self._load(self._shipped, "shipped")
        merged = dict(shipped)
        for slug, document in self._load(self._local, "local").items():
            merged[slug] = LibraryDocument(
                document.meta, document.body, "local", replaces_shipped=slug in shipped
            )
        return sorted(
            merged.values(), key=lambda item: (item.meta.updated, item.meta.slug), reverse=True
        )

    def get(self, slug: str) -> LibraryDocument[M]:
        document = next((item for item in self.documents() if item.meta.slug == slug), None)
        if document is None:
            raise DocumentNotFoundError(f"there is no {self._noun} {slug!r}")
        return document

    def search(self, query: str, limit: int = 10) -> list[tuple[LibraryDocument[M], int]]:
        """Documents matching the query's words, best first; every document if it is empty.

        A word scores most in the title and tags, then the summary, then the body.
        """
        words = set(_WORD.findall(query.lower()))
        documents = self.documents()
        if not words:
            return [(document, 0) for document in documents[:limit]]
        scored: list[tuple[LibraryDocument[M], int]] = []
        for document in documents:
            meta = document.meta
            fields = (
                (3, meta.title.lower()),
                (3, " ".join(meta.tags)),
                (2, meta.summary.lower()),
                (2, meta.search_text().lower()),
                (1, document.body.lower()),
            )
            score = sum(weight for word in words for weight, text in fields if word in text)
            if score:
                scored.append((document, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def write(
        self,
        fields: Mapping[str, object],
        body: str,
        *,
        author: Author | None = None,
        publish: bool = False,
    ) -> WriteResult[M]:
        """Save a document, locally or (``publish``) straight into the shipped folder.

        Rewriting a slug keeps the date it was first written.
        """
        if not body.strip():
            raise LibraryError("the document needs a body")
        if len(body) > MAX_BODY_CHARACTERS:
            raise LibraryError(f"the body is longer than {MAX_BODY_CHARACTERS} characters")
        try:
            self._meta_model.check_body(body)
        except ValueError as error:
            raise LibraryError(str(error)) from error
        slug = fields.get("slug")
        existing = None
        if isinstance(slug, str):
            try:
                existing = self.get(slug)
            except DocumentNotFoundError:
                existing = None
        today = self._today()
        first = self._meta_model.FIRST_DATE
        try:
            meta = self._meta_model.model_validate(
                {
                    **{key: value for key, value in fields.items() if key != first},
                    first: existing.meta.first_date() if existing else today,
                    "updated": today,
                    "author": author.model_dump() if author else None,
                }
            )
        except ValidationError as error:
            raise LibraryError(validation_message(error)) from error
        problem = self.publish_problem() if publish else None
        published = publish and problem is None
        directory = self._shipped if published else self._local
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{meta.slug}.md"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(render_document(meta, body), encoding="utf-8")
        temporary.replace(path)
        if published:
            (self._local / f"{meta.slug}.md").unlink(missing_ok=True)
        return WriteResult(self.get(meta.slug), existing is None, published, problem)

    def delete(self, slug: str) -> None:
        """Delete a local document; shipped ones are read-only."""
        path = self._local / f"{slug}.md"
        if self.get(slug).origin != "local" or not path.is_file():
            raise LibraryError(f"{slug} ships with LlamaWebUI and cannot be deleted here")
        path.unlink()

    def publish_problem(self) -> str | None:
        return source_checkout_problem(self._shipped)

    def publish(self, slugs: Sequence[str]) -> list[str]:
        """Move local documents into the shipped folder; all are checked before any move."""
        problem = self.publish_problem()
        if problem:
            raise LibraryError(problem)
        documents = [self.get(slug) for slug in dict.fromkeys(slugs)]
        shipped = [item.meta.slug for item in documents if item.origin != "local"]
        if shipped:
            raise LibraryError(f"already published: {', '.join(shipped)}")
        for document in documents:
            (self._shipped / f"{document.meta.slug}.md").write_text(
                document.text(), encoding="utf-8"
            )
            (self._local / f"{document.meta.slug}.md").unlink()
        return [item.meta.slug for item in documents]

    def index_text(self, limit: int = INDEX_LIMIT, *, empty: str = "None yet.") -> str:
        """The documents, one line each, for the system prompt."""
        documents = self.documents()
        if not documents:
            return empty
        lines = []
        for item in documents[:limit]:
            meta = item.meta
            details = "; ".join(
                part
                for part in (
                    f"tags: {', '.join(meta.tags) or 'none'}",
                    f"updated {meta.updated}",
                    meta.index_details(),
                )
                if part
            )
            lines.append(f"- `{meta.slug}`: {meta.title}. {meta.summary} ({details})")
        if len(documents) > limit:
            lines.append(f"- … and {len(documents) - limit} more; search to find them.")
        return "\n".join(lines)


def document_summary(document: LibraryDocument[LibraryMeta]) -> dict[str, object]:
    """A document for listing: its frontmatter and where it comes from."""
    return {
        **document.meta.model_dump(mode="json"),
        "origin": document.origin,
        "replaces_shipped": document.replaces_shipped,
    }


def document_payload(document: LibraryDocument[LibraryMeta]) -> dict[str, object]:
    return {**document_summary(document), "body": document.body}
