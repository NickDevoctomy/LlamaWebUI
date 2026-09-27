"""Shared test helpers for the LlamaWebUI backend test suite."""

from __future__ import annotations

from collections.abc import Callable

import keyring
import pytest
from fastapi.testclient import TestClient

from llamawebui.services.auth_service import CSRF_HEADER, CSRF_HEADER_VALUE

Login = Callable[[TestClient], TestClient]


@pytest.fixture(autouse=True)
def isolated_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a deterministic credential store without requiring a desktop keyring."""
    credentials: dict[tuple[str, str], str] = {}

    def set_password(service: str, username: str, password: str) -> None:
        credentials[(service, username)] = password

    def get_password(service: str, username: str) -> str | None:
        return credentials.get((service, username))

    def delete_password(service: str, username: str) -> None:
        credentials.pop((service, username), None)

    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)


@pytest.fixture
def login() -> Login:
    """Return a helper that authenticates a TestClient against the default admin account.

    The session cookie is stored on the client and sent on subsequent requests.
    The CSRF header is set as a default header so state-changing requests pass.
    """

    def _login(client: TestClient, username: str = "admin", password: str = "admin") -> TestClient:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        if response.status_code == 401 and username == "admin" and password == "admin":
            password = "test-admin-password"
            response = client.post(
                "/api/auth/login", json={"username": username, "password": password}
            )
        assert response.status_code == 200, response.text
        client.headers.update({CSRF_HEADER: CSRF_HEADER_VALUE})
        if username == "admin" and password == "admin":
            changed = client.post(
                "/api/auth/password",
                json={"current_password": "admin", "new_password": "test-admin-password"},
            )
            assert changed.status_code == 200, changed.text
        return client

    return _login