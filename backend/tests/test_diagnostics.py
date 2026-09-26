from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from conftest import Login
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import create_database_engine
from llamawebui.services.diagnostics import DiagnosticsExporter, _path_summary, _safe_text
from llamawebui.services.logging_utils import configure_logging


def test_diagnostics_export_is_atomic_bounded_and_redacted(
    tmp_path: Path, caplog, login: Login
) -> None:
    configure_logging(secrets=("custom-secret",))
    logging.getLogger("llamawebui.test").warning(
        "Authorization: Bearer custom-secret HF_TOKEN=hf_private_value"
    )
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings)) as client:
        login(client)
        response = client.post("/api/diagnostics/export")

    assert response.status_code == 200
    destination = Path(response.json()["path"])
    assert destination == settings.data_dir / "diagnostics" / "diagnostics-latest.json"
    payload = json.loads(destination.read_text(encoding="utf-8"))
    rendered = destination.read_text(encoding="utf-8")

    assert payload["format"] == 1
    assert payload["database"]["schema_revision"] == "0012_server_lifecycle_privilege"
    assert payload["logs"]
    assert "custom-secret" not in rendered
    assert "hf_private_value" not in rendered
    assert not list(destination.parent.glob(".diagnostics-*.tmp"))


def test_diagnostics_export_excludes_sensitive_profile_and_token_data(
    tmp_path: Path, login: Login
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        login(client)
        token = client.post("/api/tokens", json={"name": "operator", "expiry_note": "private"})
        assert token.status_code == 201
        export = client.post("/api/diagnostics/export")

    rendered = Path(export.json()["path"]).read_text(encoding="utf-8")
    assert token.json()["token"] not in rendered
    assert "token_hash" not in rendered
    assert "api-keys.txt" not in rendered


def test_diagnostics_helpers_keep_metadata_minimal(tmp_path: Path) -> None:
    summary = _path_summary(str(tmp_path / "private.db"))
    assert summary["name"] == "private.db"
    assert summary["exists"] is False
    assert len(summary["sha256"]) == 16
    assert _safe_text(None) is None

    exporter = DiagnosticsExporter(
        Settings(data_dir=tmp_path / "data"),
        create_database_engine(tmp_path / "data" / "llamawebui.db"),
    )
    assert exporter is not None


def test_diagnostics_record_summaries_are_sanitized(tmp_path: Path) -> None:
    exporter = DiagnosticsExporter(
        Settings(data_dir=tmp_path / "data"),
        create_database_engine(tmp_path / "data" / "llamawebui.db"),
    )
    runtime = SimpleNamespace(
        id="runtime-1",
        name="CPU",
        executable_path=str(tmp_path / "llama-server.exe"),
        build="b1",
        commit="c1",
        backend="CPU",
        devices=["CPU"],
        options=["models-preset"],
        help_sha256="hash",
        probe_error="Authorization: Bearer hf_private_value",
    )
    run = SimpleNamespace(
        id="run-1", runtime_id="runtime-1", endpoint="http://127.0.0.1:1234",
        state="stopped", pid=None, exit_code=0, error="hf_private_value",
        started_at=None, ended_at=None,
    )
    download = SimpleNamespace(
        id="download-1", repo_id="owner/model", revision="a" * 40,
        group_key="Q4", files=[{"path": "model.gguf"}], total_bytes=10,
        completed_bytes=10, state="completed", error="hf_private_value",
    )

    runtime_payload = exporter._runtime(runtime)
    run_payload = exporter._server_run(run)
    download_payload = exporter._download(download)

    assert runtime_payload["option_count"] == 1
    assert "hf_private_value" not in json.dumps(runtime_payload)
    assert run_payload["error"] == "[REDACTED]"
    assert download_payload["file_count"] == 1

    profile = SimpleNamespace(
        id="profile-1", alias="demo", runtime_id="missing-runtime", enabled=True,
        model_path=str(tmp_path / "model.gguf"), configuration={},
    )
    profile_payload = exporter._profile(
        profile, [], SimpleNamespace(profile_available=lambda _: False)
    )
    assert profile_payload["validation_errors"] == ["runtime not found: missing-runtime"]