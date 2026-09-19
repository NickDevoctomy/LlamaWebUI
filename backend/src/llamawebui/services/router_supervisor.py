"""Own and supervise one llama.cpp router process."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

import httpx

from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState, require_transition


class RouterOutput(Protocol):
    async def readline(self) -> bytes: ...


class RouterProcess(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def stdout(self) -> RouterOutput | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


RouterLauncher = Callable[[Sequence[str]], Awaitable[RouterProcess]]
RouterHealthProbe = Callable[[str, int], Awaitable[bool]]


async def probe_router_health(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    if probe_host == "::":
        probe_host = "::1"
    formatted_host = f"[{probe_host}]" if ":" in probe_host else probe_host
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"http://{formatted_host}:{port}/health")
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def launch_router(arguments: Sequence[str]) -> RouterProcess:
    if os.name == "nt":
        return await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


class RouterSupervisor:
    def __init__(
        self,
        launcher: RouterLauncher = launch_router,
        health_probe: RouterHealthProbe = probe_router_health,
        *,
        log_capacity: int = 500,
    ) -> None:
        self._launcher = launcher
        self._health_probe = health_probe
        self._process: RouterProcess | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._log_task: asyncio.Task[None] | None = None
        self._state = RouterState.STOPPED
        self._last_exit_code: int | None = None
        self._logs: deque[str] = deque(maxlen=log_capacity)

    @property
    def state(self) -> RouterState:
        return self._state

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

    @property
    def logs(self) -> tuple[str, ...]:
        return tuple(self._logs)

    async def start(self, launch: RouterLaunch) -> None:
        arguments = launch.arguments()
        require_transition(self._state, RouterState.STARTING)
        self._state = RouterState.STARTING
        self._last_exit_code = None
        try:
            process = await self._launcher(arguments)
        except Exception:
            self._state = RouterState.CRASHED
            raise
        self._process = process
        self._watch_task = asyncio.create_task(self._watch(process))
        if process.stdout is not None:
            self._log_task = asyncio.create_task(self._capture_logs(process.stdout))

    async def wait_until_ready(
        self,
        launch: RouterLaunch,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if self._state is not RouterState.STARTING:
            raise ValueError(f"router is not starting: {self._state}")
        try:
            async with asyncio.timeout(timeout_seconds):
                while not await self._health_probe(launch.host, launch.port):
                    process = self._process
                    if process is None or process.returncode is not None:
                        raise RuntimeError(
                            f"router exited before becoming ready: {self._last_exit_code}"
                        )
                    await asyncio.sleep(poll_interval_seconds)
        except TimeoutError:
            await self.stop()
            raise TimeoutError(f"router did not become ready within {timeout_seconds:g}s") from None
        self.mark_ready()

    def mark_ready(self) -> None:
        require_transition(self._state, RouterState.READY)
        self._state = RouterState.READY

    def mark_degraded(self) -> None:
        require_transition(self._state, RouterState.DEGRADED)
        self._state = RouterState.DEGRADED

    async def stop(self, timeout_seconds: float = 10.0) -> None:
        if self._state is RouterState.STOPPED:
            return
        if self._state is RouterState.CRASHED:
            require_transition(self._state, RouterState.STOPPED)
            self._state = RouterState.STOPPED
            return

        require_transition(self._state, RouterState.STOPPING)
        self._state = RouterState.STOPPING
        process = self._process
        if process is None:
            self._state = RouterState.CRASHED
            raise RuntimeError("router process is missing")

        process.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()

        if self._state is RouterState.STOPPING:
            self._state = RouterState.STOPPED
        self._process = None
        if self._watch_task is not None:
            await self._watch_task
            self._watch_task = None
        if self._log_task is not None:
            await self._log_task
            self._log_task = None

    async def _watch(self, process: RouterProcess) -> None:
        exit_code = await process.wait()
        if process is not self._process:
            return
        self._last_exit_code = exit_code
        if self._state is RouterState.STOPPING:
            self._state = RouterState.STOPPED
        elif self._state is not RouterState.STOPPED:
            self._state = RouterState.CRASHED
            self._process = None

    async def _capture_logs(self, output: RouterOutput) -> None:
        while line := await output.readline():
            self._logs.append(line.decode(errors="replace").rstrip("\r\n"))