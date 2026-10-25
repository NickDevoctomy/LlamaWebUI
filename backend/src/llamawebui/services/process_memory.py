"""Measure the memory each model process actually holds, as the OS reports it.

The router runs every model in its own llama-server child process, started with
``--alias``. A speculative-decoding draft loads into its main model's process, so
the two cannot be told apart here.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
from collections.abc import Callable
from time import monotonic

import psutil  # type: ignore[import-untyped]

GpuMemory = dict[int, dict[str, int | None]]

_PDH_FMT_LARGE = 0x00000400
_PDH_MORE_DATA = 0x800007D2


def _alias(arguments: list[str]) -> str | None:
    for index, argument in enumerate(arguments[:-1]):
        if argument == "--alias":
            return arguments[index + 1]
    return None


def _resident_bytes(process: psutil.Process) -> tuple[int, int]:
    """Private resident memory and resident pages of memory-mapped model files."""
    private = int(process.memory_full_info().uss)
    mapped = sum(
        int(region.rss)
        for region in process.memory_maps(grouped=True)
        if str(region.path).lower().endswith(".gguf")
    )
    if sys.platform != "win32":
        # USS counts file pages that only this process maps; Windows keeps those
        # shareable pages out of its private working set.
        private = max(private - mapped, 0)
    return private, mapped


def measure_model_processes(
    router_pid: int, gpu_memory: Callable[[], GpuMemory | None] | None = None
) -> list[dict[str, object]]:
    try:
        children = psutil.Process(router_pid).children(recursive=True)
    except psutil.Error:
        return []
    gpu = (gpu_memory or gpu_memory_by_pid)() or {}
    measured: list[dict[str, object]] = []
    for child in children:
        try:
            alias = _alias(child.cmdline())
            if alias is None:
                continue
            ram, mapped = _resident_bytes(child)
        except psutil.Error:
            continue
        device = gpu.get(child.pid, {})
        measured.append(
            {
                "model": alias,
                "pid": child.pid,
                "vram_bytes": device.get("dedicated"),
                "gpu_shared_bytes": device.get("shared"),
                "ram_bytes": ram,
                "mapped_bytes": mapped,
            }
        )
    return measured


def gpu_memory_by_pid() -> GpuMemory | None:
    """Per-process GPU memory: dedicated VRAM and, on Windows, shared system memory."""
    if sys.platform == "win32":
        dedicated = _windows_gpu_counter("Dedicated Usage")
        if dedicated is None:
            return None
        shared = _windows_gpu_counter("Shared Usage") or {}
        return {
            pid: {"dedicated": size, "shared": shared.get(pid)}
            for pid, size in dedicated.items()
        }
    return _nvidia_process_memory()


def _nvidia_process_memory() -> GpuMemory | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    memory: GpuMemory = {}
    for line in result.stdout.splitlines():
        pid, _, used = (value.strip() for value in line.partition(","))
        if pid.isdigit() and used.isdigit():
            entry = memory.setdefault(int(pid), {"dedicated": 0, "shared": None})
            entry["dedicated"] = (entry["dedicated"] or 0) + int(used) * 1024 * 1024
    return memory


class _CounterValue(ctypes.Structure):
    class _Value(ctypes.Union):
        _fields_ = [("large", ctypes.c_longlong), ("double", ctypes.c_double)]

    _fields_ = [("status", ctypes.c_ulong), ("value", _Value)]


class _CounterItem(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("value", _CounterValue)]


def _windows_gpu_counter(counter: str) -> dict[int, int] | None:
    """Read a ``GPU Process Memory`` performance counter, summed per process."""
    if sys.platform != "win32":
        return None
    try:
        pdh = ctypes.windll.pdh
    except OSError:
        return None
    query = ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return None
    try:
        handle = ctypes.c_void_p()
        path = f"\\GPU Process Memory(*)\\{counter}"
        # The English name works whatever language Windows is installed in.
        if pdh.PdhAddEnglishCounterW(query, path, None, ctypes.byref(handle)) != 0:
            return None
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        status = pdh.PdhGetFormattedCounterArrayW(
            handle, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None
        )
        if status & 0xFFFFFFFF != _PDH_MORE_DATA:
            return {} if status == 0 else None
        buffer = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(
            handle, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buffer
        ) != 0:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem * count.value)).contents
        usage: dict[int, int] = {}
        for item in items:
            # Instances look like "pid_13924_luid_0x00000000_0x000156ba_phys_0".
            parts = (item.name or "").split("_")
            if item.value.status == 0 and len(parts) > 1 and parts[1].isdigit():
                pid = int(parts[1])
                usage[pid] = usage.get(pid, 0) + int(item.value.value.large)
        return usage
    finally:
        pdh.PdhCloseQuery(query)


class ProcessMemorySampler:
    """Caches measurements briefly; reading process memory maps takes a moment."""

    def __init__(
        self,
        measure: Callable[[int], list[dict[str, object]]] = measure_model_processes,
        *,
        max_age_seconds: float = 3.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._measure = measure
        self._max_age = max_age_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sample: tuple[int, float, list[dict[str, object]]] | None = None

    def sample(self, router_pid: int | None) -> list[dict[str, object]]:
        if router_pid is None:
            return []
        with self._lock:
            now = self._clock()
            if (
                self._sample is not None
                and self._sample[0] == router_pid
                and now - self._sample[1] < self._max_age
            ):
                return self._sample[2]
            measured = self._measure(router_pid)
            self._sample = (router_pid, now, measured)
            return measured

    def model(self, router_pid: int | None, alias: str) -> dict[str, object] | None:
        """One model process's memory, measured now; the fresh reading is cached for
        everyone else too."""
        if router_pid is None:
            return None
        with self._lock:
            measured = self._measure(router_pid)
            self._sample = (router_pid, self._clock(), measured)
        return next((entry for entry in measured if entry.get("model") == alias), None)
