from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from llamawebui.services import process_memory
from llamawebui.services.process_memory import (
    ProcessMemorySampler,
    gpu_memory_by_pid,
    measure_model_processes,
)

GIB = 1024**3


class FakeChild:
    def __init__(self, pid: int, arguments: list[str], *, fail: bool = False) -> None:
        self.pid = pid
        self._arguments = arguments
        self._fail = fail

    def cmdline(self) -> list[str]:
        if self._fail:
            raise process_memory.psutil.AccessDenied(self.pid)
        return self._arguments

    def memory_full_info(self) -> SimpleNamespace:
        return SimpleNamespace(uss=10 * GIB)

    def memory_maps(self, grouped: bool = True) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(path="E:\\models\\qwen-00001-of-00002.GGUF", rss=2 * GIB),
            SimpleNamespace(path="E:\\models\\qwen-00002-of-00002.gguf", rss=1 * GIB),
            SimpleNamespace(path="C:\\Windows\\System32\\kernel32.dll", rss=GIB),
        ]


def patch_router(monkeypatch: pytest.MonkeyPatch, children: list[FakeChild]) -> None:
    router = SimpleNamespace(children=lambda recursive: children)
    monkeypatch.setattr(process_memory.psutil, "Process", lambda pid: router)


def test_measures_each_model_process_by_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_router(
        monkeypatch,
        [
            FakeChild(11, ["llama-server", "--port", "5001", "--alias", "qwen"]),
            FakeChild(12, ["conhost.exe"]),
            FakeChild(13, ["llama-server", "--alias", "denied"], fail=True),
        ],
    )

    measured = measure_model_processes(
        1, lambda: {11: {"dedicated": 20 * GIB, "shared": 4 * GIB}}
    )

    private = 10 * GIB if sys.platform == "win32" else 7 * GIB
    assert measured == [
        {
            "model": "qwen",
            "pid": 11,
            "vram_bytes": 20 * GIB,
            "gpu_shared_bytes": 4 * GIB,
            "ram_bytes": private,
            "mapped_bytes": 3 * GIB,
        }
    ]


def test_reports_nothing_when_the_router_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(pid: int) -> None:
        raise process_memory.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(process_memory.psutil, "Process", missing)

    assert measure_model_processes(1, lambda: None) == []


def test_unmeasured_gpu_memory_is_reported_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_router(monkeypatch, [FakeChild(11, ["llama-server", "--alias", "qwen"])])

    measured = measure_model_processes(1, lambda: None)

    assert measured[0]["vram_bytes"] is None
    assert measured[0]["gpu_shared_bytes"] is None


def test_reads_nvidia_process_memory_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_memory.sys, "platform", "linux")
    monkeypatch.setattr(
        process_memory.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="11, 1024\n11, 512\n12, [N/A]\n"),
    )

    assert gpu_memory_by_pid() == {11: {"dedicated": 1536 * 1024 * 1024, "shared": None}}


def test_missing_nvidia_tools_report_no_gpu_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("nvidia-smi", 2)

    monkeypatch.setattr(process_memory.sys, "platform", "linux")
    monkeypatch.setattr(process_memory.subprocess, "run", unavailable)

    assert gpu_memory_by_pid() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows performance counters")
def test_reads_windows_gpu_process_counters() -> None:
    memory = gpu_memory_by_pid()

    assert memory is not None
    assert all(
        isinstance(pid, int) and isinstance(entry["dedicated"], int)
        for pid, entry in memory.items()
    )


def test_sampler_caches_briefly_per_router() -> None:
    calls: list[int] = []
    now = [0.0]

    def measure(pid: int) -> list[dict[str, object]]:
        calls.append(pid)
        return [{"pid": pid}]

    sampler = ProcessMemorySampler(measure, max_age_seconds=3, clock=lambda: now[0])

    assert sampler.sample(None) == []
    assert sampler.sample(1) == [{"pid": 1}]
    now[0] = 2
    sampler.sample(1)
    sampler.sample(2)
    now[0] = 6
    sampler.sample(2)

    assert calls == [1, 2, 2]
