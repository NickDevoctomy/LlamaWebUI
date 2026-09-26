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
        assert login.json()["role"] == "Administrator"
        assert "server.read" in login.json()["privileges"]
        assert SESSION_COOKIE_NAME in login.cookies

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"
        assert me.json()["role"] == "Administrator"
        assert len(me.json()["privileges"]) >= 2

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


def test_roles_and_privileges_are_seeded_and_idempotent(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert first.status_code == 200
        assert first.json()["role"] == "Administrator"
        privilege_count = len(first.json()["privileges"])
        assert privilege_count >= 10

    with _client(tmp_path) as client:
        second = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert second.status_code == 200
        assert len(second.json()["privileges"]) == privilege_count
        user_role = next(role for role in client.get("/api/auth/roles").json() if role["name"] == "User")
        assert user_role["protected"] is True
        assert user_role["privileges"] == ["library.read", "profiles.read", "server.lifecycle.write", "server.read"]


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


def test_authenticated_admin_can_list_and_create_admin_users(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        users = client.get("/api/auth/users")
        assert users.status_code == 200
        assert [user["username"] for user in users.json()] == ["admin"]

        created = client.post(
            "/api/auth/users",
            json={"username": "operator", "password": "operator-secret", "description": "Ops user"},
            headers={CSRF_HEADER: CSRF_HEADER_VALUE},
        )
        assert created.status_code == 201
        assert created.json()["username"] == "operator"
        assert created.json()["description"] == "Ops user"
        assert created.json()["role"] == "Administrator"

        with _client(tmp_path) as operator:
            operator_login = operator.post(
                "/api/auth/login",
                json={"username": "operator", "password": "operator-secret"},
            )
            assert operator_login.status_code == 200


def test_user_creation_rejects_duplicate_and_unauthenticated_requests(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        unauthenticated = client.post(
            "/api/auth/users",
            json={"username": "operator", "password": "operator-secret"},
            headers={CSRF_HEADER: CSRF_HEADER_VALUE},
        )
        assert unauthenticated.status_code == 401
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        headers = {CSRF_HEADER: CSRF_HEADER_VALUE}
        assert client.post(
            "/api/auth/users",
            json={"username": "operator", "password": "operator-secret"},
            headers=headers,
        ).status_code == 201
        duplicate = client.post(
            "/api/auth/users",
            json={"username": "operator", "password": "other-secret"},
            headers=headers,
        )
    assert duplicate.status_code == 409


def test_role_crud_and_user_assignment(tmp_path: Path) -> None:
    headers = {CSRF_HEADER: CSRF_HEADER_VALUE}
    with _client(tmp_path) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        roles = client.get("/api/auth/roles")
        assert roles.status_code == 200
        administrator = next(role for role in roles.json() if role["name"] == "Administrator")
        assert administrator["protected"] is True
        role = client.post(
            "/api/auth/roles",
            json={"name": "Reader", "description": "Read-only", "privileges": ["server.read"]},
            headers=headers,
        )
        assert role.status_code == 201
        reader = role.json()
        assert reader["privileges"] == ["server.read"]
        created = client.post(
            "/api/auth/users",
            json={"username": "reader", "password": "reader-secret", "role_id": reader["id"]},
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["role"] == "Reader"
        updated = client.put(
            f"/api/auth/roles/{reader['id']}",
            json={"name": "Reader", "description": "Updated", "privileges": ["server.read", "library.read"]},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["privileges"] == ["library.read", "server.read"]
        listed = client.get("/api/auth/users")
        assert listed.json()[1]["role"] == "Reader"


def test_protected_and_invalid_role_operations_are_rejected(tmp_path: Path) -> None:
    headers = {CSRF_HEADER: CSRF_HEADER_VALUE}
    with _client(tmp_path) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        administrator = client.get("/api/auth/roles").json()[0]
        protected_update = client.put(
            f"/api/auth/roles/{administrator['id']}",
            json={"name": "Changed", "privileges": []},
            headers=headers,
        )
        assert protected_update.status_code == 403
        invalid = client.post(
            "/api/auth/roles",
            json={"name": "Invalid", "privileges": ["unknown.read"]},
            headers=headers,
        )
        assert invalid.status_code == 422


def test_user_deletion_requires_protected_account_rules(tmp_path: Path) -> None:
    headers = {CSRF_HEADER: CSRF_HEADER_VALUE}
    with _client(tmp_path) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        created = client.post(
            "/api/auth/users",
            json={"username": "operator", "password": "operator-secret"},
            headers=headers,
        )
        assert created.status_code == 201
        user_id = created.json()["id"]
        deleted = client.delete(f"/api/auth/users/{user_id}", headers=headers)
        assert deleted.status_code == 204
        assert client.get("/api/auth/users").json() == [
            item for item in client.get("/api/auth/users").json() if item["username"] == "admin"
        ]

        admin_id = client.get("/api/auth/users").json()[0]["id"]
        protected = client.delete(f"/api/auth/users/{admin_id}", headers=headers)
        assert protected.status_code == 403