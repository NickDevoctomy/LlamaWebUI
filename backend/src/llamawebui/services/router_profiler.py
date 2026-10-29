"""Profile the llama.cpp router with NVIDIA Nsight Systems (``nsys``).

When profiling is on, the router is started under ``nsys profile --start-later`` as a named
session, which follows it into the llama-server process it starts per model. Nothing is
recorded until a capture starts (``nsys start``); ``nsys stop`` writes the report and leaves
the router running. The supervisor stops a capture before it stops the router, because
stopping the router ends nsys before it can write anything.

``nsys launch`` would be the obvious fit, but on Windows it records no CPU samples even when
elevated; ``nsys profile`` does. Its sampling options are fixed when the router starts.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import re
import shutil
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llamawebui.services.settings_store import SettingsStore

LOGGER = logging.getLogger("llamawebui.profiler")

PROFILER_SETTINGS = "debug.profiler"
REPORT_SUFFIX = ".nsys-rep"
_TRACE = re.compile(r"^[a-z0-9-]+(,[a-z0-9-]+)*$")
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")
_START_ATTEMPTS = 5
_MIN_SAMPLING_HZ = 100
_MAX_SAMPLING_HZ = 8000
_COMMAND_TIMEOUT_SECONDS = 60.0
# Writing a report with CPU samples from a long capture can take minutes.
_STOP_TIMEOUT_SECONDS = 600.0

NsysRunner = Callable[[Sequence[str], float], Awaitable[tuple[int, str]]]


class ProfilerError(Exception):
    """A profiler action that cannot be done in the current state."""


@dataclass(frozen=True, slots=True)
class ProfilerConfig:
    enabled: bool = False
    # Empty means use the newest Nsight Systems install found.
    nsys_path: str = ""
    trace: str = "cuda,nvtx,cublas"
    cpu_sampling: bool = True
    # nsys allows 100-8000 Hz. Its default of 1000 Hz turns a 2-minute benchmark into
    # millions of samples and a stop that waits minutes for the report.
    sampling_hz: int = 250
    capture_on_start: bool = True
    # Record each kernel inside a CUDA graph, not just the graph. llama.cpp runs most of
    # its work as graphs, but per-node tracing slows the router down.
    graph_nodes: bool = False

    def __post_init__(self) -> None:
        if not _MIN_SAMPLING_HZ <= self.sampling_hz <= _MAX_SAMPLING_HZ:
            raise ValueError(
                f"CPU samples per second must be {_MIN_SAMPLING_HZ}-{_MAX_SAMPLING_HZ}"
            )
        if not _TRACE.fullmatch(self.trace):
            raise ValueError("trace must be a comma-separated list such as cuda,nvtx,cublas")
        if self.nsys_path and Path(self.nsys_path).stem.lower() != "nsys":
            raise ValueError("the Nsight Systems executable must be nsys or nsys.exe")


@dataclass(frozen=True, slots=True)
class Capture:
    path: Path
    started_at: datetime


async def run_nsys(arguments: Sequence[str], timeout_seconds: float) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        return -1, f"nsys did not finish within {timeout_seconds:g}s"
    return process.returncode or 0, output.decode(errors="replace")


def find_nsys() -> Path | None:
    """The newest Nsight Systems ``nsys`` installed, or one on PATH."""
    if sys.platform == "win32":
        root = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "NVIDIA Corporation"
        installs = sorted(
            root.glob("Nsight Systems */target-windows-x64/nsys.exe"),
            key=lambda path: _version_key(path.parent.parent.name),
            reverse=True,
        )
        if installs:
            return installs[0]
    found = shutil.which("nsys")
    return Path(found) if found else None


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", name))


def is_elevated() -> bool:
    """Whether this process may record CPU samples, which needs administrator rights."""
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    else:
        return os.geteuid() == 0


class RouterProfiler:
    """Profiler settings, the router's nsys session and the reports it writes."""

    def __init__(
        self,
        settings_store: SettingsStore,
        captures_dir: Path,
        *,
        runner: NsysRunner | None = None,
        locate: Callable[[], Path | None] | None = None,
        elevated: Callable[[], bool] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = settings_store
        self.captures_dir = captures_dir
        self._runner = runner or run_nsys
        self._locate = locate or find_nsys
        self._elevated = elevated or is_elevated
        self._clock = clock
        self._lock = asyncio.Lock()
        self._versions: dict[Path, str | None] = {}
        # Set while the router runs under nsys: the session, nsys and the runtime folder name.
        self._session: str | None = None
        self._nsys: Path | None = None
        self._label = "router"
        self._capture: Capture | None = None
        self._saving: Capture | None = None
        # nsys writes a session's first capture to the --output given to nsys profile, not to
        # nsys start; it lands here, out of the report list, and is renamed after the stop.
        self._pending: Path | None = None
        self._log_file: Path | None = None
        self._last_error: str | None = None

    def config(self) -> ProfilerConfig:
        saved = self._store.get_json(PROFILER_SETTINGS) or {}
        known = {key: saved[key] for key in ProfilerConfig.__dataclass_fields__ if key in saved}
        try:
            return ProfilerConfig(**known)
        except (TypeError, ValueError):
            return ProfilerConfig()

    def save(self, config: ProfilerConfig) -> ProfilerConfig:
        self._store.set_json(PROFILER_SETTINGS, asdict(config))
        return config

    def nsys_path(self, config: ProfilerConfig | None = None) -> Path | None:
        config = config or self.config()
        if config.nsys_path:
            path = Path(config.nsys_path)
            return path if path.is_file() else None
        return self._locate()

    async def status(self) -> dict[str, Any]:
        config = self.config()
        nsys = self.nsys_path(config)
        capture = self._capture
        return {
            "config": asdict(config),
            "nsys": {
                "path": str(nsys) if nsys else None,
                "version": await self._version(nsys) if nsys else None,
            },
            "elevated": self._elevated(),
            "router_profiled": self._session is not None,
            "capture": None
            if capture is None
            else {"name": capture.path.name, "started_at": capture.started_at.isoformat()},
            "saving": self.saving(),
            "last_error": self._last_error,
            "captures": self.captures(),
        }

    def saving(self) -> dict[str, str] | None:
        """The report nsys is writing now, if any."""
        saving = self._saving
        if saving is None:
            return None
        return {"name": saving.path.name, "started_at": saving.started_at.isoformat()}

    async def _version(self, nsys: Path) -> str | None:
        if nsys not in self._versions:
            code, output = await self._runner((str(nsys), "--version"), _COMMAND_TIMEOUT_SECONDS)
            match = re.search(r"version\s+(\d+(?:\.\d+)+)", output)
            self._versions[nsys] = match.group(1) if code == 0 and match else None
        return self._versions[nsys]

    def captures(self) -> list[dict[str, Any]]:
        if not self.captures_dir.is_dir():
            return []
        reports = sorted(
            self.captures_dir.glob(f"*{REPORT_SUFFIX}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
                "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
            for path in reports
        ]

    def capture_path(self, name: str) -> Path:
        """A saved report by name; only names the folder actually holds are accepted."""
        for path in self.captures_dir.glob(f"*{REPORT_SUFFIX}"):
            if path.name == name:
                return path
        raise FileNotFoundError(f"no capture named {name}")

    def delete_capture(self, name: str) -> None:
        path = self.capture_path(name)
        if self._capture is not None and self._capture.path == path:
            raise ProfilerError("stop the capture before deleting it")
        path.unlink()

    # Router hooks, called by the supervisor.

    def launch_arguments(self, arguments: Sequence[str]) -> tuple[str, ...]:
        """The router command, run under ``nsys profile`` when profiling is on."""
        self._session = None
        self._capture = None
        self._pending = None
        self._log_file = None
        config = self.config()
        if not config.enabled:
            return tuple(arguments)
        nsys = self.nsys_path(config)
        if nsys is None:
            self._last_error = "profiling is on but Nsight Systems was not found"
            LOGGER.warning(
                "profiler_unavailable",
                extra={"component": "profiler", "event": "unavailable"},
            )
            return tuple(arguments)
        self._session = f"llamawebui-{os.getpid()}"
        self._nsys = nsys
        self._label = Path(arguments[0]).parent.name if arguments else "router"
        self._pending = self.captures_dir / ".pending" / f"{self._session}{REPORT_SUFFIX}"
        self._pending.parent.mkdir(parents=True, exist_ok=True)
        self._pending.unlink(missing_ok=True)
        # nsys profile runs the router in a console of its own, so its output never reaches
        # the supervisor; the router writes its log, with its model processes' lines, here.
        self._log_file = self.captures_dir.parent / "router.log"
        self._log_file.unlink(missing_ok=True)
        self._last_error = None
        sampling = "process-tree" if config.cpu_sampling and self._elevated() else "none"
        return (
            str(nsys),
            "profile",
            f"--session-new={self._session}",
            "--start-later=true",
            f"--trace={config.trace}",
            f"--cuda-graph-trace={'node' if config.graph_nodes else 'graph'}",
            f"--sample={sampling}",
            f"--cpuctxsw={sampling}",
            *((f"--sampling-frequency={config.sampling_hz}",) if sampling != "none" else ()),
            "--force-overwrite=true",
            f"--output={_without_suffix(self._pending)}",
            *arguments,
            "--log-file",
            str(self._log_file),
        )

    def log_file(self) -> Path | None:
        return self._log_file

    async def router_ready(self) -> None:
        if self._session is None or not self.config().capture_on_start:
            return
        try:
            await self.start_capture()
        except ProfilerError as error:
            # The router runs either way; the error shows in the Debug tab.
            self._last_error = str(error)

    async def router_stopping(self) -> None:
        if self._capture is None:
            return
        try:
            await self.stop_capture()
        except ProfilerError as error:
            self._last_error = str(error)

    def router_exited(self) -> None:
        if self._capture is not None:
            name = self._capture.path.name
            self._last_error = f"the router exited during a capture; {name} was lost"
        if self._pending is not None:
            self._pending.unlink(missing_ok=True)
        self._session = None
        self._capture = None
        self._pending = None

    # Captures.

    async def start_capture(self) -> Capture:
        async with self._lock:
            session, nsys = self._session, self._nsys
            if session is None or nsys is None:
                raise ProfilerError("the router is not running under the profiler")
            if self._capture is not None:
                raise ProfilerError("a capture is already running")
            started_at = self._clock()
            label = _SAFE_LABEL.sub("-", self._label).strip("-") or "router"
            path = self.captures_dir / f"{started_at:%Y%m%d-%H%M%S}-{label}{REPORT_SUFFIX}"
            self.captures_dir.mkdir(parents=True, exist_ok=True)
            arguments = (
                str(nsys),
                "start",
                f"--session={session}",
                "--force-overwrite=true",
                f"--output={_without_suffix(path)}",
            )
            output = ""
            # The session may not be registered the moment the router answers.
            for attempt in range(_START_ATTEMPTS):
                code, output = await self._runner(arguments, _COMMAND_TIMEOUT_SECONDS)
                if code == 0:
                    self._capture = Capture(path, started_at)
                    self._last_error = None
                    return self._capture
                await asyncio.sleep(0.5 * (attempt + 1))
            raise ProfilerError(f"nsys could not start a capture: {_last_line(output)}")

    async def stop_capture(self) -> Path:
        async with self._lock:
            capture, session, nsys = self._capture, self._session, self._nsys
            if capture is None or session is None or nsys is None:
                raise ProfilerError("no capture is running")
            self._capture = None
            # Writing a report with CPU samples can take minutes; say so meanwhile.
            self._saving = Capture(capture.path, self._clock())
            try:
                code, output = await self._runner(
                    (str(nsys), "stop", f"--session={session}"), _STOP_TIMEOUT_SECONDS
                )
            finally:
                self._saving = None
            pending = self._pending
            first_capture = pending is not None and pending.is_file()
            if code == 0 and not capture.path.is_file() and pending is not None and first_capture:
                pending.replace(capture.path)
            if code != 0 or not capture.path.is_file():
                raise ProfilerError(f"nsys could not write the report: {_last_line(output)}")
            self._last_error = None
            LOGGER.info(
                "profiler_capture_saved",
                extra={
                    "component": "profiler",
                    "event": "capture_saved",
                    "path": str(capture.path),
                },
            )
            return capture.path


def _without_suffix(path: Path) -> str:
    """A report path as nsys --output takes it: nsys adds the extension itself."""
    return str(path).removesuffix(REPORT_SUFFIX)


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"
