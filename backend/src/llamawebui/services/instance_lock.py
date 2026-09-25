"""Process-owned lock for preventing multiple control planes per data directory."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, cast

import psutil  # type: ignore[import-untyped]


class InstanceAlreadyRunningError(RuntimeError):
    """Raised when another live process owns the application lock."""


class InstanceLock:
    """Hold an OS-level exclusive lock and a small diagnostic ownership record."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.owner_path = path.with_suffix(path.suffix + ".owner")
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        try:
            self._lock_file(file)
        except BlockingIOError as error:
            file.close()
            owner = self._read_owner()
            if owner is None or not self._owner_is_live(owner):
                raise InstanceAlreadyRunningError(
                    f"application lock is held and its owner cannot be verified: {self.path}"
                ) from error
            raise InstanceAlreadyRunningError(
                f"another LlamaWebUI instance is already running (pid {owner['pid']})"
            ) from error

        self._file = file
        payload = {
            "pid": os.getpid(),
            "create_time": psutil.Process().create_time(),
        }
        self.owner_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def release(self) -> None:
        if self._file is None:
            return
        file, self._file = self._file, None
        try:
            self._unlock_file(file)
        finally:
            file.close()
            with suppress(FileNotFoundError):
                self.owner_path.unlink()

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def _read_owner(self) -> dict[str, object] | None:
        try:
            with self.owner_path.open("r", encoding="utf-8") as file:
                value = json.load(file)
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
            return None
        return value

    @staticmethod
    def _owner_is_live(owner: dict[str, object]) -> bool:
        try:
            process = psutil.Process(owner["pid"])
            expected = owner.get("create_time")
            return expected is None or abs(
                process.create_time() - float(cast(float, expected))
            ) < 0.01
        except (KeyError, TypeError, ValueError, psutil.Error):
            return False

    @staticmethod
    def _lock_file(file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt_api = cast(Any, msvcrt)
            file.seek(0, os.SEEK_END)
            if file.tell() == 0:
                file.write(b" ")
                file.flush()
            file.seek(0)
            try:
                msvcrt_api.locking(file.fileno(), msvcrt_api.LK_NBLCK, 1)
            except PermissionError as error:
                raise BlockingIOError from error
        else:
            import fcntl

            fcntl_api = cast(Any, fcntl)
            fcntl_api.flock(file.fileno(), fcntl_api.LOCK_EX | fcntl_api.LOCK_NB)

    @staticmethod
    def _unlock_file(file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt_api = cast(Any, msvcrt)
            file.seek(0)
            msvcrt_api.locking(file.fileno(), msvcrt_api.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl_api = cast(Any, fcntl)
            fcntl_api.flock(file.fileno(), fcntl_api.LOCK_UN)