"""Tools that search the web, read pages, and keep the research and report libraries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from llamawebui.assistant.library import (
    Author,
    DocumentLibrary,
    LibraryError,
    document_summary,
)
from llamawebui.assistant.tools import (
    Handler,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.assistant.web import WebError

# How much of a page one fetch_url call returns; longer pages are read in parts.
PAGE_CHARACTERS = 16_000


# Said with an answer that cites no web pages.
UNSOURCED = (
    "No web results came back, so this answer is only the search model's own knowledge: it "
    "may be out of date or wrong, and any [1] style citations in it are made up. Do not "
    "cite it or record it as a source. Search again with other words, or fetch a page you "
    "know, such as the llama.cpp README or the model card."
)


async def web_search(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    query = text_argument(arguments, "query")
    try:
        result = await context.services.web.search(query)
    except WebError as error:
        raise ToolError(str(error)) from error
    count = len(result.sources)
    payload: dict[str, object] = {"answer": result.answer, "sources": result.sources}
    if "<tool_call>" in result.answer or "<function=" in result.answer:
        # The search model tried to call a tool instead of answering.
        payload["answer"] = ""
        payload["warning"] = "The search failed: no answer came back. Try different words."
    elif not count:
        payload["warning"] = UNSOURCED
    return ToolResult(
        json_result(payload).text,
        f"{count} {'source' if count == 1 else 'sources'} for {query!r}",
        cost_usd=result.cost_usd,
    )


async def fetch_url(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    url = text_argument(arguments, "url")
    offset = arguments.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ToolError("offset must be a whole number of characters, 0 or more")
    try:
        page = await context.services.web.fetch(url)
    except WebError as error:
        raise ToolError(str(error)) from error
    part = page.text[offset : offset + PAGE_CHARACTERS]
    end = offset + len(part)
    return json_result(
        {
            "url": page.url,
            "title": page.title,
            "content_type": page.content_type,
            "total_characters": len(page.text),
            "offset": offset,
            "next_offset": end if end < len(page.text) else None,
            "text": part,
        },
        page.title or page.url,
    )


def _library(context: ToolContext, kind: str) -> DocumentLibrary[Any]:
    services = context.services
    return services.research if kind == "research" else services.reports


def _search(kind: str) -> Handler:
    async def search(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
        query = text_argument(arguments, "query", required=False)
        matches = _library(context, kind).search(query)
        found = [{**document_summary(document), "score": score} for document, score in matches]
        return json_result(
            found, f"{len(matches)} {NOUNS[kind]}" + (f" for {query!r}" if query else "")
        )

    return search


def _read(kind: str) -> Handler:
    async def read(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
        try:
            document = _library(context, kind).get(text_argument(arguments, "slug"))
        except LibraryError as error:
            raise ToolError(str(error)) from error
        return ToolResult(document.text(), document.meta.title)

    return read


def _write(kind: str) -> Handler:
    async def write(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
        fields = {key: value for key, value in arguments.items() if key != "body"}
        body = arguments.get("body")
        if not isinstance(body, str):
            raise ToolError("body is required and must be markdown text")
        try:
            result = _library(context, kind).write(
                fields,
                body,
                author=Author(
                    model=context.model_id or None, conversation=context.conversation_id or None
                ),
                publish=context.services.auto_publish(),
            )
        except LibraryError as error:
            raise ToolError(str(error)) from error
        slug = result.document.meta.slug
        payload: dict[str, object] = {
            "slug": slug,
            "created": result.created,
            "published": result.published,
        }
        if result.publish_problem:
            payload["publish_problem"] = result.publish_problem
        return json_result(payload, f"{'wrote' if result.created else 'updated'} {slug}")

    return write


NOUNS = {"research": "research documents", "reports": "reports"}

RESULT = {
    "type": "object",
    "properties": {
        "metric": {"type": "string"},
        "before": {"type": "number"},
        "after": {"type": "number"},
        "unit": {"type": "string"},
    },
    "required": ["metric"],
}

SOURCE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "url": {"type": "string", "description": "Leave out for your own measurements."},
        "accessed": {"type": "string", "description": "YYYY-MM-DD"},
    },
    "required": ["title"],
}

KNOWLEDGE_TOOLS = (
    Tool(
        "web_search",
        "Search the web. Returns a short answer with its sources. Costs a little per search, "
        "so check the research library first and search with specific questions.",
        object_schema({"query": {"type": "string"}}, ["query"]),
        "assistant.write",
        web_search,
    ),
    Tool(
        "fetch_url",
        "Read a public web page as text, for example a source from web_search, a llama.cpp "
        "pull request or a model card. Long pages come in parts: pass next_offset as offset "
        "to read on.",
        object_schema(
            {"url": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}, ["url"]
        ),
        "assistant.read",
        fetch_url,
    ),
    Tool(
        "search_research",
        "Search the research library: what earlier work found about models, hardware and "
        "llama.cpp settings. An empty query lists the newest documents.",
        object_schema({"query": {"type": "string"}}),
        "assistant.read",
        _search("research"),
    ),
    Tool(
        "read_research",
        "Read a research document in full, with its sources.",
        object_schema({"slug": {"type": "string"}}, ["slug"]),
        "assistant.read",
        _read("research"),
    ),
    Tool(
        "write_research",
        "Save what you learned as a research document, or update one by reusing its slug. "
        "Write knowledge that will help later work, not a log of this conversation.",
        object_schema(
            {
                "slug": {"type": "string", "description": "lowercase-words-with-hyphens"},
                "title": {"type": "string"},
                "summary": {"type": "string", "description": "One or two sentences."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "sources": {"type": "array", "items": SOURCE, "minItems": 1},
                "applies_to": {
                    "type": "object",
                    "properties": {
                        "models": {"type": "array", "items": {"type": "string"}},
                        "hardware": {"type": "array", "items": {"type": "string"}},
                        "runtime": {"type": "string", "description": "e.g. a llama.cpp build"},
                    },
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "body": {"type": "string", "description": "The findings, in markdown."},
            },
            ["slug", "title", "summary", "sources", "confidence", "body"],
        ),
        "assistant.write",
        _write("research"),
    ),
    Tool(
        "search_reports",
        "Search reports of earlier optimisation work: what was tried on which profile or "
        "model, and the results. An empty query lists the newest.",
        object_schema({"query": {"type": "string"}}),
        "assistant.read",
        _search("reports"),
    ),
    Tool(
        "read_report",
        "Read a report in full: the changes tried, the results and the charts.",
        object_schema({"slug": {"type": "string"}}, ["slug"]),
        "assistant.read",
        _read("reports"),
    ),
    Tool(
        "write_report",
        "Write the report of this work when you finish, or update it by reusing its slug. "
        "Record the starting point, every change tried with its measured result, the final "
        "settings and what to try next. Charts go in the body as fenced ```chart blocks "
        'holding JSON: {"type": "line" or "bar", "title", "categories": '
        '[x labels], "series": [{"name", "values": [one number or null per '
        'category]}], "y_label", "x_label" (optional), "targets": [{"label", '
        '"value"}] (optional)}.',
        object_schema(
            {
                "slug": {"type": "string", "description": "lowercase-words-with-hyphens"},
                "title": {"type": "string"},
                "summary": {"type": "string", "description": "One or two sentences."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "subject": {
                    "type": "object",
                    "properties": {"profile": {"type": "string"}, "model": {"type": "string"}},
                },
                "hardware": {"type": "array", "items": {"type": "string"}},
                "runtime": {"type": "string"},
                "benchmark": {"type": "string"},
                "outcome": {
                    "type": "string",
                    "enum": ["improved", "unchanged", "worse", "failed", "incomplete"],
                },
                "results": {"type": "array", "items": RESULT},
                "alternatives": {
                    "type": "array",
                    "description": "Setups measured and kept as options without replacing "
                    "the subject, such as a clone on a smaller quant.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "profile": {"type": "string"},
                            "summary": {"type": "string"},
                            "results": {"type": "array", "items": RESULT},
                        },
                        "required": ["name"],
                    },
                },
                "left_behind": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What you left on the machine for the operator to keep or "
                    "remove: test profiles, downloads, runtimes.",
                },
                "body": {"type": "string", "description": "The report, in markdown."},
            },
            ["slug", "title", "summary", "outcome", "body"],
        ),
        "assistant.write",
        _write("reports"),
    ),
)
