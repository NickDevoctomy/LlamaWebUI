from __future__ import annotations

import sqlite3
from pathlib import Path

import keyring
import keyring.errors
import pytest
from alembic import command
from conftest import Login
from fastapi import Request
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import migration_config
from llamawebui.local_requests import is_local_request
from llamawebui.services.authorization import privilege_for_request
from llamawebui.services.secret_store import SECRET_SERVICE

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("192.168.1.20", 50000)


def test_secrets_can_be_saved_listed_and_removed(tmp_path: Path, login: Login) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app, client=LOCAL) as client:
        login(client)
        privileges = set(client.get("/api/auth/me").json()["privileges"])
        assert {"secrets.read", "secrets.write"} <= privileges

        empty = client.get("/api/secrets").json()
        saved = client.put("/api/secrets/huggingface-token", json={"value": " hf_abcd1234 "})
        listed = client.get("/api/secrets").json()
        removed = client.delete("/api/secrets/huggingface-token")

    assert empty["store"]
    assert [item["configured"] for item in empty["secrets"]] == [False, False]
    assert saved.status_code == 200
    assert saved.json() == {
        "name": "huggingface-token",
        "configured": True,
        "last_four": "1234",
    }
    assert "hf_abcd1234" not in str(listed)
    assert listed["secrets"][0]["last_four"] == "1234"
    assert listed["secrets"][1] == {
        "name": "openrouter-api-key",
        "configured": False,
        "last_four": None,
    }
    assert removed.json()["configured"] is False
    assert keyring.get_password(SECRET_SERVICE, "huggingface-token") is None


def test_invalid_values_and_names_are_rejected(tmp_path: Path, login: Login) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app, client=LOCAL) as client:
        login(client)
        spaced = client.put("/api/secrets/openrouter-api-key", json={"value": "sk or"})
        unknown = client.put("/api/secrets/github-token", json={"value": "ghp_x"})

    assert spaced.status_code == 422
    assert spaced.json()["detail"] == "secret must not contain spaces or control characters"
    assert unknown.status_code == 422


def test_an_unavailable_store_is_a_503(
    tmp_path: Path, login: Login, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app, client=LOCAL) as client:
        login(client)
        monkeypatch.setattr(keyring, "get_password", fail)
        monkeypatch.setattr(keyring, "set_password", fail)
        responses = [
            client.get("/api/secrets"),
            client.put("/api/secrets/huggingface-token", json={"value": "hf_value"}),
            client.delete("/api/secrets/huggingface-token"),
        ]

    assert [response.status_code for response in responses] == [503, 503, 503]
    assert "NoKeyringError" in responses[0].json()["detail"]


def test_remote_secrets_access_is_off_until_turned_on_locally(
    tmp_path: Path, login: Login
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app, client=REMOTE) as remote, TestClient(app, client=LOCAL) as local:
        login(local)
        login(remote, "admin", "test-admin-password")
        default = remote.get("/api/settings/security").json()
        blocked = [
            remote.get("/api/secrets"),
            remote.put("/api/secrets/huggingface-token", json={"value": "hf_remote"}),
            remote.delete("/api/secrets/huggingface-token"),
        ]
        enable_remotely = remote.put("/api/settings/security", json={"allow_remote_secrets": True})
        enabled = local.put("/api/settings/security", json={"allow_remote_secrets": True})
        allowed = remote.get("/api/secrets")
        disabled = remote.put("/api/settings/security", json={"allow_remote_secrets": False})
        blocked_again = remote.get("/api/secrets")
        local_view = local.get("/api/settings/security").json()

    assert default == {"allow_remote_secrets": False, "local_request": False}
    assert [response.status_code for response in blocked] == [403, 403, 403]
    assert "from the server itself" in blocked[0].json()["detail"]
    assert keyring.get_password(SECRET_SERVICE, "huggingface-token") is None
    assert enable_remotely.status_code == 403
    assert enabled.json() == {"allow_remote_secrets": True, "local_request": True}
    assert allowed.status_code == 200
    assert disabled.json() == {"allow_remote_secrets": False, "local_request": False}
    assert blocked_again.status_code == 403
    assert local_view == {"allow_remote_secrets": False, "local_request": True}


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True),
        ("127.5.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("::ffff:10.0.0.5", False),
        ("10.0.0.5", False),
        ("testclient", False),
    ],
)
def test_local_requests_are_recognised(host: str, local: bool) -> None:
    request = Request({"type": "http", "client": (host, 50000), "headers": []})
    assert is_local_request(request) is local


def test_a_request_without_a_client_is_not_local() -> None:
    assert is_local_request(Request({"type": "http", "client": None, "headers": []})) is False


def test_secrets_have_their_own_privileges() -> None:
    assert privilege_for_request("/api/secrets", "GET") == "secrets.read"
    assert privilege_for_request("/api/secrets/huggingface-token", "PUT") == "secrets.write"
    assert privilege_for_request("/api/secrets/huggingface-token", "DELETE") == "secrets.write"


def test_migration_grants_secrets_privileges_to_existing_administrators(tmp_path: Path) -> None:
    database = tmp_path / "llamawebui.db"
    config = migration_config(database)
    command.upgrade(config, "0029_benchmark_resources")
    with sqlite3.connect(database) as connection:
        # Databases created before this release have no secrets privileges.
        connection.execute("DELETE FROM role_privileges WHERE privilege_key LIKE 'secrets.%'")
        connection.execute("DELETE FROM privileges WHERE resource = 'secrets'")

    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        granted = connection.execute(
            "SELECT privilege_key FROM role_privileges JOIN roles ON roles.id = role_id "
            "WHERE roles.name = 'Administrator' AND privilege_key LIKE 'secrets.%' ORDER BY 1"
        ).fetchall()
        user_granted = connection.execute(
            "SELECT count(*) FROM role_privileges JOIN roles ON roles.id = role_id "
            "WHERE roles.name = 'User' AND privilege_key LIKE 'secrets.%'"
        ).fetchone()
    assert granted == [("secrets.read",), ("secrets.write",)]
    assert user_granted == (0,)

    command.downgrade(config, "0029_benchmark_resources")
    with sqlite3.connect(database) as connection:
        remaining = connection.execute(
            "SELECT count(*) FROM privileges WHERE resource = 'secrets'"
        ).fetchone()
    assert remaining == (0,)
