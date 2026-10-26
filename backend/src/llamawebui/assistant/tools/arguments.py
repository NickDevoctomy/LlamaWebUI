"""Tools for looking up llama-server arguments in the bundled catalog."""

from __future__ import annotations

from collections.abc import Mapping

from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.domain.argument_catalog import ArgumentDefinition

SEARCH_LIMIT = 20


def _brief(definition: ArgumentDefinition) -> dict[str, object]:
    return {
        "id": definition.id,
        "flag": definition.flag,
        "label": definition.label,
        "category": definition.category,
        "type": definition.value.type,
        "default": definition.value.default,
        "summary": definition.help.summary,
        "control": definition.control,
    }


def _score(definition: ArgumentDefinition, terms: list[str]) -> int:
    """How well an argument matches: names count more than its help text."""
    names = " ".join((definition.id, definition.flag, *definition.aliases, definition.label))
    text = " ".join(
        (definition.category, definition.help.summary, definition.help.details or "")
    ).lower()
    score = 0
    for term in terms:
        if term in names.lower():
            score += 3
        elif term in text:
            score += 1
        else:
            return 0
    return score


async def search_arguments(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    query = text_argument(arguments, "query")
    terms = query.lower().split()
    ranked = sorted(
        (
            (score, definition)
            for definition in context.services.catalog.arguments
            if (score := _score(definition, terms))
        ),
        key=lambda item: (-item[0], item[1].common_rank or 10_000, item[1].id),
    )
    matches = [_brief(definition) for _, definition in ranked[:SEARCH_LIMIT]]
    return json_result(
        {"matches": matches, "total": len(ranked)}, f"{len(ranked)} matches for {query!r}"
    )


async def get_argument(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    reference = text_argument(arguments, "argument")
    catalog = context.services.catalog
    definition = catalog.get(reference)
    if definition is None and (match := catalog.resolve(reference)) is not None:
        definition = match.definition
    if definition is None:
        raise ToolError(f"no catalog argument has the id or flag {reference!r}")
    payload = definition.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return json_result(payload, definition.flag)


ARGUMENT_TOOLS = (
    Tool(
        "search_arguments",
        "Search llama-server's arguments by words in their name, flag or help, e.g. "
        "'kv cache type' or 'gpu layers'. Returns up to 20 matches with a summary each.",
        object_schema(
            {"query": {"type": "string", "description": "Words to search for."}}, ["query"]
        ),
        "profiles.read",
        search_arguments,
    ),
    Tool(
        "get_argument",
        "One llama-server argument in full: allowed values, default, help, effects, "
        "conflicts and requirements. Look up an argument before changing it.",
        object_schema(
            {
                "argument": {
                    "type": "string",
                    "description": "The catalog id (e.g. ctx-size) or a flag (e.g. --ctx-size).",
                }
            },
            ["argument"],
        ),
        "profiles.read",
        get_argument,
    ),
)
