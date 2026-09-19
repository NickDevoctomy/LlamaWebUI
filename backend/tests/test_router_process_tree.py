from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from llamawebui.domain.router_lifecycle import RouterLaunch
from llamawebui.services.router_supervisor import (
    RouterProcess,
    RouterSupervisor,
    launch_router,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.name != "nt", reason="Windows process-tree verification"),
]


async def windows_process_exists(pid: int) -> bool:
    process = await asyncio.create_subprocess_exec(
        "tasklist",
        "/FI",
        f"PID eq {pid}",
        "/FO",
        "CSV",
        "/NH",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    return f'"{pid}"' in stdout.decode(errors="replace")


async def force_cleanup(pid: int) -> None:
    process = await asyncio.create_subprocess_exec(
        "taskkill",
        "/PID",
        str(pid),
        "/T",
        "/F",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


async def test_windows_router_termination_stops_child_process(tmp_path: Path) -> None:
    parent_code = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        "time.sleep(120)']); "
        "print(child.pid, flush=True); time.sleep(120)"
    )
    process = await launch_router((sys.executable, "-u", "-c", parent_code))
    child_pid: int | None = None
    try:
        assert process.stdout is not None
        child_pid = int((await asyncio.wait_for(process.stdout.readline(), 5)).decode().strip())
        assert await windows_process_exists(process.pid)
        assert await windows_process_exists(child_pid)

        preset = tmp_path / "models.ini"
        preset.touch()

        async def existing_process(arguments: Sequence[str]) -> RouterProcess:
            return process

        supervisor = RouterSupervisor(existing_process)
        await supervisor.start(RouterLaunch(Path(sys.executable), preset))
        supervisor.mark_ready()
        await supervisor.stop(timeout_seconds=0.25)
        async with asyncio.timeout(5):
            while await windows_process_exists(child_pid):
                await asyncio.sleep(0.05)

        assert not await windows_process_exists(process.pid)
        assert not await windows_process_exists(child_pid)
    finally:
        if process.returncode is None:
            await process.terminate_tree(force=True)
            await process.wait()
        if child_pid is not None and await windows_process_exists(child_pid):
            await force_cleanup(child_pid)