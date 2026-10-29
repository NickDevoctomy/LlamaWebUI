from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState
from llamawebui.services.router_supervisor import (
    ManagedRouterProcess,
    RouterProcess,
    RouterRestartPolicy,
    RouterSupervisor,
    launch_router,
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


async def test_managed_process_uses_windows_graceful_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llamawebui.services.router_supervisor as router_supervisor

    process = SimpleNamespace(
        pid=1234,
        returncode=None,
        stdout=None,
        send_signal=Mock(),
        kill=Mock(),
        terminate=Mock(),
    )
    monkeypatch.setattr(router_supervisor, "os", SimpleNamespace(name="nt"))

    await ManagedRouterProcess(process).terminate_tree(force=False)

    process.send_signal.assert_called_once()
    process.kill.assert_not_called()


@pytest.mark.parametrize(
    ("taskkill_result", "returncode", "kill_called"),
    [(0, None, False), (1, None, True), (1, 0, False)],
)
async def test_managed_process_uses_windows_force_termination(
    monkeypatch: pytest.MonkeyPatch,
    taskkill_result: int,
    returncode: int | None,
    kill_called: bool,
) -> None:
    import llamawebui.services.router_supervisor as router_supervisor

    process = SimpleNamespace(
        pid=1234,
        returncode=returncode,
        stdout=None,
        send_signal=Mock(),
        kill=Mock(),
        terminate=Mock(),
    )
    killer = SimpleNamespace(wait=AsyncMock(return_value=taskkill_result))
    create_subprocess = AsyncMock(return_value=killer)
    monkeypatch.setattr(router_supervisor, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        router_supervisor,
        "asyncio",
        SimpleNamespace(
            create_subprocess_exec=create_subprocess,
            subprocess=SimpleNamespace(DEVNULL=-3),
        ),
    )

    await ManagedRouterProcess(process).terminate_tree(force=True)

    create_subprocess.assert_awaited_once_with(
        "taskkill", "/PID", "1234", "/T", "/F", stdout=-3, stderr=-3
    )
    assert process.kill.called is kill_called


@pytest.mark.parametrize(("force", "expected_signal"), [(False, "SIGTERM"), (True, "SIGKILL")])
async def test_managed_process_signals_posix_process_group(
    monkeypatch: pytest.MonkeyPatch, force: bool, expected_signal: str
) -> None:
    import llamawebui.services.router_supervisor as router_supervisor

    kill_group = Mock()
    monkeypatch.setattr(
        router_supervisor,
        "os",
        SimpleNamespace(name="posix", kill=kill_group),
    )
    process = SimpleNamespace(pid=1234, returncode=None, stdout=None)

    await ManagedRouterProcess(process).terminate_tree(force=force)

    signal_number = getattr(
        router_supervisor.signal,
        expected_signal,
        router_supervisor.signal.SIGTERM,
    )
    kill_group.assert_called_once_with(-1234, signal_number)


@pytest.mark.parametrize(
    ("exception", "force", "returncode", "fallback"),
    [
        (ProcessLookupError(), False, None, None),
        (OSError(), False, None, "terminate"),
        (OSError(), True, None, "kill"),
        (OSError(), True, 0, None),
    ],
)
async def test_managed_process_handles_posix_signal_errors(
    monkeypatch: pytest.MonkeyPatch,
    exception: OSError,
    force: bool,
    returncode: int | None,
    fallback: str | None,
) -> None:
    import llamawebui.services.router_supervisor as router_supervisor

    kill_group = Mock(side_effect=exception)
    monkeypatch.setattr(
        router_supervisor,
        "os",
        SimpleNamespace(name="posix", kill=kill_group),
    )
    process = SimpleNamespace(
        pid=1234,
        returncode=returncode,
        stdout=None,
        terminate=Mock(),
        kill=Mock(),
    )

    await ManagedRouterProcess(process).terminate_tree(force=force)

    if fallback == "terminate":
        process.terminate.assert_called_once_with()
    else:
        process.terminate.assert_not_called()
    if fallback == "kill":
        process.kill.assert_called_once_with()
    else:
        process.kill.assert_not_called()


async def test_launch_router_uses_windows_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    import llamawebui.services.router_supervisor as router_supervisor

    process = SimpleNamespace(pid=1234, returncode=None, stdout=None)
    create_subprocess = AsyncMock(return_value=process)
    jobs: list[int] = []
    # Never attach a real process that happens to have the fake pid to a kill-on-close job.
    monkeypatch.setattr(router_supervisor, "ProcessJob", lambda pid: jobs.append(pid))
    monkeypatch.setattr(router_supervisor, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        router_supervisor,
        "subprocess",
        SimpleNamespace(CREATE_NEW_PROCESS_GROUP=512, PIPE=-1, STDOUT=-2),
    )
    monkeypatch.setattr(
        router_supervisor,
        "asyncio",
        SimpleNamespace(
            create_subprocess_exec=create_subprocess,
            subprocess=SimpleNamespace(PIPE=-1, STDOUT=-2),
        ),
    )

    wrapped = await launch_router(("llama-server", "--version"))

    assert isinstance(wrapped, ManagedRouterProcess)
    create_subprocess.assert_awaited_once_with(
        "llama-server",
        "--version",
        stdout=-1,
        stderr=-2,
        creationflags=512,
    )
    assert jobs == [1234]


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


async def test_supervisor_exposes_effective_launch_arguments(tmp_path: Path) -> None:
    process = FakeProcess()

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    supervisor = RouterSupervisor(launcher)
    launch = launch_configuration(tmp_path)

    await supervisor.start(launch)

    assert supervisor.launch_arguments == launch.arguments()


async def test_supervisor_parses_prompt_and_decode_timing() -> None:
    supervisor = RouterSupervisor()

    supervisor._parse_timing(
        "slot print_timing: id 3 | task 0 | prompt processing, n_tokens = 577, "
        "progress = 0.98, t = 6.93 s / 83.24 tokens per second"
    )
    supervisor._parse_timing(
        "slot print_timing: id 2 | task 125 | n_gen = 7289, t = 491.2 s, "
        "tg = 14.81 t/s, "
        "tg_3s = 16.65 t/s"
    )

    assert supervisor.timing == {
        "prompt_tokens_per_second": 83.24,
        "decode_tokens_per_second": 14.81,
        "decode_tokens_per_second_peak": 16.65,
        "task_id": 125,
        "task_elapsed_seconds": 491.2,
        "decode_tokens_per_second_rolling_peak": None,
        "decode_tokens_per_second_session_peak": None,
    }


async def test_supervisor_starts_a_new_telemetry_session_on_each_launch(tmp_path: Path) -> None:
    output = asyncio.StreamReader()
    processes = [FakeProcess(stdout=output), FakeProcess()]

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return processes.pop(0)

    supervisor = RouterSupervisor(launcher, clock=lambda: 100.0)
    await supervisor.start(launch_configuration(tmp_path))
    output.feed_data(
        b"[ 5001] slot print_timing: id  0 | task 3 | n_gen =     40, tg =  50.00 t/s, "
        b"tg_3s =  61.00 t/s\n"
        b"[ 5001] srv    load_model: initializing, n_slots = 1, n_ctx_slot = 4096\n"
    )
    output.feed_eof()
    for _ in range(5):
        await asyncio.sleep(0)

    assert supervisor.timing["decode_tokens_per_second_session_peak"] == 61.0
    assert supervisor.timing["decode_tokens_per_second_peak"] == 61.0
    assert supervisor.context[0]["slots"] == [{"id": 0, "used": 0, "total": 4096}]

    await supervisor.stop()
    await supervisor.start(launch_configuration(tmp_path))

    assert supervisor.timing["decode_tokens_per_second_session_peak"] is None
    assert supervisor.timing["decode_tokens_per_second_peak"] is None
    assert supervisor.context == []
    await supervisor.stop()


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
    # Lines after a mark, as far as the bounded buffer still holds them.
    assert supervisor.log_mark == 3
    assert supervisor.logs_since(1) == ("second", "third\ufffd")
    assert supervisor.logs_since(0) == ("second", "third\ufffd")
    assert supervisor.logs_since(2) == ("third\ufffd",)
    assert supervisor.logs_since(3) == ()
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


class RecordingHooks:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.events: list[str] = []

    def launch_arguments(self, arguments: Sequence[str]) -> tuple[str, ...]:
        self.events.append("launch")
        return ("profiler", *arguments)

    async def router_ready(self) -> None:
        self.events.append("ready")

    async def router_stopping(self) -> None:
        # The report must be written while the router is still running.
        self.events.append("stopping" if self.process.returncode is None else "stopping-late")

    def router_exited(self) -> None:
        self.events.append("exited")

    def log_file(self) -> Path | None:
        return None


class LogFileHooks(RecordingHooks):
    def __init__(self, process: FakeProcess, path: Path) -> None:
        super().__init__(process)
        self.path = path

    def log_file(self) -> Path | None:
        return self.path


async def test_supervisor_hooks_wrap_the_command_and_run_before_stop(tmp_path: Path) -> None:
    process = FakeProcess()
    hooks = RecordingHooks(process)
    launched: list[tuple[str, ...]] = []

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        launched.append(tuple(arguments))
        return process

    async def healthy(host: str, port: int) -> bool:
        return True

    supervisor = RouterSupervisor(launcher, healthy, hooks=hooks)
    launch = launch_configuration(tmp_path)
    await supervisor.start(launch)
    await supervisor.wait_until_ready(launch)
    await supervisor.stop()

    assert launched == [("profiler", *launch.arguments())]
    # The supervisor still reports the router's own command.
    assert supervisor.launch_arguments == launch.arguments()
    assert hooks.events == ["launch", "ready", "stopping", "exited"]


async def test_supervisor_reads_the_router_log_file_a_profiler_asks_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("llamawebui.services.router_supervisor._LOG_FILE_POLL_SECONDS", 0.01)
    process = FakeProcess()
    log = tmp_path / "router.log"

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    supervisor = RouterSupervisor(launcher, hooks=LogFileHooks(process, log))
    await supervisor.start(launch_configuration(tmp_path))
    await asyncio.sleep(0.05)
    # The file appears after the router starts, and lines can arrive half-written.
    log.write_bytes(
        b"[ 5001] srv    load_model: initializing, n_slots = 1, n_ctx_slot = 4096\r\n[ 5001] slot"
    )
    for _ in range(50):
        if supervisor.context:
            break
        await asyncio.sleep(0.01)
    assert supervisor.context[0]["slots"] == [{"id": 0, "used": 0, "total": 4096}]

    with log.open("ab") as handle:
        handle.write(b" print_timing: id  0 | task 3 | n_gen = 40, tg =  50.00 t/s\n")
    for _ in range(50):
        if supervisor.timing["decode_tokens_per_second"] is not None:
            break
        await asyncio.sleep(0.01)
    assert supervisor.timing["decode_tokens_per_second"] == 50.0

    with log.open("ab") as handle:
        handle.write(b"last line without a newline")
    await supervisor.stop()

    assert supervisor.logs[-3:] == (
        "[ 5001] srv    load_model: initializing, n_slots = 1, n_ctx_slot = 4096",
        "[ 5001] slot print_timing: id  0 | task 3 | n_gen = 40, tg =  50.00 t/s",
        "last line without a newline",
    )
