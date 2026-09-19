from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState
from llamawebui.services.router_supervisor import RouterProcess, RouterSupervisor

pytestmark = pytest.mark.asyncio


class FakeProcess:
    pid = 1234

    def __init__(self, *, terminate_exits: bool = True) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._terminate_exits = terminate_exits
        self._exited = asyncio.Event()

    def terminate(self) -> None:
        self.terminated = True
        if self._terminate_exits:
            self.exit(0)

    def kill(self) -> None:
        self.killed = True
        self.exit(-9)

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def exit(self, returncode: int) -> None:
        self.returncode = returncode
        self._exited.set()


def launch_configuration(tmp_path: Path) -> RouterLaunch:
    executable = tmp_path / "llama-server.exe"
    preset = tmp_path / "models.ini"
    executable.touch()
    preset.touch()
    return RouterLaunch(executable, preset)


async def test_supervisor_starts_marks_ready_and_stops(tmp_path: Path) -> None:
    process = FakeProcess()
    captured: tuple[str, ...] = ()

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        nonlocal captured
        captured = tuple(arguments)
        return process

    supervisor = RouterSupervisor(launcher)
    await supervisor.start(launch_configuration(tmp_path))
    supervisor.mark_ready()

    assert supervisor.state is RouterState.READY
    assert supervisor.pid == 1234
    assert captured[1] == "--models-preset"

    await supervisor.stop()

    assert process.terminated
    assert supervisor.state is RouterState.STOPPED
    assert supervisor.pid is None
    assert supervisor.last_exit_code == 0


async def test_supervisor_detects_unexpected_exit_and_can_reset(tmp_path: Path) -> None:
    process = FakeProcess()

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    supervisor = RouterSupervisor(launcher)
    await supervisor.start(launch_configuration(tmp_path))
    process.exit(17)
    await asyncio.sleep(0)

    assert supervisor.state is RouterState.CRASHED
    assert supervisor.last_exit_code == 17

    await supervisor.stop()
    assert supervisor.state is RouterState.STOPPED


async def test_supervisor_force_kills_after_timeout(tmp_path: Path) -> None:
    process = FakeProcess(terminate_exits=False)

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    supervisor = RouterSupervisor(launcher)
    await supervisor.start(launch_configuration(tmp_path))
    await supervisor.stop(timeout_seconds=0.001)

    assert process.terminated
    assert process.killed
    assert supervisor.state is RouterState.STOPPED


async def test_supervisor_records_launch_failure(tmp_path: Path) -> None:
    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        raise OSError("launch failed")

    supervisor = RouterSupervisor(launcher)
    with pytest.raises(OSError, match="launch failed"):
        await supervisor.start(launch_configuration(tmp_path))

    assert supervisor.state is RouterState.CRASHED
    assert supervisor.pid is None