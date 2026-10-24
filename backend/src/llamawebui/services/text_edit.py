"""Replace part of a text file by exact text, the way coding agents edit files.

Matching follows OpenCode's edit tool: an exact match first; failing that, the same
text with typographic quotes, dashes and spaces treated as their plain forms; failing
that, line by line ignoring trailing whitespace. Text must otherwise match exactly,
and must be found once unless every occurrence is replaced. When it is found more than
once, the line it starts on can pick one.

Files with Windows line endings are matched as the model reads them, with plain line
breaks, and keep their endings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TYPOGRAPHIC = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("          ", " "),
        **dict.fromkeys("  　", " "),
    }
)
_LINE = re.compile(r"[^\n]*\n|[^\n]+$")


class EditError(ValueError):
    """An edit that cannot be made; the message is written for the model to act on."""


@dataclass(frozen=True, slots=True)
class Edited:
    text: str
    replacements: int
    # How the text was found: "exact", "typographic" or "trailing whitespace".
    matched: str


def _plain(text: str) -> str:
    """Typographic characters as their plain forms; one character for one, so offsets hold."""
    return text.translate(_TYPOGRAPHIC)


def _occurrences(content: str, search: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    offset = content.find(search)
    while offset != -1:
        found.append((offset, offset + len(search)))
        offset = content.find(search, offset + len(search))
    return found


def _line_occurrences(content: str, search: str) -> list[tuple[int, int]]:
    """Blocks of whole lines that match the search line by line, ignoring trailing whitespace."""
    trailing_newline = search.endswith("\n")
    expected = [_plain(line).rstrip() for line in search.split("\n")]
    if trailing_newline:
        expected.pop()
    lines = [(match.start(), match.end(), match.group()) for match in _LINE.finditer(content)]
    found: list[tuple[int, int]] = []
    for index in range(len(lines) - len(expected) + 1):
        block = lines[index : index + len(expected)]
        if any(
            _plain(text.rstrip("\n")).rstrip() != want
            for (_, _, text), want in zip(block, expected, strict=True)
        ):
            continue
        start, (_, end, last) = block[0][0], block[-1]
        has_newline = last.endswith("\n")
        if trailing_newline and not has_newline:
            continue
        stop = end if trailing_newline else end - (1 if has_newline else 0)
        if not found or start >= found[-1][1]:
            found.append((start, stop))
    return found


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _listed(numbers: list[int]) -> str:
    words = [str(number) for number in numbers]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def replace_text(
    source: str,
    old: str,
    new: str,
    *,
    name: str,
    replace_all: bool = False,
    line: int | None = None,
) -> Edited:
    """Replace ``old`` with ``new`` in ``source``; ``name`` is the file, for messages."""
    if old == "":
        raise EditError("old_string must not be empty. Use write_file to create or replace a file.")
    if old == new:
        raise EditError("No changes to apply: old_string and new_string are identical.")
    # Match against the plain-newline view the model reads, then restore Windows endings.
    windows = "\r\n" in source and "\n" not in source.replace("\r\n", "")
    view = source.replace("\r\n", "\n") if windows else source
    search = old.replace("\r\n", "\n")
    replacement = new.replace("\r\n", "\n")
    matched, matches = "exact", _occurrences(view, search)
    if not matches:
        matched, matches = "typographic", _occurrences(_plain(view), _plain(search))
    if not matches:
        matched, matches = "trailing whitespace", _line_occurrences(view, search)
    if not matches:
        raise EditError(
            f"Could not find old_string in {name}. It must match exactly, including whitespace "
            "and indentation. Read the file again and copy the text without the line numbers."
        )
    if len(matches) > 1 and not replace_all:
        starts = [_line_of(view, start) for start, _ in matches]
        if line is None:
            raise EditError(
                f"Found {len(matches)} matches for old_string in {name}, starting on lines "
                f"{_listed(starts)}, but expected exactly one. Give the line the one you mean "
                "starts on, add more surrounding lines to make it unique, or set replace_all "
                "to true to replace every occurrence."
            )
        chosen = [match for match, start in zip(matches, starts, strict=True) if start == line]
        if not chosen:
            raise EditError(
                f"None of the {len(matches)} matches for old_string in {name} starts on line "
                f"{line}; they start on lines {_listed(starts)}."
            )
        matches = chosen
    text = view
    for start, end in reversed(matches if replace_all else matches[:1]):
        text = text[:start] + replacement + text[end:]
    if windows:
        text = text.replace("\n", "\r\n")
    if text == source:
        raise EditError(f"No changes to apply: the edit leaves {name} unchanged.")
    return Edited(text=text, replacements=len(matches) if replace_all else 1, matched=matched)


def numbered(text: str) -> str:
    """File text with each line prefixed by its number, as ``12: text``."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(f"{index}: {line}" for index, line in enumerate(lines, start=1))
