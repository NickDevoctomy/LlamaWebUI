from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.router_client import RouterAPIError, RouterModel, RouterModelEvent
from llamawebui.services.router_supervisor import (
    RouterProcess,
    RouterRestartPolicy,
    RouterSupervisor,
)
from llamawebui.services.runtime_probe import RuntimeProbeResult


class FakeProcess:
    stdout = None

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    async def terminate_tree(self, *, force: bool) -> None:
        self.returncode = -9 if force else 0
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def exit(self, returncode: int) -> None:
        self.returncode = returncode
        self._exited.set()


async def available_port(host: str, port: int) -> bool:
    return True


class FakeRouterClient:
    def __init__(self) -> None:
        self.actions: list[tuple[str, object]] = []
        self.error: RouterAPIError | None = None
        self.events: tuple[RouterModelEvent, ...] = ()

    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]:
        if self.error is not None:
            raise self.error
        self.actions.append(("list", reload))
        return (
            RouterModel(
                id="local-model",
                path="C:/models/model.gguf",
                status={"value": "loaded"},
                metadata={"architecture": {"input_modalities": ["text"]}},
            ),
        )

    async def load_model(self, model: str) -> None:
        if self.error is not None:
            raise self.error
        self.actions.append(("load", model))

    async def unload_model(self, model: str) -> None:
        if self.error is not None:
            raise self.error
        self.actions.append(("unload", model))

    async def model_events(self) -> AsyncIterator[RouterModelEvent]:
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event


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
    app = create_app(
        settings,
        runtime_prober=fake_probe,
        router_supervisor=supervisor,
        router_port_probe=available_port,
        router_client=FakeRouterClient(),
    )
    with TestClient(app) as client:
        stopped = client.get("/api/server/status")
        created_token = client.post("/api/tokens", json={"name": "OpenCode"})
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        client.post(
            "/api/profiles",
            json={"alias": "local-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        started = client.post("/api/server/start", json={"runtime_id": runtime_id})
        opencode = client.get("/api/integrations/opencode")
        token_while_running = client.post("/api/tokens", json={"name": "Blocked"})
        running = client.get("/api/server/status")
        duplicate = client.post("/api/server/start", json={"runtime_id": runtime_id})
        stopped_again = client.post("/api/server/stop")
        runs = client.get("/api/server/runs")

    assert stopped.json()["state"] == "stopped"
    assert created_token.status_code == 201
    assert started.status_code == 200
    assert started.json()["state"] == "ready"
    assert started.json()["pid"] == 4321
    assert started.json()["endpoint"] == "http://127.0.0.1:9876"
    assert running.json()["state"] == "ready"
    assert duplicate.status_code == 409
    assert token_while_running.status_code == 409
    assert opencode.status_code == 200
    provider = opencode.json()["provider"]["llama-web-ui"]
    assert provider["options"] == {
        "baseURL": "http://127.0.0.1:9876/v1",
        "apiKey": "{env:LLAMA_WEB_UI_API_KEY}",
    }
    assert provider["models"] == {"local-model": {"name": "local-model"}}
    assert stopped_again.json()["state"] == "stopped"
    assert len(runs.json()) == 1
    assert runs.json()[0]["state"] == "stopped"
    assert runs.json()[0]["pid"] == 4321
    assert runs.json()[0]["exit_code"] == 0
    assert runs.json()[0]["ended_at"] is not None
    assert launched[0][0] == str(executable.resolve())
    assert launched[0][-2] == "--api-key-file"
    assert Path(launched[0][-1]).read_text(encoding="utf-8").strip() == (
        created_token.json()["token"]
    )
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
        router_port_probe=available_port,
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
    with TestClient(app) as client:
        runs = client.get("/api/server/runs").json()
    assert len(runs) == 1
    assert runs[0]["state"] == "stopped"
    assert "did not become ready" in runs[0]["error"]


def test_server_restart_uses_previous_runtime_and_creates_new_run(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    processes = iter((FakeProcess(1001), FakeProcess(1002), FakeProcess(1003)))
    launches: list[tuple[str, ...]] = []

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
        launches.append(tuple(arguments))
        return next(processes)

    async def healthy(host: str, port: int) -> bool:
        return True

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, healthy),
        router_port_probe=available_port,
    )
    with TestClient(app) as client:
        no_previous = client.post("/api/server/restart")
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        client.post(
            "/api/profiles",
            json={"alias": "local-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        client.post("/api/server/start", json={"runtime_id": runtime_id})
        restarted = client.post("/api/server/restart")
        runs = client.get("/api/server/runs").json()

    assert no_previous.status_code == 409
    assert restarted.status_code == 200
    assert restarted.json()["pid"] == 1002
    assert len(launches) == 2
    assert len(runs) == 2
    assert {run["pid"] for run in runs} == {1001, 1002}
    assert {run["runtime_id"] for run in runs} == {runtime_id}


def test_server_restart_can_switch_to_explicit_runtime(tmp_path: Path) -> None:
    executable_one = tmp_path / "llama-one.exe"
    executable_two = tmp_path / "llama-two.exe"
    model = tmp_path / "model.gguf"
    executable_one.touch()
    executable_two.touch()
    model.touch()
    processes = iter((FakeProcess(1001), FakeProcess(1002), FakeProcess(1003)))
    launches: list[tuple[str, ...]] = []

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build=path.stem, commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        launches.append(tuple(arguments))
        return next(processes)

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, available_port),
        router_port_probe=available_port,
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/runtimes", json={"name": "One", "executable_path": str(executable_one)}
        ).json()
        second = client.post(
            "/api/runtimes", json={"name": "Two", "executable_path": str(executable_two)}
        ).json()
        client.post(
            "/api/profiles",
            json={"alias": "local-model", "runtime_id": first["id"], "model_path": str(model)},
        )
        client.post(
            "/api/profiles",
            json={"alias": "local-model-two", "runtime_id": second["id"], "model_path": str(model)},
        )
        client.post("/api/server/start", json={"runtime_id": first["id"]})
        restarted = client.post(
            "/api/server/restart", json={"runtime_id": second["id"]}
        )
        rolled_back = client.post("/api/server/rollback")
        runtimes = client.get("/api/runtimes").json()

    assert restarted.status_code == 200
    assert rolled_back.status_code == 200
    assert rolled_back.json()["pid"] == 1003
    assert len(launches) == 3
    assert runtimes[0]["id"] in {first["id"], second["id"]}


def test_server_rollback_restores_current_runtime_when_candidate_fails(
    tmp_path: Path,
) -> None:
    executable_one = tmp_path / "llama-one.exe"
    executable_two = tmp_path / "llama-two.exe"
    model = tmp_path / "model.gguf"
    executable_one.touch()
    executable_two.touch()
    model.touch()
    processes = iter((FakeProcess(1001), FakeProcess(1002), FakeProcess(1003)))
    launches: list[tuple[str, ...]] = []

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build=path.stem, commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        launches.append(tuple(arguments))
        if len(launches) == 3:
            raise OSError("candidate runtime failed")
        return next(processes)

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, available_port),
        router_port_probe=available_port,
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/runtimes", json={"name": "One", "executable_path": str(executable_one)}
        ).json()
        second = client.post(
            "/api/runtimes", json={"name": "Two", "executable_path": str(executable_two)}
        ).json()
        for alias, runtime_id in (("one", first["id"]), ("two", second["id"])):
            client.post(
                "/api/profiles",
                json={"alias": alias, "runtime_id": runtime_id, "model_path": str(model)},
            )
        client.post("/api/server/start", json={"runtime_id": first["id"]})
        client.post("/api/server/restart", json={"runtime_id": second["id"]})
        response = client.post("/api/server/rollback")
        status_response = client.get("/api/server/status")

    assert response.status_code == 502
    assert "candidate runtime failed" in response.json()["detail"]
    assert status_response.json()["state"] == "ready"
    assert len(launches) == 4


