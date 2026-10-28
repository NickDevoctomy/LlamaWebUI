"""Whether a request comes from the machine the control plane runs on."""

from __future__ import annotations

from ipaddress import IPv6Address, ip_address

from starlette.requests import Request


def is_local_request(request: Request) -> bool:
    """Whether the request comes from this machine, after any trusted proxy headers."""
    if request.client is None:
        return False
    try:
        address = ip_address(request.client.host)
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback
