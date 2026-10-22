"""Best-effort description of the host, used to match recommended profiles."""

from __future__ import annotations

import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import psutil  # type: ignore[import-untyped]

# Display adapters Windows lists that are not graphics hardware.
_VIRTUAL_ADAPTER = re.compile(r"virtual|basic display|basic render|remote display|mirror", re.I)


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    cpu: str | None
    memory_bytes: int
    gpus: tuple[str, ...]
    discrete_vram_bytes: int

    def to_payload(self) -> dict[str, object]:
        return {
            "cpu": self.cpu,
            "memory_bytes": self.memory_bytes,
            "gpus": list(self.gpus),
            "discrete_vram_bytes": self.discrete_vram_bytes,
        }


@cache
def detect_hardware() -> HardwareProfile:
    """Detect once per process; hardware does not change while the app runs."""
    nvidia = _nvidia_gpus()
    gpus = list(_gpu_names())
    gpus.extend(name for name, _ in nvidia if name not in gpus)
    return HardwareProfile(
        cpu=_cpu_name(),
        memory_bytes=int(psutil.virtual_memory().total),
        gpus=tuple(gpus),
        discrete_vram_bytes=sum(memory for _, memory in nvidia),
    )


def _run(command: list[str], timeout: float = 5) -> str | None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def _cpu_name() -> str | None:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return str(value).strip() or None
        except OSError:
            return platform.processor() or None
    if sys.platform == "darwin":
        output = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        return output.strip() if output else None
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return platform.processor() or None
    match = re.search(r"^model name\s*:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _gpu_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        output = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }",
            ]
        )
    elif sys.platform == "darwin":
        output = _run(["system_profiler", "SPDisplaysDataType"])
        if output is None:
            return ()
        return tuple(
            match.group(1).strip()
            for match in re.finditer(r"Chipset Model:\s*(.+)", output)
        )
    else:
        output = _run(["lspci"])
        if output is None:
            return ()
        return tuple(
            line.split(": ", 1)[1].strip()
            for line in output.splitlines()
            if re.search(r"VGA compatible|3D controller|Display controller", line)
            and ": " in line
        )
    if output is None:
        return ()
    return tuple(
        line.strip()
        for line in output.splitlines()
        if line.strip() and not _VIRTUAL_ADAPTER.search(line)
    )


def _nvidia_gpus() -> tuple[tuple[str, int], ...]:
    output = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        timeout=2,
    )
    if output is None:
        return ()
    gpus: list[tuple[str, int]] = []
    for line in output.splitlines():
        name, _, memory = line.rpartition(",")
        try:
            gpus.append((name.strip(), int(memory.strip()) * 1024 * 1024))
        except ValueError:
            continue
    return tuple(gpus)
