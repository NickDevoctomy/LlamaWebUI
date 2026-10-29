from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from llamawebui.services.router_profiler import (
    PROFILER_SETTINGS,
    ProfilerConfig,
    ProfilerError,
    RouterProfiler,
    find_nsys,
    is_elevated,
    run_nsys,
)
from llamawebui.services.settings_store import SettingsStore

pytestmark = pytest.mark.asyncio

ROUTER = ("C:/runtimes/b11815-cuda-debugsym/llama-server.exe", "--port", "1234")
STARTED = datetime(2026, 10, 29, 9, 30, 15, tzinfo=UTC)


class FakeStore:
    def __init__(self, saved: dict[str, Any] | None = None) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        if saved is not None:
            self.values[PROFILER_SETTINGS] = saved

    def get_json(self, key: str) -> dict[str, Any] | None:
        return self.values.get(key)

    def set_json(self, key: str, value: dict[str, Any]) -> None:
        self.values[key] = value


def output_of(arguments: Sequence[str]) -> Path:
    return Path(next(a for a in arguments if a.startswith("--output=")).split("=", 1)[1])


class FakeNsys:
    """Answers nsys commands the way nsys profile --start-later sessions behave.

    ``stop`` writes the report the last ``start`` asked for, except that the session's first
    report goes to the --output given to nsys profile.
    """

    def __init__(self, *, start_failures: int = 0, stop_code: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.start_failures = start_failures
        self.stop_code = stop_code
        self.output: Path | None = None
        self.profile_output: Path | None = None
        self.stops = 0

    def launch(self, instance: RouterProfiler) -> tuple[str, ...]:
        arguments = instance.launch_arguments(ROUTER)
        if "profile" in arguments:
            self.profile_output = output_of(arguments)
        return arguments

    async def __call__(self, arguments: Sequence[str], timeout: float) -> tuple[int, str]:
        call = tuple(arguments)
        self.calls.append(call)
        command = call[1]
        if command == "--version":
            return 0, "NVIDIA Nsight Systems version 2025.6.3.541-256337736014v0"
        if command == "start":
            if self.start_failures:
                self.start_failures -= 1
                return 1, "Session not found"
            self.output = output_of(call)
            return 0, ""
        if command == "stop":
            self.stops += 1
            first = self.stops == 1 and self.profile_output is not None
            target = self.profile_output if first else self.output
            if self.stop_code == 0 and target is not None:
                Path(f"{target}.nsys-rep").write_bytes(b"report")
            return self.stop_code, "Generated report" if self.stop_code == 0 else "stop failed"
        return 0, ""


def profiler(
    tmp_path: Path,
    *,
    saved: dict[str, Any] | None = None,
    nsys: FakeNsys | None = None,
    elevated: bool = True,
    found: bool = True,
) -> tuple[RouterProfiler, FakeNsys, FakeStore]:
    runner = nsys or FakeNsys()
    store = FakeStore(saved)
    executable = tmp_path / "nsys.exe"
    executable.write_bytes(b"")
    instance = RouterProfiler(
        cast(SettingsStore, store),
        tmp_path / "captures",
        runner=runner,
        locate=lambda: executable if found else None,
        elevated=lambda: elevated,
        clock=lambda: STARTED,
    )
    return instance, runner, store


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_: float) -> None:
        return None

    monkeypatch.setattr("llamawebui.services.router_profiler.asyncio.sleep", sleep)


async def test_router_command_is_unchanged_while_profiling_is_off(tmp_path: Path) -> None:
    instance, runner, _ = profiler(tmp_path)

    assert instance.launch_arguments(ROUTER) == ROUTER
    assert instance.log_file() is None
    await instance.router_ready()
    assert runner.calls == []
    assert (await instance.status())["router_profiled"] is False


async def test_profiled_router_runs_under_a_delayed_nsys_profile_session(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True, "trace": "cuda,nvtx"})

    arguments = instance.launch_arguments(ROUTER)

    assert arguments[0] == str(tmp_path / "nsys.exe")
    assert arguments[1] == "profile"
    assert arguments[2].startswith("--session-new=llamawebui-")
    assert arguments[3:11] == (
        "--start-later=true",
        "--trace=cuda,nvtx",
        "--cuda-graph-trace=graph",
        "--sample=process-tree",
        "--cpuctxsw=process-tree",
        "--sampling-frequency=250",
        "--force-overwrite=true",
        f"--output={tmp_path / 'captures' / '.pending' / arguments[2].split('=', 1)[1]}",
    )
    log_file = tmp_path / "router.log"
    assert arguments[11:] == (*ROUTER, "--log-file", str(log_file))
    assert instance.log_file() == log_file


