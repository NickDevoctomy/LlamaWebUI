"""Own and supervise one llama.cpp router process."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
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

    async def terminate_tree(self, *, force: bool) -> None: ...

    async def wait(self) -> int: ...


RouterLauncher = Callable[[Sequence[str]], Awaitable[RouterProcess]]
RouterHealthProbe = Callable[[str, int], Awaitable[bool]]
RouterStateObserver = Callable[[RouterState, int | None, int | None], None]


@dataclass(frozen=True, slots=True)
class RouterRestartPolicy:
    max_attempts: int = 3
    window_seconds: float = 60.0
    delay_seconds: float = 1.0
    ready_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("restart max attempts must not be negative")
        if self.window_seconds <= 0:
            raise ValueError("restart window must be positive")
        if self.delay_seconds < 0:
            raise ValueError("restart delay must not be negative")
        if self.ready_timeout_seconds <= 0:
            raise ValueError("restart readiness timeout must be positive")


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


class ManagedRouterProcess:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self._process.stdout

    async def terminate_tree(self, *, force: bool) -> None:
        if os.name == "nt":
            if not force:
                with suppress(ProcessLookupError):
                    self._process.send_signal(
                        getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                    )
                return
            arguments = ["taskkill", "/PID", str(self.pid), "/T", "/F"]
            killer = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            result = await killer.wait()
            if result != 0 and self.returncode is None:
                self._process.kill()
            return

        try:
            force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(-self.pid, force_signal if force else signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            if self.returncode is None:
                if force:
                    self._process.kill()
                else:
                    self._process.terminate()

    async def wait(self) -> int:
        return await self._process.wait()


async def launch_router(arguments: Sequence[str]) -> RouterProcess:
    if os.name == "nt":
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    return ManagedRouterProcess(process)


class RouterSupervisor:
    def __init__(
        self,
        launcher: RouterLauncher = launch_router,
        health_probe: RouterHealthProbe = probe_router_health,
        *,
        log_capacity: int = 500,
        restart_policy: RouterRestartPolicy | None = None,
    ) -> None:
        self._launcher = launcher
        self._health_probe = health_probe
        self._process: RouterProcess | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._log_task: asyncio.Task[None] | None = None
        self._state = RouterState.STOPPED
        self._last_exit_code: int | None = None
        self._logs: deque[str] = deque(maxlen=log_capacity)
        self._state_observer: RouterStateObserver | None = None
        self._restart_policy = restart_policy
        self._restart_attempts: deque[float] = deque()
        self._restart_task: asyncio.Task[None] | None = None
        self._restart_enabled = False
        self._launch: RouterLaunch | None = None
        self._launch_arguments: tuple[str, ...] = ()
        self._timing: dict[str, float | int | None] = {
            "prompt_tokens_per_second": None,
            "decode_tokens_per_second": None,
            "decode_tokens_per_second_peak": None,
            "task_id": None,
            "context_tokens": None,
            "task_elapsed_seconds": None,
        }

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

    @property
    def timing(self) -> dict[str, float | int | None]:
        return dict(self._timing)

    @property
    def launch_arguments(self) -> tuple[str, ...]:
        return self._launch_arguments

    def set_state_observer(self, observer: RouterStateObserver | None) -> None:
        self._state_observer = observer

    async def start(self, launch: RouterLaunch) -> None:
        await self._cancel_restart()
        self._restart_attempts.clear()
        self._restart_enabled = True
        self._launch = launch
        await self._start_once(launch)

    async def _start_once(self, launch: RouterLaunch) -> None:
        arguments = launch.arguments()
        self._launch_arguments = arguments
        require_transition(self._state, RouterState.STARTING)
        self._set_state(RouterState.STARTING)
        self._last_exit_code = None
        try:
            process = await self._launcher(arguments)
        except Exception:
            self._set_state(RouterState.CRASHED)
            raise
        self._process = process
        self._notify_state()
        self._watch_task = asyncio.create_task(self._watch(process))
        if process.stdout is not None:
            self._log_task = asyncio.create_task(self._capture_logs(process.stdout))

    async def wait_until_ready(
        self,
        launch: RouterLaunch,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
        disable_restart_on_timeout: bool = True,
    ) -> None:
        if self._state is not RouterState.STARTING:
            raise ValueError(f"router is not starting: {self._state}")
        try:
            async with asyncio.timeout(timeout_seconds):
                while not await self._health_probe(launch.host, launch.port):
                    process = self._process
                    if process is None or process.returncode is not None:
                        if self._watch_task is not None:
                            await self._watch_task
                        raise RuntimeError(
                            f"router exited before becoming ready: {self._last_exit_code}"
                        )
                    await asyncio.sleep(poll_interval_seconds)
        except TimeoutError:
            if disable_restart_on_timeout:
                await self.stop()
            else:
                await self._stop_process()
                self._set_state(RouterState.CRASHED)
            raise TimeoutError(f"router did not become ready within {timeout_seconds:g}s") from None
        self.mark_ready()

    def mark_ready(self) -> None:
        require_transition(self._state, RouterState.READY)
        self._set_state(RouterState.READY)

    def mark_degraded(self) -> None:
        require_transition(self._state, RouterState.DEGRADED)
        self._set_state(RouterState.DEGRADED)

    async def stop(self, timeout_seconds: float = 10.0) -> None:
        self._restart_enabled = False
        await self._cancel_restart()
        await self._stop_process(timeout_seconds)

    async def _stop_process(self, timeout_seconds: float = 10.0) -> None:
        if self._state is RouterState.STOPPED:
            return
        if self._state is RouterState.CRASHED:
            require_transition(self._state, RouterState.STOPPED)
            self._state = RouterState.STOPPED
            return

        require_transition(self._state, RouterState.STOPPING)
        self._set_state(RouterState.STOPPING)
        process = self._process
        if process is None:
            self._set_state(RouterState.CRASHED)
            raise RuntimeError("router process is missing")

        await process.terminate_tree(force=False)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout_seconds)
        except TimeoutError:
            await process.terminate_tree(force=True)
            await process.wait()

        if self._state is RouterState.STOPPING:
            self._set_state(RouterState.STOPPED)
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
            self._set_state(RouterState.STOPPED)
        elif self._state is not RouterState.STOPPED:
            self._set_state(RouterState.CRASHED)
            self._process = None
            self._schedule_restart()

    def _schedule_restart(self) -> None:
        policy = self._restart_policy
        if (
            policy is None
            or policy.max_attempts == 0
            or not self._restart_enabled
            or self._restart_task is not None
        ):
            return
        self._restart_task = asyncio.create_task(self._restart_loop())

    async def _restart_loop(self) -> None:
        policy = self._restart_policy
        launch = self._launch
        assert policy is not None
        assert launch is not None
        try:
            while self._restart_enabled and self._state in {
                RouterState.CRASHED,
                RouterState.STOPPED,
            }:
                now = monotonic()
                while (
                    self._restart_attempts
                    and now - self._restart_attempts[0] > policy.window_seconds
                ):
                    self._restart_attempts.popleft()
                if len(self._restart_attempts) >= policy.max_attempts:
                    return
                self._restart_attempts.append(now)
                await asyncio.sleep(policy.delay_seconds)
                if not self._restart_enabled:
                    return
                try:
                    await self._start_once(launch)
                    await self.wait_until_ready(
                        launch,
                        timeout_seconds=policy.ready_timeout_seconds,
                        disable_restart_on_timeout=False,
                    )
                except (OSError, RuntimeError, TimeoutError, ValueError):
                    continue
                return
        finally:
            self._restart_task = None

    async def _cancel_restart(self) -> None:
        task = self._restart_task
        if task is None or task is asyncio.current_task():
            return
        self._restart_task = None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _capture_logs(self, output: RouterOutput) -> None:
        while line := await output.readline():
            text = line.decode(errors="replace").rstrip("\r\n")
            self._logs.append(text)
            self._parse_timing(text)

    def _parse_timing(self, text: str) -> None:
        prompt = re.search(
            r"prompt processing, n_tokens\s*=\s*\d+.*?t\s*=\s*([\d.]+)"
            r"\s*s\s*/\s*([\d.]+) tokens per second",
            text,
        )
        generation = re.search(
            r"task\s+(\d+).*?n_gen\s*=.*?tg\s*=\s*([\d.]+) t/s"
            r"(?:, tg_3s\s*=\s*([\d.]+))?",
            text,
        )
        if prompt:
            self._timing["prompt_tokens_per_second"] = float(prompt.group(2))
        if generation:
            self._timing["task_id"] = int(generation.group(1))
            self._timing["decode_tokens_per_second"] = float(generation.group(2))
            if generation.group(3):
                self._timing["decode_tokens_per_second_peak"] = float(generation.group(3))
        context = re.search(
            r"(?:context|n_ctx)\s*[=:]\s*(\d+)(?:\s*/\s*(\d+))?",
            text,
            re.IGNORECASE,
        )
        if context:
            self._timing["context_tokens"] = int(context.group(1))

    def _set_state(self, state: RouterState) -> None:
        self._state = state
        self._notify_state()

    def _notify_state(self) -> None:
        if self._state_observer is not None:
            self._state_observer(self._state, self.pid, self._last_exit_code)