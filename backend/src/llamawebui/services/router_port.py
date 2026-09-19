"""Check router port availability without interacting with an occupying process."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable

RouterPortProbe = Callable[[str, int], Awaitable[bool]]


async def probe_router_port(host: str, port: int) -> bool:
    return await asyncio.to_thread(_can_bind, host, port)


def _can_bind(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((host, port))
    except OSError:
        return False
    return True