async def test_graph_nodes_trace_each_kernel_inside_cuda_graphs(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True, "graph_nodes": True})

    assert "--cuda-graph-trace=node" in instance.launch_arguments(ROUTER)


async def test_missing_nsys_leaves_the_router_unprofiled(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True}, found=False)

    assert instance.launch_arguments(ROUTER) == ROUTER
    status = await instance.status()
    assert status["nsys"] == {"path": None, "version": None}
    assert "not found" in status["last_error"]


async def test_capture_starts_when_ready_and_report_is_written_before_stop(
    tmp_path: Path,
) -> None:
    instance, runner, _ = profiler(tmp_path, saved={"enabled": True})
    runner.launch(instance)

    await instance.router_ready()
    capturing = await instance.status()
    await instance.router_stopping()
    instance.router_exited()
    status = await instance.status()

    assert capturing["capture"] == {
        "name": "20261029-093015-b11815-cuda-debugsym.nsys-rep",
        "started_at": STARTED.isoformat(),
    }
    assert status["capture"] is None
    assert status["last_error"] is None
    assert [item["name"] for item in status["captures"]] == [
        "20261029-093015-b11815-cuda-debugsym.nsys-rep"
    ]
    assert status["nsys"]["version"] == "2025.6.3.541"
    # The first report, written to the placeholder nsys profile was given, was moved.
    assert list((tmp_path / "captures" / ".pending").iterdir()) == []


async def test_capture_waits_for_a_manual_start_when_auto_capture_is_off(tmp_path: Path) -> None:
    instance, runner, _ = profiler(tmp_path, saved={"enabled": True, "capture_on_start": False})
    runner.launch(instance)

    await instance.router_ready()
    assert runner.calls == []
    await instance.start_capture()
    with pytest.raises(ProfilerError, match="already running"):
        await instance.start_capture()
    first = await instance.stop_capture()
    later = datetime(2026, 10, 29, 9, 40, tzinfo=UTC)
    instance._clock = lambda: later
    await instance.start_capture()
    second = await instance.stop_capture()

    assert first.read_bytes() == b"report"
    assert second.read_bytes() == b"report"
    assert [path.name for path in (first, second)] == [
        "20261029-093015-b11815-cuda-debugsym.nsys-rep",
        "20261029-094000-b11815-cuda-debugsym.nsys-rep",
    ]


async def test_cpu_sampling_is_off_without_administrator_rights(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True}, elevated=False)

    arguments = instance.launch_arguments(ROUTER)

    assert "--sample=none" in arguments and "--cpuctxsw=none" in arguments
    assert not any(argument.startswith("--sampling-frequency") for argument in arguments)


async def test_sampling_rate_is_configurable_within_nsys_limits(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True, "sampling_hz": 1000})

    assert "--sampling-frequency=1000" in instance.launch_arguments(ROUTER)
    with pytest.raises(ValueError, match="100-8000"):
        ProfilerConfig(sampling_hz=50)
    with pytest.raises(ValueError, match="100-8000"):
        ProfilerConfig(sampling_hz=9000)


async def test_saving_shows_while_nsys_writes_the_report(tmp_path: Path) -> None:
    nsys = FakeNsys()
    instance, _, _ = profiler(tmp_path, saved={"enabled": True}, nsys=nsys)
    nsys.launch(instance)
    await instance.router_ready()
    seen: list[dict[str, str] | None] = []
    answer = nsys.__call__

    async def watching(arguments: Sequence[str], timeout: float) -> tuple[int, str]:
        if arguments[1] == "stop":
            seen.append(instance.saving())
        return await answer(arguments, timeout)

    instance._runner = watching
    await instance.router_stopping()

    assert seen == [
        {"name": "20261029-093015-b11815-cuda-debugsym.nsys-rep", "started_at": STARTED.isoformat()}
    ]
    assert instance.saving() is None
    assert (await instance.status())["saving"] is None


async def test_cpu_sampling_can_be_turned_off(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True, "cpu_sampling": False})

    assert "--sample=none" in instance.launch_arguments(ROUTER)


async def test_router_exit_clears_an_unused_placeholder_report(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True})
    pending = output_of(instance.launch_arguments(ROUTER))
    Path(f"{pending}.nsys-rep").write_bytes(b"stray")

    instance.router_exited()

    assert list(pending.parent.iterdir()) == []


