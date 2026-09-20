"""Best-effort host telemetry for the operator dashboard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil  # type: ignore[import-untyped]


def collect_system_metrics(data_dir: Path) -> dict[str, object]:
    disk = psutil.disk_usage(str(data_dir))
    memory = psutil.virtual_memory()
    network = psutil.net_io_counters()
    disk_io = psutil.disk_io_counters()
    gpu = _nvidia_metrics()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_bytes": memory.used,
        "ram_total_bytes": memory.total,
        "network_sent_bytes": network.bytes_sent,
        "network_received_bytes": network.bytes_recv,
        "disk_read_bytes": disk_io.read_bytes if disk_io else None,
        "disk_write_bytes": disk_io.write_bytes if disk_io else None,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "gpu": gpu,
        "gpu_supported": gpu is not None,
    }


def _nvidia_metrics() -> dict[str, int] | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    values = [value.strip() for value in result.stdout.split(",")]
    if len(values) != 3:
        return None
    try:
        return {
            "utilization_percent": int(values[0]),
            "memory_used_bytes": int(values[1]) * 1024 * 1024,
            "memory_total_bytes": int(values[2]) * 1024 * 1024,
        }
    except ValueError:
        return None