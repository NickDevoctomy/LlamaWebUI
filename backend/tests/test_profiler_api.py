from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Login
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.services.authorization import privilege_for_request


@pytest.fixture(autouse=True)
def no_nsys_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("llamawebui.services.router_profiler.find_nsys", lambda: None)
    monkeypatch.setattr("llamawebui.services.router_profiler.is_elevated", lambda: False)


def settings_body(**changes: object) -> dict[str, object]:
    return {
        "enabled": True,
        "nsys_path": "",
        "trace": "cuda,nvtx",
        "cpu_sampling": True,
        "sampling_hz": 500,
        "capture_on_start": False,
        "graph_nodes": True,
        **changes,
    }


def test_profiler_settings_are_shown_and_saved(tmp_path: Path, login: Login) -> None:
    nsys = tmp_path / "nsys.exe"
    nsys.write_bytes(b"")
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as client:
        login(client)
        initial = client.get("/api/diagnostics/profiler").json()
        bad_trace = client.put("/api/diagnostics/profiler", json=settings_body(trace="cuda nvtx"))
        bad_rate = client.put("/api/diagnostics/profiler", json=settings_body(sampling_hz=20000))
        status_payload = client.get("/api/server/status").json()
        missing = client.put(
            "/api/diagnostics/profiler", json=settings_body(nsys_path=str(tmp_path / "nope.exe"))
        )
        wrong_name = tmp_path / "cmd.exe"
        wrong_name.write_bytes(b"")
        not_nsys = client.put(
            "/api/diagnostics/profiler", json=settings_body(nsys_path=str(wrong_name))
        )
        saved = client.put("/api/diagnostics/profiler", json=settings_body())
        reloaded = client.get("/api/diagnostics/profiler").json()

    assert initial["config"] == {
        "enabled": False,
        "nsys_path": "",
        "trace": "cuda,nvtx,cublas",
        "cpu_sampling": True,
        "sampling_hz": 250,
        "capture_on_start": True,
        "graph_nodes": False,
    }
    assert initial["nsys"] == {"path": None, "version": None}
    assert initial["elevated"] is False
    assert initial["router_profiled"] is False
    assert initial["captures"] == []
    assert bad_trace.status_code == 422
    assert bad_rate.status_code == 422
    assert "100-8000" in bad_rate.json()["detail"]
    assert status_payload["profiler_saving"] is None
    assert missing.status_code == 422
    assert "not found" in missing.json()["detail"]
    assert not_nsys.status_code == 422
    assert saved.status_code == 200
    assert reloaded["config"] == settings_body()


def test_captures_need_a_profiled_router_and_are_served_by_name(
    tmp_path: Path, login: Login
) -> None:
    data_dir = tmp_path / "data"
    captures = data_dir / "debug" / "captures"
    captures.mkdir(parents=True)
    (captures / "20261029-093015-router.nsys-rep").write_bytes(b"report")
    with TestClient(create_app(Settings(data_dir=data_dir))) as client:
        login(client)
        start = client.post("/api/diagnostics/profiler/capture/start")
        stop = client.post("/api/diagnostics/profiler/capture/stop")
        listed = client.get("/api/diagnostics/profiler").json()["captures"]
        report = "/api/diagnostics/profiler/captures/20261029-093015-router.nsys-rep"
        download = client.get(report)
        unknown = client.get("/api/diagnostics/profiler/captures/other.nsys-rep")
        deleted = client.delete(report)
        deleted_again = client.delete(report)

    assert start.status_code == 409
    assert "not running under the profiler" in start.json()["detail"]
    assert stop.status_code == 409
    assert [item["name"] for item in listed] == ["20261029-093015-router.nsys-rep"]
    assert listed[0]["size"] == 6
    assert download.status_code == 200
    assert download.content == b"report"
    assert "attachment" in download.headers["content-disposition"]
    assert unknown.status_code == 404
    assert deleted.status_code == 200
    assert deleted.json()["captures"] == []
    assert deleted_again.status_code == 404


def test_profiler_routes_use_the_diagnostics_privileges() -> None:
    assert privilege_for_request("/api/diagnostics/profiler", "GET") == "diagnostics.read"
    assert (
        privilege_for_request("/api/diagnostics/profiler/captures/x.nsys-rep", "GET")
        == "diagnostics.read"
    )
    assert privilege_for_request("/api/diagnostics/profiler", "PUT") == "diagnostics.write"
    assert (
        privilege_for_request("/api/diagnostics/profiler/capture/start", "POST")
        == "diagnostics.write"
    )


def test_profiler_routes_require_sign_in(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as client:
        response = client.get("/api/diagnostics/profiler")

    assert response.status_code == 401