def test_server_start_records_occupied_port_without_launching(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    launched = False

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
        nonlocal launched
        launched = True
        return FakeProcess()

    async def occupied(host: str, port: int) -> bool:
        return False

    app = create_app(
        Settings(data_dir=tmp_path / "data", router_port=4567),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher),
        router_port_probe=occupied,
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
        runs = client.get("/api/server/runs").json()

    assert response.status_code == 409
    assert response.json()["detail"] == "router port is already in use: 127.0.0.1:4567"
    assert not launched
    assert runs[0]["state"] == "crashed"
    assert runs[0]["error"] == response.json()["detail"]


@pytest.mark.asyncio
async def test_server_lifecycle_requests_are_serialized(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    process = FakeProcess()
    health_entered = asyncio.Event()
    release_health = asyncio.Event()

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

    async def delayed_health(host: str, port: int) -> bool:
        health_entered.set()
        await release_health.wait()
        return True

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, delayed_health),
        router_port_probe=available_port,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            runtime_id = (
                await client.post(
                    "/api/runtimes",
                    json={"name": "CPU", "executable_path": str(executable)},
                )
            ).json()["id"]
            await client.post(
                "/api/profiles",
                json={
                    "alias": "local-model",
                    "runtime_id": runtime_id,
                    "model_path": str(model),
                },
            )
            start_task = asyncio.create_task(
                client.post("/api/server/start", json={"runtime_id": runtime_id})
            )
            await health_entered.wait()
            stop_task = asyncio.create_task(client.post("/api/server/stop"))
            await asyncio.sleep(0)

            assert not stop_task.done()
            release_health.set()
            assert (await start_task).status_code == 200
            assert (await stop_task).json()["state"] == "stopped"


@pytest.mark.asyncio
async def test_server_recovers_with_durable_attempt_history(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    first_process = FakeProcess(1001)
    second_process = FakeProcess(1002)
    processes = iter((first_process, second_process))
    restarted = asyncio.Event()
    router_client = FakeRouterClient()
    router_client.events = (
        RouterModelEvent(
            model="local-model",
            event="model_status",
            data={"status": "loaded"},
        ),
    )

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
        process = next(processes)
        if process is second_process:
            restarted.set()
        return process

    async def healthy(host: str, port: int) -> bool:
        return True

    supervisor = RouterSupervisor(
        launcher,
        healthy,
        restart_policy=RouterRestartPolicy(delay_seconds=0),
    )
    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=supervisor,
        router_port_probe=available_port,
        router_client=router_client,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            runtime_id = (
                await client.post(
                    "/api/runtimes",
                    json={"name": "CPU", "executable_path": str(executable)},
                )
            ).json()["id"]
            await client.post(
                "/api/profiles",
                json={
                    "alias": "local-model",
                    "runtime_id": runtime_id,
                    "model_path": str(model),
                },
            )
            assert (
                await client.post("/api/server/start", json={"runtime_id": runtime_id})
            ).status_code == 200
            await asyncio.sleep(0)

            first_process.exit(17)
            await asyncio.wait_for(restarted.wait(), 1)
            await asyncio.sleep(0)
            status_response = await client.get("/api/server/status")
            runs = (await client.get("/api/server/runs")).json()
            broker = app.state.event_broker
            subscription = broker.subscribe(0)
            events = [await anext(subscription) for _ in range(broker.latest_id)]
            subscription.close()

    assert status_response.json()["state"] == "ready"
    assert status_response.json()["pid"] == 1002
    assert len(runs) == 2
    assert {run["state"] for run in runs} == {"crashed", "ready"}
    assert {run["pid"] for run in runs} == {1001, 1002}
    assert second_process.returncode == 0
    assert [event.id for event in events] == list(range(1, len(events) + 1))
    assert any(
        event.type == "router.state" and event.data["state"] == "crashed"
        for event in events
    )
    assert any(event.type == "router.model.model_status" for event in events)


def test_router_model_operations_require_running_server(tmp_path: Path) -> None:
    router_client = FakeRouterClient()
    app = create_app(
        Settings(data_dir=tmp_path / "data"), router_client=router_client
    )
    with TestClient(app) as client:
        listed = client.get("/api/server/models")
        events = client.get("/api/server/models/events")
        loaded = client.post("/api/server/models/load", json={"model": "local-model"})
        invalid = client.post("/api/server/models/load", json={"model": " "})

    assert listed.status_code == 409
    assert events.status_code == 409
    assert loaded.status_code == 409
    assert invalid.status_code == 422
    assert router_client.actions == []


def test_router_model_list_load_and_unload(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    process = FakeProcess()
    router_client = FakeRouterClient()
    router_client.events = (
        RouterModelEvent(
            model="local-model",
            event="model_status",
            data={"status": "loaded"},
        ),
    )

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

    async def healthy(host: str, port: int) -> bool:
        return True

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, healthy),
        router_port_probe=available_port,
        router_client=router_client,
    )
    with TestClient(app) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        client.post(
            "/api/profiles",
            json={"alias": "local-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        client.post("/api/server/start", json={"runtime_id": runtime_id})
        listed = client.get("/api/server/models", params={"reload": True})
        loaded = client.post("/api/server/models/load", json={"model": "local-model"})
        unloaded = client.post("/api/server/models/unload", json={"model": "local-model"})
        events = client.get("/api/server/models/events")
        router_client.error = RouterAPIError(503, "native router unavailable")
        failed = client.get("/api/server/models")
        failed_events = client.get("/api/server/models/events")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == "local-model"
    assert listed.json()[0]["status"] == {"value": "loaded"}
    assert loaded.json() == {"success": True}
    assert unloaded.json() == {"success": True}
    assert events.headers["content-type"].startswith("text/event-stream")
    assert events.text == (
        "event: model_status\n"
        'data: {"model":"local-model","event":"model_status",'
        '"data":{"status":"loaded"}}\n\n'
    )
    assert failed.status_code == 502
    assert failed.json() == {"detail": "native router unavailable"}
    assert failed_events.status_code == 200
    assert failed_events.text == (
        "event: error\n"
        'data: {"model":"*","event":"error",'
        '"data":{"code":502,"message":"native router unavailable"}}\n\n'
    )
