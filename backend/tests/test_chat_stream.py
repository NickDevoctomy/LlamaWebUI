from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from llamawebui.services.chat_stream import (
    assistant_message,
    completion_chunks,
    delta_kind,
    function_tool,
    history_arguments,
    merge_tool_calls,
    ordered_calls,
    shorten,
)


async def _lines(*lines: str) -> AsyncIterator[str]:
    for line in lines:
        yield line


@pytest.mark.asyncio
async def test_completion_chunks_skip_comments_and_stop_at_done() -> None:
    chunks = [
        chunk
        async for chunk in completion_chunks(
            _lines(
                ": OPENROUTER PROCESSING",
                "",
                'data: {"a": 1}',
                "data: [1]",
                "data: [DONE]",
                'data: {"b": 2}',
            )
        )
    ]
    assert chunks == [{"a": 1}]


@pytest.mark.asyncio
async def test_completion_chunks_reject_invalid_json() -> None:
    with pytest.raises(ValueError):
        [chunk async for chunk in completion_chunks(_lines("data: {nope"))]


def test_tool_calls_are_assembled_from_fragments_and_ordered() -> None:
    pending: dict[int, dict[str, str]] = {}
    sizes: dict[int, int] = {}
    assert merge_tool_calls({"content": "hi"}, pending, sizes) is None
    first = merge_tool_calls(
        {
            "tool_calls": [
                {"index": 1, "id": "b", "function": {"name": "get_", "arguments": '{"x"'}}
            ]
        },
        pending,
        sizes,
    )
    merge_tool_calls(
        {
            "tool_calls": [
                {"index": 1, "function": {"name": "profile", "arguments": ": 1}"}},
                "junk",
            ]
        },
        pending,
        sizes,
    )
    latest = merge_tool_calls({"tool_calls": [{"function": {"name": "list"}}]}, pending, sizes)

    assert first == {"name": "get_", "bytes": 4}
    assert latest == {"name": "list", "bytes": 0}
    calls = ordered_calls(pending, 3)
    assert [call["id"] for call in calls] == ["call_3_0", "b"]
    assert calls[1] == {"id": "b", "name": "get_profile", "arguments": '{"x": 1}'}


def test_history_and_helpers() -> None:
    assert history_arguments('{"a": 1}') == '{"a": 1}'
    assert history_arguments("[1]") == "{}"
    assert history_arguments("{bad") == "{}"
    assert history_arguments("") == ""
    message = assistant_message("text", [{"id": "c", "name": "n", "arguments": "{oops"}])
    assert message == {
        "role": "assistant",
        "content": "text",
        "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "n", "arguments": "{}"}}
        ],
    }
    assert delta_kind({"tool_calls": [{}]}) == "tool_call"
    assert delta_kind({"reasoning": "x"}) == "thinking"
    assert delta_kind({"content": "x"}) == "answer"
    assert delta_kind({}) is None
    assert function_tool("t", "d", {"type": "object"})["function"]["name"] == "t"
    assert shorten("abcdef", 3) == "abc…"
    assert shorten("abc", 3) == "abc"
