from pathlib import Path

from conftest import Login
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


def test_profile_persists_preset_and_guards_runtime_removal(tmp_path: Path, login: Login) -> None:
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
        login(client)
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
        login(client)
        assert client.get("/api/profiles").json() == []


def test_profile_rejects_unknown_runtime_and_invalid_options(tmp_path: Path, login: Login) -> None:
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
        login(client)
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


def test_profile_clone_copies_configuration_but_stays_disabled(
    tmp_path: Path, login: Login
) -> None:
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
        login(client)
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


def test_profile_export_returns_portable_profile_json(tmp_path: Path, login: Login) -> None:
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
        login(client)
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


def test_profile_command_export_returns_readable_structured_command(
    tmp_path: Path, login: Login
) -> None:
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
        login(client)
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


def test_profile_command_import_creates_disabled_profile(tmp_path: Path, login: Login) -> None:
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
        login(client)
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


def test_windows_profile_command_round_trip_preserves_settings_and_shards(
    tmp_path: Path,
    login: Login,
) -> None:
    executable = tmp_path / "llama tools" / "llama-server.exe"
    executable.parent.mkdir()
    executable.touch()
    model_dir = tmp_path / "Qwen model shards"
    model_dir.mkdir()
    shards = [
        model_dir / f"Qwen-UD-IQ4_XS-{index:05d}-of-00003.gguf"
        for index in range(1, 4)
    ]
    for shard in shards:
        shard.touch()
    option_names = {
        "model",
        "no-reasoning-preserve",
        "n-gpu-layers",
        "ctx-size",
        "flash-attn",
        "load-mode",
        "lazy-mode",
        "cache-ram",
        "fit",
        "override-tensor",
        "cache-type-k",
        "cache-type-v",
        "threads",
        "batch-size",
        "ubatch-size",
        "future-option",
        "future-toggle",
    }

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(options=frozenset(option_names), raw_help="help"),
            devices_output=None,
            errors=(),
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    ) as client:
        login(client)
        runtime_id = client.post(
            "/api/runtimes",
            json={"name": "CPU", "executable_path": str(executable)},
        ).json()["id"]
        command = (
            f'"{executable}" -m "{shards[0]}" --no-reasoning-preserve '
            "-ngl 60 -c 262144 -fa on --load-mode none -lzm on --cache-ram 0 "
            "--fit off -ot per_layer_token_embd=CPU -ctk q4_0 -ctv q4_0 "
            "--threads 8 -b 1024 -ub 1024 --future-option \"value with spaces\" "
            "--future-toggle"
        )
        imported = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "round-trip-one",
                "runtime_id": runtime_id,
                "command": command,
            },
        )
        assert imported.status_code == 201, imported.text
        imported_profile = imported.json()
        exported_command = client.get(f"/api/profiles/{imported_profile['id']}/command")
        assert exported_command.status_code == 200
        round_tripped = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "round-trip-two",
                "runtime_id": runtime_id,
                "command": exported_command.text,
            },
        )
        assert round_tripped.status_code == 201, round_tripped.text

    first = imported_profile["configuration"]
    second = round_tripped.json()["configuration"]
    first = {key: value for key, value in first.items() if key != "alias"}
    second = {key: value for key, value in second.items() if key != "alias"}
    assert imported_profile["enabled"] is False
    assert round_tripped.json()["enabled"] is False
    assert first == second
    assert first["model_path"] == str(shards[0].resolve())
    assert first["advanced"] == [
        {"name": "future-option", "value": "value with spaces"},
        {"name": "future-toggle", "value": True},
    ]
    assert "model = " + str(shards[0].resolve()) in imported_profile["preset"]
    assert "override-tensor = per_layer_token_embd=CPU" in imported_profile["preset"]


def test_invalid_profile_command_import_does_not_change_saved_profiles(
    tmp_path: Path, login: Login
) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model = tmp_path / "model.gguf"
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
        login(client)
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        existing = client.post(
            "/api/profiles",
            json={"alias": "saved-profile", "runtime_id": runtime_id, "model_path": str(model)},
        )
        before = client.get("/api/profiles").json()
        invalid = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "invalid-import",
                "runtime_id": runtime_id,
                "command": f'--model "{model}" --unsupported-option value',
            },
        )
        after = client.get("/api/profiles").json()

    assert existing.status_code == 201
    assert invalid.status_code == 422
    assert after == before


def test_profile_import_clears_missing_model_path(tmp_path: Path, login: Login) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            path.resolve(), RuntimeVersion("1", None, "version"),
            RuntimeCapabilities(frozenset({"model", "models-preset"}), "help"), None, (),
        )

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        login(client)
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        imported = client.post("/api/profiles/import", json={"document": {
            "format": "llamawebui-profile-v1", "alias": "portable-model",
            "runtime_id": runtime_id, "enabled": True,
            "configuration": {"model_path": str(tmp_path / "other-machine.gguf")},
        }})

    assert imported.status_code == 201
    assert imported.json()["model_path"] == ""
    assert imported.json()["enabled"] is False


def test_profile_command_import_rejects_malformed_command(tmp_path: Path, login: Login) -> None:
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
        login(client)
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        response = client.post(
            "/api/profiles/import-command",
            json={"alias": "bad", "runtime_id": runtime_id, "command": "--ctx-size 4"},
        )

    assert response.status_code == 422
    assert "--model" in response.json()["detail"]


def test_profile_command_import_rejects_unknown_runtime(tmp_path: Path, login: Login) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as client:
        login(client)
        response = client.post(
            "/api/profiles/import-command",
            json={
                "alias": "missing-runtime",
                "runtime_id": "missing",
                "command": "--model model.gguf",
            },
        )

    assert response.status_code == 404


def test_profile_command_export_reports_missing_profile_and_runtime(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        login(client)
        missing = client.get("/api/profiles/missing/command")

    assert missing.status_code == 404


def test_profile_import_recreates_export_with_exported_enabled_state(
    tmp_path: Path, login: Login
) -> None:
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
        login(client)
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
        login(client)
        imported = client.post("/api/profiles/import", json={"document": exported})

    assert imported.status_code == 201
    assert imported.json()["alias"] == "imported-model"
    assert imported.json()["enabled"] is True
    assert imported.json()["configuration"]["ctx_size"] == 4096


def test_profile_validate_reports_runtime_and_file_errors_without_mutation(
    tmp_path: Path,
    login: Login,
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
        login(client)
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


def test_profile_reset_retains_identity_and_removes_overrides(tmp_path: Path, login: Login) -> None:
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
        login(client)
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


def test_profile_update_populates_and_persists_all_editor_settings(
    tmp_path: Path, login: Login
) -> None:
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
        login(client)
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
