import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from llamawebui.services import hardware_profile
from llamawebui.services.hardware_profile import HardwareProfile, detect_hardware


def _fake_run(outputs: dict[str, str | None]) -> object:
    def run(command: list[str], timeout: float = 5) -> str | None:
        return outputs.get(command[0])

    return run


@pytest.fixture(autouse=True)
def fresh_detection() -> None:
    detect_hardware.cache_clear()


def test_detect_hardware_combines_names_and_discrete_nvidia_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hardware_profile.sys, "platform", "linux")
    monkeypatch.setattr(hardware_profile, "_cpu_name", lambda: "AMD RYZEN AI MAX+ 395")
    monkeypatch.setattr(
        hardware_profile.psutil, "virtual_memory", lambda: SimpleNamespace(total=96 * 1024**3)
    )
    monkeypatch.setattr(
        hardware_profile,
        "_run",
        _fake_run(
            {
                "lspci": "00:02.0 VGA compatible controller: AMD Radeon 8060S\n"
                "01:00.0 3D controller: NVIDIA RTX 4090\n"
                "02:00.0 Ethernet controller: Intel\n",
                "nvidia-smi": "NVIDIA RTX 4090, 24564\nbroken line, n/a\n",
            }
        ),
    )

    hardware = detect_hardware()

    assert hardware == HardwareProfile(
        cpu="AMD RYZEN AI MAX+ 395",
        memory_bytes=96 * 1024**3,
        gpus=("AMD Radeon 8060S", "NVIDIA RTX 4090"),
        discrete_vram_bytes=24564 * 1024 * 1024,
    )
    assert hardware.to_payload()["gpus"] == ["AMD Radeon 8060S", "NVIDIA RTX 4090"]


def test_gpu_names_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hardware_profile,
        "_run",
        _fake_run(
            {
                "powershell": "AMD Radeon 8060S Graphics\n\nMicrosoft Basic Display Adapter\n"
                "Meta Virtual Monitor\nNVIDIA GeForce RTX 4090\n",
                "system_profiler": "Graphics:\n  Apple M3 Max:\n    Chipset Model: Apple M3 Max\n",
            }
        ),
    )
    monkeypatch.setattr(hardware_profile.sys, "platform", "win32")
    assert hardware_profile._gpu_names() == (
        "AMD Radeon 8060S Graphics",
        "NVIDIA GeForce RTX 4090",
    )
    monkeypatch.setattr(hardware_profile.sys, "platform", "darwin")
    assert hardware_profile._gpu_names() == ("Apple M3 Max",)

    monkeypatch.setattr(hardware_profile, "_run", _fake_run({}))
    assert hardware_profile._gpu_names() == ()
    monkeypatch.setattr(hardware_profile.sys, "platform", "linux")
    assert hardware_profile._gpu_names() == ()
    monkeypatch.setattr(hardware_profile.sys, "platform", "win32")
    assert hardware_profile._gpu_names() == ()
    assert hardware_profile._nvidia_gpus() == ()


def test_cpu_name_on_linux_and_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nmodel name\t: AMD Ryzen AI Max+ 395\n", encoding="utf-8")
    real_path = Path
    monkeypatch.setattr(
        hardware_profile,
        "Path",
        lambda value: cpuinfo if value == "/proc/cpuinfo" else real_path(value),
    )
    monkeypatch.setattr(hardware_profile.sys, "platform", "linux")
    assert hardware_profile._cpu_name() == "AMD Ryzen AI Max+ 395"

    cpuinfo.write_text("processor\t: 0\n", encoding="utf-8")
    assert hardware_profile._cpu_name() is None

    monkeypatch.setattr(hardware_profile.sys, "platform", "darwin")
    monkeypatch.setattr(hardware_profile, "_run", _fake_run({"sysctl": "Apple M3 Max\n"}))
    assert hardware_profile._cpu_name() == "Apple M3 Max"


def test_run_returns_none_when_a_command_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("nvidia-smi", 2)

    monkeypatch.setattr(hardware_profile.subprocess, "run", failing)
    assert hardware_profile._run(["nvidia-smi"]) is None
    monkeypatch.setattr(
        hardware_profile.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="ok")
    )
    assert hardware_profile._run(["echo"]) == "ok"
