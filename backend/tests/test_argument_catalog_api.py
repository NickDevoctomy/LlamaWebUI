from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections.abc import Sequence
from pathlib import Path

from conftest import Login
from fastapi.testclient import TestClient
from sqlalchemy import update

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import create_database_engine
from llamawebui.domain.runtime_capabilities import RuntimeVersion, parse_help_output
from llamawebui.models import ModelProfileRecord
from llamawebui.services.router_supervisor import RouterProcess, RouterSupervisor
from llamawebui.services.runtime_probe import RuntimeProbeResult

HELP = """
-m,    --model FNAME                    model path
--models-preset PATH                    router presets
-c,    --ctx-size N                     size of the prompt context (default: 4096)
-t,    --threads N                      number of threads (default: -1)
--agent, --no-agent                     enable agent tools
-bn,   --brand-new N                    an option this catalog does not know
"""


async def fake_probe(path: Path) -> RuntimeProbeResult:
    return RuntimeProbeResult(
        executable=path.resolve(),
        version=RuntimeVersion(build="b1", commit=None, raw="version"),
        capabilities=parse_help_output(HELP),
        devices_output=None,
        errors=(),
    )


def _setup(client: TestClient, tmp_path: Path, login: Login) -> tuple[str, Path]:
    login(client)
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()
    runtime = client.post(
        "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
    )
    assert runtime.status_code == 201, runtime.text
    return runtime.json()["id"], model


