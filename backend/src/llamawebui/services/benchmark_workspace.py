"""A benchmark run's own folder, the only place its model may read and write files.

Models asked to write code like to put test files at the root of a drive, so every
path is checked here: it must be relative, stay inside the run's folder once
resolved, and avoid names Windows treats specially.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from llamawebui.services.text_edit import EditError, numbered, replace_text

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 64
MAX_TOTAL_BYTES = 16 * 1024 * 1024
_RUN_ID = re.compile(r"^[0-9a-f-]{36}$")
_DRIVE = re.compile(r"^[A-Za-z]:")
# Device names Windows reserves in every folder, with or without an extension.
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9]|conin\$|conout\$)(\..*)?$", re.I)
_FORBIDDEN_CHARACTERS = set('<>:"|?*') | {chr(code) for code in range(32)}


class WorkspaceError(ValueError):
    """A request the model made that is refused; the message is sent back to it."""


def run_folder(root: Path, run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError(f"invalid benchmark run id: {run_id}")
    return root / run_id


def remove_run_folder(root: Path, run_id: str) -> None:
    """Delete a run's files; only ever a folder directly inside the workspace root."""
    folder = run_folder(root, run_id)
    if folder.is_symlink():
        folder.unlink()
    elif folder.is_dir() and folder.resolve().parent == root.resolve():
        shutil.rmtree(folder)


