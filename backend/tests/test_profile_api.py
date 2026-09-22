from pathlib import Path

from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.runtime_probe import RuntimeProbeResult


def _profile_payload(runtime_id: str, model: Path) -> dict[str, object]:
    return {
        "alias": "local-model",
        "runtime_id": runtime_id,
        "model_path": str(model),
        "ctx_size": 4096,
        "no_reasoning_preserve": True,
    }


def test_profile_persists_preset_and_guards_runtime_removal(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "no-reasoning-preserve"}),
                raw_help="help",
            ),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, runtime_prober=fake_probe)
    with TestClient(app) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        payload = _profile_payload(runtime_id, model)
        created = client.post("/api/profiles", json=payload)
        duplicate = client.post("/api/profiles", json=payload)
        blocked = client.delete(f"/api/runtimes/{runtime_id}")
        listed = client.get("/api/profiles")
        removed = client.delete(f"/api/profiles/{created.json()['id']}")
        missing = client.delete(f"/api/profiles/{created.json()['id']}")
        runtime_removed = client.delete(f"/api/runtimes/{runtime_id}")

    assert created.status_code == 201
    assert "ctx-size = 4096" in created.json()["preset"]
    assert created.json()["configuration"]["no_reasoning_preserve"] is True
    assert duplicate.status_code == 409
    assert blocked.status_code == 409
    assert len(listed.json()) == 1
    assert removed.status_code == 204
    assert missing.status_code == 404
    assert runtime_removed.status_code == 204

    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        assert client.get("/api/profiles").json() == []


def test_profile_rejects_unknown_runtime_and_invalid_options(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.touch()
    settings = Settings(data_dir=tmp_path / "data")

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path,
            version=RuntimeVersion(build="1", commit=None, raw=""),
            capabilities=RuntimeCapabilities(options=frozenset({"model"}), raw_help=""),
            devices_output=None,
            errors=(),
        )

    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        unknown = client.post(
            "/api/profiles",
            json={"alias": "model", "runtime_id": "missing", "model_path": str(model)},
        )
        executable = tmp_path / "llama-server.exe"
        executable.touch()
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        invalid = client.post(
            "/api/profiles",
            json={
                "alias": "model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "ctx_size": 4096,
            },
        )

    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == ["runtime does not support --ctx-size"]


def test_profile_clone_copies_configuration_but_stays_disabled(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "no-reasoning-preserve"}),
                raw_help="help",
            ),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        source = client.post(
            "/api/profiles",
            json={
                **_profile_payload(runtime_id, model),
                "alias": "source-model",
                "ctx_size": 4096,
            },
        )
        cloned = client.post(
            f"/api/profiles/{source.json()['id']}/clone", json={"alias": "copy-model"}
        )
        duplicate = client.post(
            f"/api/profiles/{source.json()['id']}/clone", json={"alias": "copy-model"}
        )
        padded = client.post(
            f"/api/profiles/{source.json()['id']}/clone", json={"alias": " copy-model-2 "}
        )

    assert source.status_code == 201
    assert cloned.status_code == 201
    assert cloned.json()["alias"] == "copy-model"
    assert cloned.json()["runtime_id"] == runtime_id
    assert cloned.json()["model_path"] == str(model.resolve())
    assert cloned.json()["configuration"] == source.json()["configuration"]
    assert cloned.json()["enabled"] is False
    assert duplicate.status_code == 409
    assert padded.status_code == 422


def test_profile_export_returns_portable_profile_json(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "no-reasoning-preserve"}),
                raw_help="help",
            ),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        created = client.post(
            "/api/profiles",
            json={**_profile_payload(runtime_id, model), "alias": "export-model"},
        )
        exported = client.get(f"/api/profiles/{created.json()['id']}/export")

    payload = exported.json()
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == 'attachment; filename="export-model.json"'
    assert payload["format"] == "llamawebui-profile-v1"
    assert payload["alias"] == "export-model"
    assert payload["configuration"]["ctx_size"] == 4096
    assert "export-model" in payload["preset"]


def test_profile_command_export_returns_readable_structured_command(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model file.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    ) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        profile = client.post(
            "/api/profiles",
            json={
                "alias": "command-model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "ctx_size": 4096,
            },
        ).json()
        command = client.get(f"/api/profiles/{profile['id']}/command")

    assert command.status_code == 200
    assert str(executable.resolve()) in command.text
    assert "--model" in command.text
    assert "model file.gguf" in command.text
    assert "--ctx-size 4096" in command.text


