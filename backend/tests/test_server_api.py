from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.router_supervisor import RouterProcess, RouterSupervisor
from llamawebui.services.runtime_probe import RuntimeProbeResult


class FakeProcess:
    pid = 4321
    stdout = None

    def __init__(self) -> None:
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    def terminate(self) -> None:
        self.returncode = 0
        self._exited.set()

    def kill(self) -> None:
        self.returncode = -9
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode


def test_server_start_status_and_stop(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    process = FakeProcess()
    launched: list[tuple[str, ...]] = []

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        launched.append(tuple(arguments))
        return process

    async def healthy(host: str, port: int) -> bool:
        return True

    settings = Settings(data_dir=tmp_path / "data", router_port=9876)
    supervisor = RouterSupervisor(launcher, healthy)
    app = create_app(settings, runtime_prober=fake_probe, router_supervisor=supervisor)
    with TestClient(app) as client:
        stopped = client.get("/api/server/status")
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        client.post(
            "/api/profiles",
            json={"alias": "local-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        started = client.post("/api/server/start", json={"runtime_id": runtime_id})
        running = client.get("/api/server/status")
        duplicate = client.post("/api/server/start", json={"runtime_id": runtime_id})
        stopped_again = client.post("/api/server/stop")

    assert stopped.json()["state"] == "stopped"
    assert started.status_code == 200
    assert started.json()["state"] == "ready"
    assert started.json()["pid"] == 4321
    assert started.json()["endpoint"] == "http://127.0.0.1:9876"
    assert running.json()["state"] == "ready"
    assert duplicate.status_code == 409
    assert stopped_again.json()["state"] == "stopped"
    assert launched[0][0] == str(executable.resolve())
    preset_path = Path(launched[0][2])
    assert "[local-model]" in preset_path.read_text(encoding="utf-8")


def test_server_start_validates_runtime_and_profiles(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(options=frozenset(), raw_help="help"),
            devices_output=None,
            errors=(),
        )

    app = create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    with TestClient(app) as client:
        missing = client.post("/api/server/start", json={"runtime_id": "missing"})
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        unsupported = client.post("/api/server/start", json={"runtime_id": runtime_id})

    assert missing.status_code == 404
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == "runtime does not support --models-preset"


def test_server_start_requires_enabled_profile(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    app = create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    with TestClient(app) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        response = client.post("/api/server/start", json={"runtime_id": runtime_id})

    assert response.status_code == 422
    assert response.json()["detail"] == "runtime has no enabled model profiles"


def test_server_start_reports_readiness_timeout(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    process = FakeProcess()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return process

    async def unhealthy(host: str, port: int) -> bool:
        return False

    settings = Settings(
        data_dir=tmp_path / "data", router_ready_timeout_seconds=0.001
    )
    app = create_app(
        settings,
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, unhealthy),
    )
    with TestClient(app) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        client.post(
            "/api/profiles",
            json={"alias": "local-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        response = client.post("/api/server/start", json={"runtime_id": runtime_id})
        status_response = client.get("/api/server/status")

    assert response.status_code == 504
    assert "did not become ready" in response.json()["detail"]
    assert status_response.json()["state"] == "stopped"