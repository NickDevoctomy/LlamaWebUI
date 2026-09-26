"""Focused tests for control-plane authentication and sessions."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.services.auth_service import (
    CSRF_HEADER,
    CSRF_HEADER_VALUE,
    SESSION_COOKIE_NAME,
)


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path / "data")))


def test_health_is_public(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/health")
    assert response.status_code == 200


def test_control_plane_requires_authentication(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/server/status")
    assert response.status_code == 401


def test_control_plane_requires_csrf_header_for_mutations(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        # Missing CSRF header on a state-changing request.
        response = client.post("/api/server/stop")
    assert response.status_code == 403


def test_unauthenticated_mutations_return_401_before_csrf_check(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        without_header = client.post("/api/server/stop")
        with_header = client.post(
            "/api/server/stop",
            headers={CSRF_HEADER: CSRF_HEADER_VALUE},
        )
    assert without_header.status_code == 401
    assert with_header.status_code == 401


def test_login_logout_and_authenticated_access(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        assert login.json()["username"] == "admin"
        assert login.json()["default_credentials"] is True
        assert SESSION_COOKIE_NAME in login.cookies

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"

        status = client.get("/api/server/status")
        assert status.status_code == 200

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200

        after_logout = client.get("/api/server/status")
        assert after_logout.status_code == 401


def test_login_rejects_bad_credentials(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong"}
        )
    assert response.status_code == 401


def test_change_password_and_default_flag_clears(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        assert login.json()["default_credentials"] is True

        changed = client.post(
            "/api/auth/password",
            json={"current_password": "admin", "new_password": "new-secret"},
            headers={CSRF_HEADER: CSRF_HEADER_VALUE},
        )
        assert changed.status_code == 200

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["default_credentials"] is False

        # Old password no longer works.
        old_login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert old_login.status_code == 401

        # New password works.
        new_login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "new-secret"}
        )
        assert new_login.status_code == 200


def test_change_password_requires_current_password(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        response = client.post(
            "/api/auth/password",
            json={"current_password": "wrong", "new_password": "new-secret"},
            headers={CSRF_HEADER: CSRF_HEADER_VALUE},
        )
    assert response.status_code == 401


def test_password_change_revokes_other_sessions_but_keeps_current_session(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as first_client:
        first_login = first_client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert first_login.status_code == 200

        with _client(tmp_path) as second_client:
            second_login = second_client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            )
            assert second_login.status_code == 200

            changed = first_client.post(
                "/api/auth/password",
                json={"current_password": "admin", "new_password": "new-secret"},
                headers={CSRF_HEADER: CSRF_HEADER_VALUE},
            )
            assert changed.status_code == 200
            assert first_client.get("/api/auth/me").status_code == 200
            assert second_client.get("/api/auth/me").status_code == 401


def test_default_admin_seeded_once(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert first.status_code == 200

    # A second app instance over the same data dir must not duplicate or reset.
    with _client(tmp_path) as client:
        second = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert second.status_code == 200
        assert second.json()["default_credentials"] is True


def test_password_hash_is_never_exposed(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert "password" not in me.text.lower()
        assert "hash" not in me.text.lower()
        assert "admin" in me.text