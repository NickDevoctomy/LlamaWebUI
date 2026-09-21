from pathlib import Path

from llamawebui.services import system_metrics


def test_collect_system_metrics_includes_host_and_gpu_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        system_metrics.psutil,
        "disk_usage",
        lambda _: type("Disk", (), {"free": 40, "total": 100})(),
    )
    monkeypatch.setattr(
        system_metrics.psutil,
        "virtual_memory",
        lambda: type("Memory", (), {"used": 60, "total": 120})(),
    )
    monkeypatch.setattr(
        system_metrics.psutil,
        "net_io_counters",
        lambda: type("Network", (), {"bytes_sent": 7, "bytes_recv": 8})(),
    )
    monkeypatch.setattr(
        system_metrics.psutil,
        "disk_io_counters",
        lambda: type("DiskIo", (), {"read_bytes": 9, "write_bytes": 10})(),
    )
    monkeypatch.setattr(system_metrics.psutil, "cpu_percent", lambda interval=None: 12.5)
    monkeypatch.setattr(system_metrics, "_nvidia_metrics", lambda: {
        "utilization_percent": 20,
        "memory_used_bytes": 30,
        "memory_total_bytes": 40,
    })

    assert system_metrics.collect_system_metrics(tmp_path) == {
        "cpu_percent": 12.5,
        "ram_used_bytes": 60,
        "ram_total_bytes": 120,
        "network_sent_bytes": 7,
        "network_received_bytes": 8,
        "disk_read_bytes": 9,
        "disk_write_bytes": 10,
        "disk_free_bytes": 40,
        "disk_total_bytes": 100,
        "gpu": {
            "utilization_percent": 20,
            "memory_used_bytes": 30,
            "memory_total_bytes": 40,
        },
        "gpu_supported": True,
    }


def test_collect_system_metrics_handles_missing_disk_io_and_gpu(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        system_metrics.psutil,
        "disk_usage",
        lambda _: type("Disk", (), {"free": 1, "total": 2})(),
    )
    monkeypatch.setattr(
        system_metrics.psutil,
        "virtual_memory",
        lambda: type("Memory", (), {"used": 3, "total": 4})(),
    )
    monkeypatch.setattr(
        system_metrics.psutil,
        "net_io_counters",
        lambda: type("Network", (), {"bytes_sent": 5, "bytes_recv": 6})(),
    )
    monkeypatch.setattr(system_metrics.psutil, "disk_io_counters", lambda: None)
    monkeypatch.setattr(system_metrics.psutil, "cpu_percent", lambda interval=None: 7)
    monkeypatch.setattr(system_metrics, "_nvidia_metrics", lambda: None)

    result = system_metrics.collect_system_metrics(tmp_path)

    assert result["disk_read_bytes"] is None
    assert result["disk_write_bytes"] is None
    assert result["gpu"] is None
    assert result["gpu_supported"] is False