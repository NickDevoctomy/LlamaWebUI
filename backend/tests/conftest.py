"""Shared test helpers for the LlamaWebUI backend test suite."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from llamawebui.services.auth_service import CSRF_HEADER, CSRF_HEADER_VALUE

Login = Callable[[TestClient], TestClient]


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
        assert response.status_code == 200, response.text
        client.headers.update({CSRF_HEADER: CSRF_HEADER_VALUE})
        return client

    return _login