def test_catalog_endpoints_return_global_and_runtime_effective_catalogs(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        anonymous = TestClient(client.app).get("/api/argument-catalog")
        runtime_id, _ = _setup(client, tmp_path, login)
        catalog = client.get("/api/argument-catalog")
        effective = client.get(f"/api/runtimes/{runtime_id}/argument-catalog")
        missing = client.get("/api/runtimes/missing/argument-catalog")
        runtime = client.get(f"/api/runtimes/{runtime_id}")

    assert anonymous.status_code == 401
    assert catalog.status_code == 200
    assert catalog.json()["catalogVersion"] == "0.4.1"
    assert len(catalog.json()["arguments"]) == 214
    payload = effective.json()
    arguments = {item["id"]: item for item in payload["arguments"]}
    assert arguments["ctx-size"]["runtime"]["default"] == "4096"
    assert arguments["ctx-size"]["runtime"]["status"] == "advertised"
    assert arguments["fit"]["runtime"]["status"] == "not-advertised"
    assert payload["uncatalogued"][0]["flag"] == "--brand-new"
    assert payload["runtime"]["id"] == runtime_id
    assert payload["runtime"]["flagsSha256"] == runtime.json()["flags_sha256"]
    assert runtime.json()["option_details_count"] == 6
    assert missing.status_code == 404


def test_profile_preview_validates_unsaved_arguments_and_shows_launch_vector(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id, model = _setup(client, tmp_path, login)
        base = {"alias": "preview-model", "runtime_id": runtime_id, "model_path": str(model)}
        valid = client.post(
            "/api/profiles/preview",
            json={
                **base,
                "arguments": [
                    {"id": "ctx-size", "value": 8192},
                    {"id": "fit", "value": "on"},
                    {"id": "hf-token", "value": "hf_secret"},
                    {"flag": "--brand-new", "value": "2", "origin": "runtime"},
                ],
                "acknowledged": ["hf-token"],
            },
        )
        unconfirmed = client.post(
            "/api/profiles/preview",
            json={**base, "arguments": [{"id": "agent", "value": True}]},
        )
        malformed = client.post(
            "/api/profiles/preview",
            json={**base, "arguments": [{"id": "ctx-size", "value": "lots"}]},
        )
        unsafe = client.post(
            "/api/profiles/preview",
            json={**base, "arguments": [{"flag": "--x", "value": "$(whoami)"}]},
        )
        missing = client.post(
            "/api/profiles/preview", json={**base, "runtime_id": "missing"}
        )

    body = valid.json()
    assert valid.status_code == 200
    assert body["valid"] is True
    assert body["arguments"][1:] == [
        "--model",
        str(model.resolve()),
        "--ctx-size",
        "8192",
        "--fit",
        "on",
        "--hf-token",
        "******",
        "--brand-new",
        "2",
    ]
    assert "hf_secret" not in json.dumps(body)
    assert "hf-token = ******" in body["preset"]
    assert body["router_arguments"][1:3] == ["--models-preset", body["router_arguments"][2]]
    assert any("--fit" in warning for warning in body["warnings"])
    assert any(issue["code"] == "security" for issue in body["issues"])
    assert unconfirmed.json()["valid"] is False
    assert unconfirmed.json()["issues"][0]["code"] == "unconfirmed-security"
    assert malformed.json()["errors"] == ["--ctx-size must be a whole number"]
    assert malformed.json()["arguments"] == []
    assert unsafe.json()["valid"] is False
    assert missing.status_code == 404


def test_structured_profile_saves_with_warnings_and_validates(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id, model = _setup(client, tmp_path, login)
        blocked = client.post(
            "/api/profiles",
            json={
                "alias": "agent-model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "arguments": [{"id": "agent", "value": True}],
            },
        )
        created = client.post(
            "/api/profiles",
            json={
                "alias": "agent-model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "arguments": [
                    {"id": "agent", "value": True},
                    {"id": "parallel", "value": 2},
                ],
                "acknowledged": ["agent"],
            },
        )
        profile_id = created.json()["id"]
        validation = client.post(f"/api/profiles/{profile_id}/validate")
        command = client.get(f"/api/profiles/{profile_id}/command")
        missing_validation = client.post("/api/profiles/missing/validate")
        missing_export = client.get("/api/profiles/missing/export")
        reset = client.post(f"/api/profiles/{profile_id}/reset")

    assert blocked.status_code == 422
    assert "requires a security acknowledgement" in blocked.json()["detail"][0]
    assert created.status_code == 201, created.text
    assert created.json()["configuration"]["acknowledged"] == ["agent"]
    assert "agent = true" in created.json()["preset"]
    assert any("--parallel" in warning for warning in created.json()["warnings"])
    assert validation.json()["valid"] is True
    assert validation.json()["arguments"][-2:] == ["--parallel", "2"]
    assert "--agent --parallel 2" in command.text
    assert missing_validation.status_code == 404
    assert missing_export.status_code == 404
    assert reset.json()["configuration"]["acknowledged"] == []


def test_legacy_profiles_are_migrated_at_startup_without_changing_presets(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id, model = _setup(client, tmp_path, login)
        created = client.post(
            "/api/profiles",
            json={
                "alias": "legacy-model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "ctx_size": 4096,
                "no_reasoning_preserve": True,
                "advanced": [{"name": "brand-new", "value": "3"}],
            },
        )
        drifted = client.post(
            "/api/profiles",
            json={"alias": "drifted-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
    assert created.status_code == 201, created.text
    original_preset = created.json()["preset"]
    legacy = {
        "alias": "legacy-model",
        "model_path": str(model.resolve()),
        "no_reasoning_preserve": True,
        "ctx_size": 4096,
        "threads": None,
        "override_tensor": [],
        "advanced": [{"name": "brand-new", "value": "3"}],
    }
    engine = create_database_engine(settings.database_path)
    with engine.begin() as connection:
        connection.execute(
            update(ModelProfileRecord)
            .where(ModelProfileRecord.id == created.json()["id"])
            .values(configuration=legacy)
        )
        connection.execute(
            update(ModelProfileRecord)
            .where(ModelProfileRecord.id == drifted.json()["id"])
            .values(configuration={"ctx_size": 1}, preset="version = 1\n\n[drifted-model]\n")
        )
    engine.dispose()

    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        login(client)
        profiles = {item["alias"]: item for item in client.get("/api/profiles").json()}

    migrated = profiles["legacy-model"]
    configuration = migrated["configuration"]
    assert migrated["preset"] == original_preset
    assert configuration["schema_version"] == 2
    assert configuration["legacy_configuration"] == legacy
    assert configuration["arguments"] == [
        {"id": "reasoning-preserve", "value": False},
        {"id": "ctx-size", "value": 4096},
        {"flag": "--brand-new", "value": "3", "origin": "catalog-mismatch"},
    ]
    assert profiles["drifted-model"]["configuration"]["arguments"] == [
        {"id": "ctx-size", "value": 1}
    ]


def test_legacy_and_unresolved_imports_store_structured_configuration(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id, model = _setup(client, tmp_path, login)
        legacy_document = {
            "format": "llamawebui-profile-v1",
            "alias": "legacy-import",
            "runtime_id": runtime_id,
            "enabled": False,
            "configuration": {"model_path": str(model), "ctx_size": 2048, "advanced": []},
        }
        imported = client.post("/api/profiles/import", json={"document": legacy_document})
        unresolved = client.post(
            "/api/profiles/import",
            json={
                "document": {
                    **legacy_document,
                    "configuration": {"model_path": "C:/elsewhere/model.gguf", "threads": 4},
                },
                "alias": "unresolved-import",
            },
        )
        rejected = client.post(
            "/api/profiles/import",
            json={
                "document": {
                    **legacy_document,
                    "configuration": {"model_path": str(model), "advanced": [
                        {"name": "port", "value": 9000}
                    ]},
                },
                "alias": "managed-import",
            },
        )
        command = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "command-import",
                "runtime_id": runtime_id,
                "command": f'llama-server -m "{model}" -bn 2 --host 0.0.0.0 --port 9000',
            },
        )
        duplicate = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "command-import",
                "runtime_id": runtime_id,
                "command": f'llama-server -m "{model}"',
            },
        )

    assert imported.status_code == 201, imported.text
    assert imported.json()["configuration"]["arguments"] == [{"id": "ctx-size", "value": 2048}]
    assert unresolved.status_code == 201
    assert unresolved.json()["model_path"] == ""
    assert unresolved.json()["configuration"]["arguments"] == [{"id": "threads", "value": 4}]
    assert rejected.status_code == 422
    assert "--port cannot be set in a profile" in rejected.json()["detail"][0]
    assert command.status_code == 201, command.text
    assert command.json()["ignored_arguments"] == ["--host 0.0.0.0", "--port 9000"]
    assert command.json()["configuration"]["arguments"] == [
        {"flag": "--brand-new", "value": "2", "origin": "runtime"}
    ]
    assert duplicate.status_code == 409


def test_router_start_preflight_blocks_unconfirmed_security_and_logs_warnings(
    tmp_path: Path, login: Login
) -> None:
    launched: list[tuple[str, ...]] = []

    class Process:
        stdout = None
        pid = 1234
        returncode: int | None = None

        def __init__(self) -> None:
            self._exited = asyncio.Event()

        async def terminate_tree(self, *, force: bool) -> None:
            self.returncode = 0
            self._exited.set()

        async def wait(self) -> int:
            await self._exited.wait()
            return 0

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        launched.append(tuple(arguments))
        return Process()

    async def healthy(host: str, port: int) -> bool:
        return True

    settings = Settings(data_dir=tmp_path / "data", router_port=9877)
    supervisor = RouterSupervisor(launcher, healthy)
    app = create_app(
        settings,
        runtime_prober=fake_probe,
        router_supervisor=supervisor,
        router_port_probe=healthy,
    )
    with TestClient(app) as client:
        runtime_id, model = _setup(client, tmp_path, login)
        created = client.post(
            "/api/profiles",
            json={
                "alias": "launch-model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "arguments": [
                    {"id": "fit", "value": "on"},
                    {"id": "hf-token", "value": "hf_secret"},
                ],
                "acknowledged": ["hf-token"],
            },
        )
        assert created.status_code == 201, created.text
        engine = create_database_engine(settings.database_path)
        with engine.begin() as connection:
            stored = dict(created.json()["configuration"])
            stored["acknowledged"] = []
            connection.execute(
                update(ModelProfileRecord)
                .where(ModelProfileRecord.id == created.json()["id"])
                .values(configuration=stored)
            )
        engine.dispose()
        blocked = client.post("/api/server/start", json={"runtime_id": runtime_id})
        with engine.begin() as connection:
            stored["acknowledged"] = ["hf-token"]
            connection.execute(
                update(ModelProfileRecord)
                .where(ModelProfileRecord.id == created.json()["id"])
                .values(configuration=stored)
            )
        engine.dispose()
        started = client.post("/api/server/start", json={"runtime_id": runtime_id})
        logs = client.get("/api/server/status").json()["logs"]
        client.post("/api/server/stop")

    assert blocked.status_code == 422
    assert "launch-model: --hf-token requires a security acknowledgement" in blocked.json()[
        "detail"
    ]
    assert started.status_code == 200, started.text
    assert len(launched) == 1
    joined = "\n".join(logs)
    assert "hf_secret" not in joined
    redacted = "******" if os.name == "nt" else shlex.quote("******")
    assert f"--hf-token {redacted}" in joined
    assert "profile launch-model warning: runtime does not advertise --fit" in joined
