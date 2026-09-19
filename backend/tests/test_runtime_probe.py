from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from llamawebui.services.runtime_probe import CommandResult, probe_runtime, run_command

pytestmark = pytest.mark.asyncio


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    path = tmp_path / "llama-server.exe"
    path.touch()
    return path


async def test_probe_runtime_collects_capabilities_and_devices(executable: Path) -> None:
    async def runner(arguments: Sequence[str], timeout: float) -> CommandResult:
        assert timeout == 3.0
        outputs = {
            "--version": CommandResult(0, "version: 10964 (b29c606e)", ""),
            "--help": CommandResult(0, "--models-preset PATH\n--override-tensor RULE", ""),
            "--list-devices": CommandResult(0, "CUDA0: NVIDIA GPU", ""),
        }
        return outputs[arguments[-1]]

    result = await probe_runtime(executable, runner=runner, timeout_seconds=3.0)

    assert result.usable
    assert result.version.build == "10964"
    assert result.version.commit == "b29c606e"
    assert result.capabilities.supports("models-preset")
    assert result.devices_output == "CUDA0: NVIDIA GPU"
    assert result.errors == ()


async def test_probe_runtime_records_failures_without_losing_successes(executable: Path) -> None:
    async def runner(arguments: Sequence[str], _timeout: float) -> CommandResult:
        if arguments[-1] == "--version":
            raise TimeoutError
        if arguments[-1] == "--list-devices":
            raise OSError("missing dependency")
        return CommandResult(0, "--models-preset PATH", "")

    result = await probe_runtime(executable, runner=runner)

    assert not result.usable
    assert result.capabilities.supports("models-preset")
    assert result.devices_output is None
    assert result.errors == (
        "version probe timed out after 15s",
        "devices probe could not start: missing dependency",
    )


async def test_probe_runtime_records_nonzero_exit(executable: Path) -> None:
    async def runner(arguments: Sequence[str], _timeout: float) -> CommandResult:
        if arguments[-1] == "--help":
            return CommandResult(4, "", "bad option")
        return CommandResult(0, "version: 1", "")

    result = await probe_runtime(executable, runner=runner)

    assert not result.usable
    assert "help probe exited with 4: bad option" in result.errors


async def test_probe_runtime_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="llama-server executable not found"):
        await probe_runtime(tmp_path / "missing.exe")


async def test_run_command_captures_stdout_and_stderr() -> None:
    result = await run_command(
        (
            sys.executable,
            "-c",
            "import sys; print('ready'); print('warning', file=sys.stderr)",
        ),
        5.0,
    )

    assert result.returncode == 0
    assert result.stdout == "ready"
    assert result.stderr == "warning"
    assert result.combined_output == "ready\nwarning"


async def test_run_command_kills_process_after_timeout() -> None:
    with pytest.raises(TimeoutError):
        await run_command((sys.executable, "-c", "import time; time.sleep(30)"), 0.01)