class BenchmarkWorkspace:
    def __init__(
        self,
        folder: Path,
        *,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_files: int = MAX_FILES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> None:
        self.folder = folder
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files
        self._max_total_bytes = max_total_bytes

    def resolve(self, relative: object) -> Path:
        """The file a model's path names, refusing anything outside this folder."""
        if not isinstance(relative, str) or not relative.strip():
            raise WorkspaceError("give a file path, like 'tetris/index.html'")
        text = relative.strip().replace("\\", "/")
        if text.startswith(("/", "~")) or _DRIVE.match(text):
            raise WorkspaceError(
                f"'{relative}' is outside your workspace. Use a relative path, "
                "like 'tetris/index.html'; absolute paths and drives are not allowed."
            )
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if not parts:
            raise WorkspaceError("give a file path, like 'tetris/index.html'")
        for part in parts:
            if part == "..":
                raise WorkspaceError(f"'{relative}' leaves your workspace; '..' is not allowed")
            if (
                _RESERVED.match(part)
                or part != part.rstrip(". ")
                or _FORBIDDEN_CHARACTERS.intersection(part)
            ):
                raise WorkspaceError(f"'{part}' is not a usable file or folder name")
        root = self.folder.resolve()
        candidate = self.folder.joinpath(*parts)
        # Refuse links anywhere along the way, so nothing can point back out.
        for step in [candidate, *candidate.parents]:
            if step == self.folder or len(step.parts) < len(self.folder.parts):
                break
            if step.is_symlink() or step.is_junction():
                raise WorkspaceError(f"'{relative}' passes through a link, which is not allowed")
        resolved = candidate.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise WorkspaceError(f"'{relative}' is outside your workspace")
        return candidate

    def _entries(self) -> list[tuple[str, int]]:
        if not self.folder.is_dir():
            return []
        return [
            (path.relative_to(self.folder).as_posix(), path.stat().st_size)
            for path in sorted(self.folder.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]

    def files(self) -> list[dict[str, object]]:
        return [{"path": path, "size": size} for path, size in self._entries()]

    def write(self, relative: object, content: object) -> str:
        if not isinstance(content, str):
            raise WorkspaceError("give the file's content as text")
        target = self.resolve(relative)
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise WorkspaceError(
                f"the file is {len(data):,} bytes; files are limited to {self._max_file_bytes:,}"
            )
        existing = dict(self._entries())
        name = target.relative_to(self.folder).as_posix()
        if name not in existing and len(existing) >= self._max_files:
            raise WorkspaceError(f"the workspace is limited to {self._max_files} files")
        total = sum(existing.values()) - existing.get(name, 0) + len(data)
        if total > self._max_total_bytes:
            raise WorkspaceError(
                f"the workspace is limited to {self._max_total_bytes:,} bytes in total"
            )
        if target.is_dir():
            raise WorkspaceError(f"'{name}' is a folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"Wrote {len(data):,} bytes to {name}"

    def read(self, relative: object) -> str:
        target = self.resolve(relative)
        name = target.relative_to(self.folder).as_posix()
        if not target.is_file():
            raise WorkspaceError(f"'{name}' does not exist; use list_files to see your files")
        if target.stat().st_size > self._max_file_bytes:
            raise WorkspaceError(f"'{name}' is too large to read")
        return target.read_bytes().decode("utf-8", errors="replace")

    def read_numbered(self, relative: object) -> str:
        """A file as the model reads it: each line prefixed by its number, as ``12: text``."""
        text = numbered(self.read(relative))
        return text or "(the file is empty)"

    def edit(
        self,
        relative: object,
        old: object,
        new: object,
        *,
        replace_all: object = False,
        line: object = None,
    ) -> str:
        """Replace part of a file by exact text; see :func:`replace_text`."""
        if not isinstance(old, str) or not isinstance(new, str):
            raise WorkspaceError("give old_string and new_string as text")
        if not isinstance(replace_all, bool):
            raise WorkspaceError("replace_all must be true or false")
        number = _line_number(line)
        source = self.read(relative)
        name = self.resolve(relative).relative_to(self.folder).as_posix()
        try:
            edited = replace_text(
                source, old, new, name=name, replace_all=replace_all, line=number
            )
        except EditError as error:
            # line only picks between several matches, so one it can't read only matters
            # when there is more than one; then it is part of why the edit failed.
            unread = (
                f" The line given, {line!r}, is not a line number counting from 1."
                if line is not None and number is None
                else ""
            )
            raise WorkspaceError(f"{error}{unread}") from error
        self.write(relative, edited.text)
        count = edited.replacements
        replaced = f"{count} replacement{'' if count == 1 else 's'}"
        how = "" if edited.matched == "exact" else f", matched ignoring {edited.matched}"
        size = len(edited.text.encode("utf-8"))
        return f"Edited {name} ({replaced}{how}; now {size:,} bytes)"

    def delete(self, relative: object) -> str:
        """Delete one file in the workspace, and any folders that leaves empty.

        :meth:`resolve` already refuses anything outside the folder or through a link;
        the target is checked again just before it goes, and only a plain file inside
        the folder is ever removed. Folders themselves cannot be deleted.
        """
        target = self.resolve(relative)
        name = target.relative_to(self.folder).as_posix()
        if target.is_symlink() or target.is_junction():
            raise WorkspaceError(f"'{name}' is a link, which is not allowed")
        if target.is_dir():
            raise WorkspaceError(f"'{name}' is a folder; only files can be deleted")
        if not target.is_file():
            raise WorkspaceError(f"'{name}' does not exist; use list_files to see your files")
        root = self.folder.resolve()
        resolved = target.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise WorkspaceError(f"'{relative}' is outside your workspace")
        size = target.stat().st_size
        target.unlink()
        # Tidy folders the file leaves empty, stopping at the workspace itself.
        parent = target.parent
        while parent != self.folder and parent.resolve().is_relative_to(root):
            if parent.is_symlink() or parent.is_junction() or any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent
        return f"Deleted {name} ({size:,} bytes)"

    def listing(self) -> str:
        entries = self._entries()
        if not entries:
            return "Your workspace is empty."
        return "\n".join(f"{path} ({size:,} bytes)" for path, size in entries)


_LINE_TEXT = re.compile(r"^\s*(\d+)\s*(?::.*)?$", re.DOTALL)


def _line_number(value: object) -> int | None:
    """A line number from what a model sends: 42, 42.0, "42", or "42: text" copied from
    read_file. None for anything else, which edit_file then does without."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and (found := _LINE_TEXT.match(value)):
        value = int(found.group(1))
    return value if isinstance(value, int) and value >= 1 else None