def test_profile_command_import_creates_disabled_profile(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "future-flag"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    ) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        imported = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "imported-command",
                "runtime_id": runtime_id,
                "command": f'--model "{model}" --ctx-size 4096 --future-flag value',
            },
        )

    assert imported.status_code == 201, imported.text
    assert imported.json()["enabled"] is False
    assert imported.json()["configuration"]["ctx_size"] == 4096
    assert imported.json()["configuration"]["advanced"] == [
        {"name": "future-flag", "value": "value"}
    ]


def test_profile_command_import_rejects_malformed_command(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(options=frozenset({"model"}), raw_help="help"),
            devices_output=None,
            errors=(),
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    ) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        response = client.post(
            "/api/profiles/import-command",
            json={"alias": "bad", "runtime_id": runtime_id, "command": "--ctx-size 4"},
        )

    assert response.status_code == 422
    assert "--model" in response.json()["detail"]


def test_profile_command_import_rejects_unknown_runtime(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as client:
        response = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "missing-runtime",
                "runtime_id": "missing",
                "command": "--model model.gguf",
            },
        )

    assert response.status_code == 404


def test_profile_command_export_reports_missing_profile_and_runtime(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        missing = client.get("/api/profiles/missing/command")

    assert missing.status_code == 404


def test_profile_import_recreates_export_with_exported_enabled_state(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "no-reasoning-preserve"}),
                raw_help="help",
            ),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        created = client.post(
            "/api/profiles",
            json={**_profile_payload(runtime_id, model), "alias": "source-model"},
        )
        exported = client.get(f"/api/profiles/{created.json()['id']}/export").json()
        imported = client.post("/api/profiles/import", json={"document": exported})
        invalid = client.post(
            "/api/profiles/import", json={"document": {"format": "unknown"}}
        )

    assert imported.status_code == 409
    assert invalid.status_code == 422

    exported["alias"] = "imported-model"
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        imported = client.post("/api/profiles/import", json={"document": exported})

    assert imported.status_code == 201
    assert imported.json()["alias"] == "imported-model"
    assert imported.json()["enabled"] is True
    assert imported.json()["configuration"]["ctx_size"] == 4096


def test_profile_validate_reports_runtime_and_file_errors_without_mutation(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "missing.gguf"

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(options=frozenset({"model"}), raw_help="help"),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        model.touch()
        created = client.post(
            "/api/profiles",
            json={"alias": "validate-model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        model.unlink()
        validation = client.post(f"/api/profiles/{created.json()['id']}/validate")
        listed = client.get("/api/profiles")

    assert validation.status_code == 200
    assert validation.json()["valid"] is False
    assert any("model file not found" in error for error in validation.json()["errors"])
    assert listed.json()[0]["alias"] == "validate-model"


def test_profile_reset_retains_identity_and_removes_overrides(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "n-gpu-layers", "no-reasoning-preserve"}),
                raw_help="help",
            ),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        created = client.post(
            "/api/profiles",
            json={
                **_profile_payload(runtime_id, model),
                "alias": "reset-model",
                "n_gpu_layers": 12,
            },
        )
        reset = client.post(f"/api/profiles/{created.json()['id']}/reset")

    assert reset.status_code == 200
    assert reset.json()["alias"] == "reset-model"
    assert reset.json()["model_path"] == str(model.resolve())
    assert reset.json()["configuration"]["advanced"] == []
    assert "ctx-size" not in reset.json()["preset"]


def test_profile_update_populates_and_persists_all_editor_settings(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset(
                    {
                        "model",
                        "ctx-size",
                        "n-gpu-layers",
                        "threads",
                        "batch-size",
                        "flash-attn",
                        "no-reasoning-preserve",
                    }
                ),
                raw_help="help",
            ),
            devices_output=None,
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        created = client.post(
            "/api/profiles",
            json={**_profile_payload(runtime_id, model), "alias": "original-model"},
        )
        updated = client.put(
            f"/api/profiles/{created.json()['id']}",
            json={
                "alias": "edited-model",
                "runtime_id": runtime_id,
                "model_path": str(model),
                "enabled": False,
                "no_reasoning_preserve": False,
                "n_gpu_layers": 42,
                "ctx_size": 8192,
                "threads": 8,
                "batch_size": 512,
                "flash_attn": "on",
            },
        )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["alias"] == "edited-model"
    assert payload["enabled"] is False
    assert payload["configuration"]["n_gpu_layers"] == 42
    assert payload["configuration"]["ctx_size"] == 8192
    assert payload["configuration"]["threads"] == 8
    assert payload["configuration"]["batch_size"] == 512
    assert payload["configuration"]["flash_attn"] == "on"
