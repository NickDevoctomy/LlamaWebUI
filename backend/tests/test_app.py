from pathlib import Path

from conftest import Login
from fastapi.testclient import TestClient
from pydantic import SecretStr

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.runtime_probe import RuntimeProbeResult


def test_health_creates_data_directory_without_exposing_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "data"
    settings = Settings(data_dir=data_dir, hf_token=SecretStr("hf_private"))

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "data_dir": str(data_dir.resolve()),
        "database_path": str(data_dir.resolve() / "llamawebui.db"),
        "hugging_face_token_configured": True,
    }
    assert "hf_private" not in response.text
    assert data_dir.is_dir()
    assert settings.database_path.is_file()


def test_packaged_static_directory_serves_frontend_without_vite(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><html><body>packaged-ui</body></html>", encoding="utf-8"
    )
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings, static_dir=static_dir)) as client:
        frontend = client.get("/")
        health = client.get("/api/health")

    assert frontend.status_code == 200
    assert "packaged-ui" in frontend.text
    assert frontend.headers["content-type"].startswith("text/html")
    assert health.status_code == 200


def test_access_token_is_shown_once_and_can_be_revoked(tmp_path: Path, login: Login) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings)) as client:
        login(client)
        created = client.post(
            "/api/tokens", json={"name": "OpenCode", "expiry_note": "Rotate monthly"}
        )
        token = created.json()["token"]
        listed = client.get("/api/tokens")
        revoked = client.delete(f"/api/tokens/{created.json()['id']}")
        missing = client.delete("/api/tokens/missing")

    assert created.status_code == 201
    assert token.startswith("lwui_")
    assert created.json()["last_four"] == token[-4:]
    assert created.json()["expiry_note"] == "Rotate monthly"
    assert listed.status_code == 200
    assert "token" not in listed.json()[0]
    assert token not in listed.text
    assert revoked.status_code == 200
    assert revoked.json()["enabled"] is False
    assert missing.status_code == 404

    with TestClient(create_app(settings)) as client:
        login(client)
        persisted = client.get("/api/tokens")

    assert persisted.json()[0]["enabled"] is False
    assert token not in persisted.text


def test_runtime_registration_persists_and_rejects_duplicate(tmp_path: Path, login: Login) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    probe_calls = 0

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        nonlocal probe_calls
        probe_calls += 1
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="b11053", commit="1af554f8f", raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"models-preset", "api-key"}), raw_help="--models-preset"
            ),
            devices_output="CPU",
            errors=(),
        )

    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, runtime_prober=fake_probe)
    with TestClient(app) as client:
        login(client)
        created = client.post(
            "/api/runtimes",
            json={"name": "Local CPU", "executable_path": str(executable), "backend": "cpu"},
        )
        duplicate = client.post(
            "/api/runtimes",
            json={"name": "Again", "executable_path": str(executable)},
        )
        listed = client.get("/api/runtimes")

    assert created.status_code == 201
    assert created.json()["build"] == "b11053"
    assert created.json()["devices"] == ["CPU"]
    assert created.json()["usable"] is True
    assert duplicate.status_code == 409
    assert listed.status_code == 200
    assert [runtime["name"] for runtime in listed.json()] == ["Local CPU"]
    assert probe_calls == 1

    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        login(client)
        assert client.get("/api/runtimes").json()[0]["id"] == created.json()["id"]


def test_runtime_registration_reports_missing_executable(tmp_path: Path, login: Login) -> None:
    async def fake_probe(path: Path) -> RuntimeProbeResult:
        raise FileNotFoundError(f"llama-server executable not found: {path}")

    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, runtime_prober=fake_probe)) as client:
        login(client)
        response = client.post(
            "/api/runtimes",
            json={"name": "Missing", "executable_path": str(tmp_path / "missing.exe")},
        )

    assert response.status_code == 404
    assert "llama-server executable not found" in response.json()["detail"]


def test_runtime_can_be_reprobed_and_removed(tmp_path: Path, login: Login) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    build = "old"

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build=build, commit=None, raw=build),
            capabilities=RuntimeCapabilities(
                options=frozenset({"models-preset", build}), raw_help=build
            ),
            devices_output=None,
            errors=(),
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    ) as client:
        login(client)
        created = client.post(
            "/api/runtimes",
            json={"name": "Mutable runtime", "executable_path": str(executable)},
        ).json()
        runtime_id = created["id"]
        build = "new"

        reprobed = client.post(f"/api/runtimes/{runtime_id}/probe")
        fetched = client.get(f"/api/runtimes/{runtime_id}")
        removed = client.delete(f"/api/runtimes/{runtime_id}")
        missing = client.get(f"/api/runtimes/{runtime_id}")
        remove_missing = client.delete(f"/api/runtimes/{runtime_id}")
        probe_missing = client.post("/api/runtimes/unknown/probe")

    assert reprobed.status_code == 200
    assert reprobed.json()["build"] == "new"
    assert reprobed.json()["options"] == ["models-preset", "new"]
    assert fetched.json()["build"] == "new"
    assert removed.status_code == 204
    assert missing.status_code == 404
    assert remove_missing.status_code == 404
    assert probe_missing.status_code == 404


def test_reprobe_missing_executable_preserves_runtime(tmp_path: Path, login: Login) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        if not path.exists():
            raise FileNotFoundError(f"llama-server executable not found: {path}")
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="original", commit=None, raw="original"),
            capabilities=RuntimeCapabilities(options=frozenset({"help"}), raw_help="help"),
            devices_output=None,
            errors=(),
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    ) as client:
        login(client)
        runtime_id = client.post(
            "/api/runtimes",
            json={"name": "Removed binary", "executable_path": str(executable)},
        ).json()["id"]
        executable.unlink()

        response = client.post(f"/api/runtimes/{runtime_id}/probe")
        persisted = client.get(f"/api/runtimes/{runtime_id}")

    assert response.status_code == 404
    assert persisted.json()["build"] == "original"