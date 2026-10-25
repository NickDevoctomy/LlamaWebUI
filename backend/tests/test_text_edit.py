from pathlib import Path

import pytest

from llamawebui.services.benchmark_workspace import BenchmarkWorkspace, WorkspaceError
from llamawebui.services.text_edit import EditError, numbered, replace_text

GAME = "let score = 0\nfunction tick() {\n  score += 1\n}\nfunction reset() {\n  score = 0\n}\n"


def test_an_exact_unique_match_is_replaced() -> None:
    edited = replace_text(GAME, "score += 1", "score += 10", name="game.js")

    assert edited.text == GAME.replace("score += 1", "score += 10")
    assert (edited.replacements, edited.matched) == (1, "exact")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("", "x", "old_string must not be empty. Use write_file"),
        ("tick", "tick", "No changes to apply: old_string and new_string are identical."),
        ("score -= 1", "x", "Could not find old_string in game.js. It must match exactly"),
    ],
)
def test_edits_that_cannot_be_made_say_why(old: str, new: str, message: str) -> None:
    with pytest.raises(EditError, match=message):
        replace_text(GAME, old, new, name="game.js")


def test_several_matches_need_a_line_more_context_or_replace_all() -> None:
    with pytest.raises(EditError) as raised:
        replace_text(GAME, "score = 0", "score = 5", name="game.js")
    assert str(raised.value).startswith(
        "Found 2 matches for old_string in game.js, starting on lines 1 and 6, "
        "but expected exactly one."
    )

    # The line it starts on picks one; any other line is refused with the real ones.
    picked = replace_text(GAME, "score = 0", "score = 5", name="game.js", line=6)
    assert picked.text.splitlines()[5] == "  score = 5"
    assert picked.text.splitlines()[0] == "let score = 0"
    with pytest.raises(EditError, match="starts on line 3; they start on lines 1 and 6"):
        replace_text(GAME, "score = 0", "score = 5", name="game.js", line=3)

    every = replace_text(GAME, "score = 0", "score = 5", name="game.js", replace_all=True)
    assert every.replacements == 2
    assert "score = 0" not in every.text


def test_a_line_is_ignored_when_the_text_is_unique() -> None:
    edited = replace_text(GAME, "score += 1", "score += 2", name="game.js", line=40)
    assert "score += 2" in edited.text


def test_typographic_variants_match_their_plain_forms() -> None:
    source = 'title = "Tetris – classic"\n'
    curly = "title = “Tetris — classic”"
    edited = replace_text(source, curly, "title = 'T'", name="a")

    assert edited.text == "title = 'T'\n"
    assert edited.matched == "typographic"


def test_trailing_whitespace_on_each_line_is_ignored_as_a_last_resort() -> None:
    source = "if (a) {   \n  go()\t\n}\nrest\n"
    edited = replace_text(source, "if (a) {\n  go()\n}\n", "if (b) {\n  stop()\n}\n", name="a")

    assert edited.text == "if (b) {\n  stop()\n}\nrest\n"
    assert edited.matched == "trailing whitespace"
    # Leading whitespace still has to match.
    with pytest.raises(EditError, match="Could not find"):
        replace_text(source, "if (a) {\ngo()", "x", name="a")


def test_windows_line_endings_are_matched_as_read_and_kept() -> None:
    source = "one\r\ntwo\r\nthree\r\n"
    edited = replace_text(source, "two\nthree", "2\n3", name="a")

    assert edited.text == "one\r\n2\r\n3\r\n"
    # A model that sends Windows endings itself matches too.
    assert replace_text(source, "one\r\ntwo", "1", name="a").text == "1\r\nthree\r\n"


def test_numbered_lines_match_what_read_file_shows() -> None:
    assert numbered("a\r\nb\n") == "1: a\n2: b"
    assert numbered("") == ""


def test_the_workspace_edits_within_its_limits(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace(tmp_path / "run", max_file_bytes=40)
    workspace.write("game.js", "let score = 0\nlet best = 0\n")

    assert workspace.read_numbered("game.js") == "1: let score = 0\n2: let best = 0"
    assert (
        workspace.edit("game.js", "let score = 0", "let score = 1")
        == "Edited game.js (1 replacement; now 27 bytes)"
    )
    with pytest.raises(WorkspaceError, match="files are limited to 40"):
        workspace.edit("game.js", "score", "a much longer variable name")
    with pytest.raises(WorkspaceError, match="does not exist"):
        workspace.edit("missing.js", "a", "b")
    with pytest.raises(WorkspaceError, match="outside your workspace"):
        workspace.edit("C:/game.js", "a", "b")
    with pytest.raises(WorkspaceError, match="give old_string and new_string as text"):
        workspace.edit("game.js", None, "b")
    with pytest.raises(WorkspaceError, match="replace_all must be true or false"):
        workspace.edit("game.js", "a", "b", replace_all="yes")
    with pytest.raises(WorkspaceError, match="Could not find old_string in game.js"):
        workspace.edit("game.js", "nothing like this", "b")
    workspace.write("empty.txt", "")
    assert workspace.read_numbered("empty.txt") == "(the file is empty)"


@pytest.mark.parametrize("line", ["2", "2: let b = 0;", " 2 ", 2.0, 2])
def test_edit_reads_line_numbers_however_a_model_writes_them(
    tmp_path: Path, line: object
) -> None:
    workspace = BenchmarkWorkspace(tmp_path)
    workspace.write("game.js", "let a = 0;\nlet b = 0;\nlet b = 0;\n")

    workspace.edit("game.js", "let b = 0;", "let b = 1;", line=line)

    assert workspace.read("game.js") == "let a = 0;\nlet b = 1;\nlet b = 0;\n"


@pytest.mark.parametrize("line", [0, -3, "two", True, 1.5, "", [2]])
def test_an_unreadable_line_is_ignored_when_the_match_is_unique(
    tmp_path: Path, line: object
) -> None:
    workspace = BenchmarkWorkspace(tmp_path)
    workspace.write("game.js", "let a = 0;\nlet b = 0;\nlet b = 0;\n")

    workspace.edit("game.js", "let a = 0;", "let a = 1;", line=line)
    assert workspace.read("game.js").startswith("let a = 1;")

    # With several matches it is part of why the edit failed, and the reply says so.
    with pytest.raises(WorkspaceError) as caught:
        workspace.edit("game.js", "let b = 0;", "let b = 1;", line=line)
    assert str(caught.value).startswith("Found 2 matches for old_string in game.js")
    assert str(caught.value).endswith(
        f"The line given, {line!r}, is not a line number counting from 1."
    )
