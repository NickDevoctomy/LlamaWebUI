from __future__ import annotations

import json
from pathlib import Path

import psutil
import pytest

from llamawebui.services.instance_lock import InstanceAlreadyRunningError, InstanceLock


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