async def test_start_retries_until_the_session_is_registered(tmp_path: Path) -> None:
    nsys = FakeNsys(start_failures=2)
    instance, runner, _ = profiler(tmp_path, saved={"enabled": True}, nsys=nsys)
    instance.launch_arguments(ROUTER)

    await instance.start_capture()

    assert [call[1] for call in runner.calls] == ["start", "start", "start"]


async def test_failed_auto_capture_is_reported_not_raised(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True}, nsys=FakeNsys(start_failures=9))
    instance.launch_arguments(ROUTER)

    await instance.router_ready()

    status = await instance.status()
    assert status["capture"] is None
    assert "could not start a capture: Session not found" in status["last_error"]


async def test_failed_report_on_stop_is_reported_not_raised(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True}, nsys=FakeNsys(stop_code=1))
    instance.launch_arguments(ROUTER)
    await instance.router_ready()

    await instance.router_stopping()

    status = await instance.status()
    assert "could not write the report: stop failed" in status["last_error"]
    assert status["captures"] == []


async def test_router_exit_during_a_capture_loses_it(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True})
    instance.launch_arguments(ROUTER)
    await instance.router_ready()

    instance.router_exited()

    status = await instance.status()
    assert status["router_profiled"] is False
    assert status["capture"] is None
    assert "exited during a capture" in status["last_error"]
    with pytest.raises(ProfilerError, match="not running under the profiler"):
        await instance.start_capture()
    with pytest.raises(ProfilerError, match="no capture is running"):
        await instance.stop_capture()


async def test_captures_can_be_found_and_deleted_by_name_only(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True})
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "one.nsys-rep").write_bytes(b"1")
    (tmp_path / "outside.nsys-rep").write_bytes(b"x")

    assert instance.capture_path("one.nsys-rep") == captures / "one.nsys-rep"
    with pytest.raises(FileNotFoundError):
        instance.capture_path("../outside.nsys-rep")
    instance.delete_capture("one.nsys-rep")

    assert instance.captures() == []
    assert (tmp_path / "outside.nsys-rep").exists()


async def test_a_running_capture_cannot_be_deleted(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"enabled": True})
    instance.launch_arguments(ROUTER)
    capture = await instance.start_capture()
    capture.path.write_bytes(b"partial")

    with pytest.raises(ProfilerError, match="stop the capture"):
        instance.delete_capture(capture.path.name)


async def test_settings_are_saved_and_bad_saved_values_fall_back(tmp_path: Path) -> None:
    instance, _, store = profiler(tmp_path, saved={"trace": "cuda; rm -rf"})

    assert instance.config() == ProfilerConfig()
    instance.save(ProfilerConfig(enabled=True, trace="cuda", cpu_sampling=False))

    assert store.values[PROFILER_SETTINGS]["trace"] == "cuda"
    assert instance.config().enabled is True


async def test_configured_nsys_path_must_exist_and_be_nsys(tmp_path: Path) -> None:
    instance, _, _ = profiler(tmp_path, saved={"nsys_path": str(tmp_path / "missing" / "nsys.exe")})

    assert instance.nsys_path() is None
    with pytest.raises(ValueError, match="nsys or nsys.exe"):
        ProfilerConfig(nsys_path="C:/Windows/System32/cmd.exe")
    with pytest.raises(ValueError, match="comma-separated"):
        ProfilerConfig(trace="cuda nvtx")


async def test_find_nsys_prefers_the_newest_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for version in ("2025.6.3", "2026.1.0", "2025.10.1"):
        install = tmp_path / "NVIDIA Corporation" / f"Nsight Systems {version}"
        target = install / "target-windows-x64"
        target.mkdir(parents=True)
        (target / "nsys.exe").write_bytes(b"")
    monkeypatch.setattr("llamawebui.services.router_profiler.sys.platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))

    found = find_nsys()

    assert found is not None
    assert found.parent.parent.name == "Nsight Systems 2026.1.0"


async def test_run_nsys_returns_exit_code_and_output() -> None:
    code, output = await run_nsys((sys.executable, "-c", "print('ready'); raise SystemExit(3)"), 30)

    assert code == 3
    assert output.strip() == "ready"


async def test_run_nsys_gives_up_after_the_timeout() -> None:
    code, output = await run_nsys((sys.executable, "-c", "import time; time.sleep(30)"), 0.5)

    assert code == -1
    assert "did not finish" in output


async def test_is_elevated_answers_without_failing() -> None:
    assert isinstance(is_elevated(), bool)
