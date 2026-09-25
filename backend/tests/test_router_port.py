import socket

import pytest

from llamawebui.services.router_port import probe_router_port

pytestmark = pytest.mark.asyncio


async def test_probe_router_port_detects_available_and_occupied_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        host, port = listener.getsockname()

        assert not await probe_router_port(host, port)

    assert await probe_router_port(host, port)