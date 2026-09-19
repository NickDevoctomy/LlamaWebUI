"""Own and supervise one llama.cpp router process."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState, require_transition


class RouterProcess(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


RouterLauncher = Callable[[Sequence[str]], Awaitable[RouterProcess]]


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
    def __init__(self, launcher: RouterLauncher = launch_router) -> None:
        self._launcher = launcher
        self._process: RouterProcess | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._state = RouterState.STOPPED
        self._last_exit_code: int | None = None

    @property
    def state(self) -> RouterState:
        return self._state

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

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