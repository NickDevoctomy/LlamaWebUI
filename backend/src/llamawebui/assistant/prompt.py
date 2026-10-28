"""The assistant's system prompt, from a bundled template with ``{{token}}`` placeholders."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

PROMPT_DIRECTORY = Path(__file__).resolve().parent / "prompts"
SYSTEM_TEMPLATE = PROMPT_DIRECTORY / "system.md"
TOKENS = frozenset(
    {"assistant_name", "date", "hardware", "brief", "research_index", "report_index", "snapshots"}
)
_TOKEN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class PromptTemplateError(ValueError):
    pass


def template_tokens(template: str) -> set[str]:
    return set(_TOKEN.findall(template))


def render_prompt(template: str, values: Mapping[str, str]) -> str:
    """Fill every ``{{token}}``; a token without a value, or an unknown one, is an error.

    Values are inserted once, so text inside them that looks like a token stays as it is.
    """
    unknown = template_tokens(template) - TOKENS
    if unknown:
        raise PromptTemplateError(f"unknown prompt tokens: {', '.join(sorted(unknown))}")
    missing = template_tokens(template) - values.keys()
    if missing:
        raise PromptTemplateError(f"no value for prompt tokens: {', '.join(sorted(missing))}")
    return _TOKEN.sub(lambda match: values[match.group(1)], template)


def system_template() -> str:
    return SYSTEM_TEMPLATE.read_text(encoding="utf-8")
