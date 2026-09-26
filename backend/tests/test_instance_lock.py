from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from llamawebui.services.instance_lock import InstanceAlreadyRunningError, InstanceLock


def test_windows_lock_file_uses_nonblocking_msvcrt_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    import llamawebui.services.instance_lock as instance_lock

    file = SimpleNamespace(
        seek=Mock(),
        tell=Mock(return_value=0),
        write=Mock(),
        flush=Mock(),
        fileno=Mock(return_value=7),
    )
    locking = Mock()
    monkeypatch.setattr(instance_lock, "os", SimpleNamespace(name="nt", SEEK_END=2))
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking),
    )

    InstanceLock._lock_file(file)  # type: ignore[arg-type]

    assert file.write.call_args.args == (b" ",)
    file.flush.assert_called_once_with()
    locking.assert_called_once_with(7, 1, 1)


def test_windows_lock_file_translates_contention(monkeypatch: pytest.MonkeyPatch) -> None:
    import llamawebui.services.instance_lock as instance_lock

    locking = Mock(side_effect=PermissionError("locked"))
    monkeypatch.setattr(instance_lock, "os", SimpleNamespace(name="nt", SEEK_END=2))
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking),
    )

    with pytest.raises(BlockingIOError):
        file = SimpleNamespace(
            seek=Mock(),
            tell=Mock(return_value=1),
            fileno=Mock(return_value=7),
        )
        InstanceLock._lock_file(file)  # type: ignore[arg-type]


def test_windows_unlock_file_uses_msvcrt(monkeypatch: pytest.MonkeyPatch) -> None:
    import llamawebui.services.instance_lock as instance_lock

    locking = Mock()
    monkeypatch.setattr(instance_lock, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking),
    )
    file = SimpleNamespace(seek=Mock(), fileno=Mock(return_value=7))

    InstanceLock._unlock_file(file)  # type: ignore[arg-type]

    file.seek.assert_called_once_with(0)
    locking.assert_called_once_with(7, 2, 1)


def test_instance_lock_writes_owner_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "llamawebui.lock"

    lock = InstanceLock(path)
    lock.acquire()
    try:
        owner = json.loads(lock.owner_path.read_text(encoding="utf-8"))
        assert owner["pid"] == psutil.Process().pid
        assert isinstance(owner["create_time"], float)
    finally:
        lock.release()

    second = InstanceLock(path)
    second.acquire()
    second.release()


def test_instance_lock_rejects_verified_live_owner(tmp_path: Path) -> None:
    path = tmp_path / "llamawebui.lock"
    first = InstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunningError, match="already running"):
            InstanceLock(path).acquire()
    finally:
        first.release()


def test_instance_lock_does_not_steal_unverified_lock(tmp_path: Path) -> None:
    path = tmp_path / "llamawebui.lock"
    path.write_text(json.dumps({"pid": 99999999, "create_time": 1.0}), encoding="utf-8")

    lock = InstanceLock(path)
    lock._lock_file = lambda _file: (_ for _ in ()).throw(BlockingIOError())  # type: ignore[method-assign]
    with pytest.raises(InstanceAlreadyRunningError, match="cannot be verified"):
        lock.acquire()


def test_instance_lock_release_without_acquire_is_safe(tmp_path: Path) -> None:
    InstanceLock(tmp_path / "llamawebui.lock").release()


def test_instance_lock_context_manager_releases(tmp_path: Path) -> None:
    path = tmp_path / "llamawebui.lock"
    with InstanceLock(path):
        assert path.exists()
    InstanceLock(path).acquire()