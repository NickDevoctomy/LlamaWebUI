from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState
from llamawebui.services.router_supervisor import (
    RouterProcess,
    RouterRestartPolicy,
    RouterSupervisor,
)

pytestmark = pytest.mark.asyncio


class FakeProcess:
    pid = 1234

    def __init__(
        self, *, terminate_exits: bool = True, stdout: asyncio.StreamReader | None = None
    ) -> None:
        self.returncode: int | None = None
        self.stdout = stdout
        self.terminated = False
        self.killed = False
        self._terminate_exits = terminate_exits
        self._exited = asyncio.Event()

    async def terminate_tree(self, *, force: bool) -> None:
        if force:
            self.killed = True
            self.exit(-9)
        else:
            self.terminated = True
            if self._terminate_exits:
                self.exit(0)

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


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        ({"max_attempts": -1}, "max attempts"),
        ({"window_seconds": 0}, "window"),
        ({"delay_seconds": -1}, "delay"),
        ({"ready_timeout_seconds": 0}, "readiness timeout"),
    ),
)
async def test_restart_policy_rejects_invalid_values(
    arguments: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RouterRestartPolicy(**arguments)


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


async def test_supervisor_waits_for_health_and_captures_bounded_logs(tmp_path: Path) -> None:
    output = asyncio.StreamReader()
    process = FakeProcess(stdout=output)
    health_results = iter((False, True))

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    async def health_probe(host: str, port: int) -> bool:
        assert (host, port) == ("127.0.0.1", 1234)
        return next(health_results)

    supervisor = RouterSupervisor(launcher, health_probe, log_capacity=2)
    launch = launch_configuration(tmp_path)
    await supervisor.start(launch)
    output.feed_data(b"first\nsecond\nthird\xff\n")
    output.feed_eof()
    await supervisor.wait_until_ready(launch, poll_interval_seconds=0)
    await asyncio.sleep(0)

    assert supervisor.state is RouterState.READY
    assert supervisor.logs == ("second", "third\ufffd")
    await supervisor.stop()


async def test_supervisor_stops_after_readiness_timeout(tmp_path: Path) -> None:
    process = FakeProcess()

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    async def unhealthy(host: str, port: int) -> bool:
        return False

    supervisor = RouterSupervisor(launcher, unhealthy)
    launch = launch_configuration(tmp_path)
    await supervisor.start(launch)

    with pytest.raises(TimeoutError, match="did not become ready"):
        await supervisor.wait_until_ready(
            launch, timeout_seconds=0.001, poll_interval_seconds=0
        )

    assert process.terminated
    assert supervisor.state is RouterState.STOPPED


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


async def test_readiness_reports_early_process_exit(tmp_path: Path) -> None:
    process = FakeProcess()
    observed: list[tuple[RouterState, int | None, int | None]] = []

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    async def unhealthy(host: str, port: int) -> bool:
        process.exit(23)
        return False

    supervisor = RouterSupervisor(launcher, unhealthy)
    supervisor.set_state_observer(lambda state, pid, code: observed.append((state, pid, code)))
    launch = launch_configuration(tmp_path)
    await supervisor.start(launch)

    with pytest.raises(RuntimeError, match="exited before becoming ready: 23"):
        await supervisor.wait_until_ready(launch, poll_interval_seconds=0)

    assert supervisor.state is RouterState.CRASHED
    assert observed[-1] == (RouterState.CRASHED, 1234, 23)


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


async def test_supervisor_restarts_after_unexpected_exit(tmp_path: Path) -> None:
    processes = [FakeProcess(), FakeProcess()]
    restarted = asyncio.Event()

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        process = processes.pop(0)
        if len(processes) == 0:
            restarted.set()
        return process

    async def healthy(host: str, port: int) -> bool:
        return True

    supervisor = RouterSupervisor(
        launcher,
        healthy,
        restart_policy=RouterRestartPolicy(delay_seconds=0),
    )
    launch = launch_configuration(tmp_path)
    await supervisor.start(launch)
    supervisor.mark_ready()
    first_process = supervisor._process
    assert isinstance(first_process, FakeProcess)

    first_process.exit(17)
    await asyncio.wait_for(restarted.wait(), 1)
    await asyncio.sleep(0)

    assert supervisor.state is RouterState.READY
    assert supervisor.pid == 1234
    await supervisor.stop()


async def test_supervisor_suppresses_rapid_restart_failures(tmp_path: Path) -> None:
    process = FakeProcess()
    launch_count = 0
    attempts_exhausted = asyncio.Event()

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        nonlocal launch_count
        launch_count += 1
        if launch_count == 1:
            return process
        if launch_count == 3:
            attempts_exhausted.set()
        raise OSError("restart failed")

    supervisor = RouterSupervisor(
        launcher,
        restart_policy=RouterRestartPolicy(max_attempts=2, delay_seconds=0),
    )
    await supervisor.start(launch_configuration(tmp_path))
    supervisor.mark_ready()
    process.exit(17)
    await asyncio.wait_for(attempts_exhausted.wait(), 1)
    await asyncio.sleep(0)

    assert launch_count == 3
    assert supervisor.state is RouterState.CRASHED
    await supervisor.stop()


async def test_supervisor_retries_after_restart_readiness_timeout(tmp_path: Path) -> None:
    processes = [FakeProcess(), FakeProcess(), FakeProcess()]
    launch_count = 0

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        nonlocal launch_count
        process = processes[launch_count]
        launch_count += 1
        return process

    async def unhealthy(host: str, port: int) -> bool:
        return False

    supervisor = RouterSupervisor(
        launcher,
        unhealthy,
        restart_policy=RouterRestartPolicy(
            max_attempts=2,
            delay_seconds=0,
            ready_timeout_seconds=0.001,
        ),
    )
    await supervisor.start(launch_configuration(tmp_path))
    supervisor.mark_ready()
    processes[0].exit(17)
    await asyncio.sleep(0)
    restart_task = supervisor._restart_task
    assert restart_task is not None
    await asyncio.wait_for(asyncio.shield(restart_task), 1)

    assert launch_count == 3
    assert processes[1].terminated
    assert processes[2].terminated
    assert supervisor.state is RouterState.CRASHED
    await supervisor.stop()


async def test_explicit_stop_cancels_pending_restart(tmp_path: Path) -> None:
    process = FakeProcess()
    launch_count = 0

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        nonlocal launch_count
        launch_count += 1
        return process

    supervisor = RouterSupervisor(
        launcher,
        restart_policy=RouterRestartPolicy(delay_seconds=60),
    )
    await supervisor.start(launch_configuration(tmp_path))
    supervisor.mark_ready()
    process.exit(17)
    await asyncio.sleep(0)
    await supervisor.stop()

    assert launch_count == 1
    assert supervisor.state is RouterState.STOPPED