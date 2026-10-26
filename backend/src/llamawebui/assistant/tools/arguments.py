"""Tools for looking up llama-server arguments in the bundled catalog."""

from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import get_close_matches

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


_WORD = re.compile(r"[a-z0-9_.]+")
# Words that mean nothing on their own in a search.
_STOPWORDS = frozenset(
    {"a", "an", "and", "the", "of", "for", "to", "in", "on", "with", "or", "size"}
)
# Common names for things the catalog words differently.
_SYNONYMS = {
    "mtp": ("speculative", "draft"),
    "speculation": ("speculative",),
    "kv": ("cache",),
    "ctx": ("context",),
    "context": ("ctx",),
    "offload": ("gpu", "layers"),
    "ngl": ("gpu", "layers"),
    "vram": ("gpu",),
    "fa": ("flash",),
    "lookup": ("ngram",),
    "batching": ("batch",),
    "parallel": ("slots",),
    "slots": ("parallel",),
    "threads": ("thread",),
}


def _words(text: str) -> set[str]:
    words = set(_WORD.findall(text.lower().replace("-", " ")))
    return words | {word.removesuffix("s") for word in words if len(word) > 3}


def _terms(query: str) -> list[tuple[str, ...]]:
    """The query's words, each with the alternatives that also count as a match."""
    words = [
        word for word in _WORD.findall(query.lower().replace("-", " ")) if word not in _STOPWORDS
    ]
    return [
        (word, word.removesuffix("s"), *_SYNONYMS.get(word, ())) for word in dict.fromkeys(words)
    ]


def _match(term: tuple[str, ...], words: set[str], text: str) -> int:
    """4 for a whole word, 3 for part of one, 2 for a near miss (a typo); 0 for none."""
    if any(alternative in words for alternative in term):
        return 4
    if any(len(alternative) >= 3 and alternative in text for alternative in term):
        return 3
    if len(term[0]) >= 4 and get_close_matches(
        term[0], [word for word in words if len(word) >= 4], n=1, cutoff=0.8
    ):
        return 2
    return 0


def _score(definition: ArgumentDefinition, terms: list[tuple[str, ...]]) -> tuple[int, int]:
    """(score, terms matched): words in the name count double those in the help text.

    Every term adds what it matches, so an argument matching some of a long query still
    ranks, below those matching more of it.
    """
    value = definition.value
    names = " ".join(
        (definition.id, definition.flag, *definition.aliases, definition.label)
    ).lower()
    help_text = " ".join(
        (
            definition.category,
            *definition.effect_tags,
            definition.help.summary,
            definition.help.details or "",
            *definition.help.notes,
            *definition.help.examples,
            *(f"{option.value} {option.label or ''}" for option in value.options),
            value.negation or "",
        )
    ).lower()
    name_words, help_words = _words(names), _words(help_text)
    score = matched = 0
    for term in terms:
        best = max(2 * _match(term, name_words, names), _match(term, help_words, help_text))
        score += best
        matched += best > 0
    return score, matched


async def search_arguments(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    query = text_argument(arguments, "query", required=False)
    category = text_argument(arguments, "category", required=False).lower()
    catalog = context.services.catalog
    terms = _terms(query)
    if not terms and not category:
        raise ToolError("give a query, a category, or both")
    ranked = sorted(
        (
            (score, matched, definition)
            for definition in catalog.arguments
            if not category or definition.category == category
            for score, matched in [_score(definition, terms) if terms else (1, 0)]
            if score
        ),
        key=lambda item: (-item[1], -item[0], item[2].common_rank or 10_000, item[2].id),
    )
    payload: dict[str, object] = {
        "matches": [_brief(definition) for _, _, definition in ranked[:SEARCH_LIMIT]],
        "total": len(ranked),
    }
    if not ranked:
        payload["categories"] = sorted({definition.category for definition in catalog.arguments})
    return json_result(payload, f"{len(ranked)} matches for {query or category!r}")


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
        "'kv cache type', 'speculative draft' or 'gpu layers'. Matches any of the words, "
        "best matches first, and forgives typos. Returns up to 20 with a summary each; with "
        "no matches it lists the categories, which can be browsed with category.",
        object_schema(
            {
                "query": {"type": "string", "description": "Words to search for."},
                "category": {"type": "string", "description": "Only this category."},
            }
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
