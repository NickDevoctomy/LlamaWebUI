"""Best-effort host telemetry for the operator dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import psutil


def collect_system_metrics(data_dir: Path) -> dict[str, object]:
    disk = psutil.disk_usage(data_dir)
    memory = psutil.virtual_memory()
    network = psutil.net_io_counters()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_bytes": memory.used,
        "ram_total_bytes": memory.total,
        "network_sent_bytes": network.bytes_sent,
        "network_received_bytes": network.bytes_recv,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "gpu": None,
    }