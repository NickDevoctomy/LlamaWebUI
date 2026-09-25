"""Router lifecycle rules and safe launch argument construction."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RouterState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    CRASHED = "crashed"


_TRANSITIONS = {
    RouterState.STOPPED: frozenset({RouterState.STARTING}),
    RouterState.STARTING: frozenset(
        {RouterState.READY, RouterState.STOPPING, RouterState.CRASHED}
    ),
    RouterState.READY: frozenset(
        {RouterState.DEGRADED, RouterState.STOPPING, RouterState.CRASHED}
    ),
    RouterState.DEGRADED: frozenset(
        {RouterState.READY, RouterState.STOPPING, RouterState.CRASHED}
    ),
    RouterState.STOPPING: frozenset({RouterState.STOPPED, RouterState.CRASHED}),
    RouterState.CRASHED: frozenset({RouterState.STARTING, RouterState.STOPPED}),
}


def require_transition(current: RouterState, target: RouterState) -> None:
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid router state transition: {current} -> {target}")


@dataclass(frozen=True, slots=True)
class RouterLaunch:
    executable: Path
    preset_path: Path
    host: str = "127.0.0.1"
    port: int = 1234
    api_key_file: Path | None = None

    def arguments(self) -> tuple[str, ...]:
        executable = self.executable.expanduser().resolve()
        preset = self.preset_path.expanduser().resolve()
        errors: list[str] = []
        if not executable.is_file():
            errors.append(f"llama-server executable not found: {executable}")
        if not preset.is_file():
            errors.append(f"model preset not found: {preset}")
        if not 1 <= self.port <= 65535:
            errors.append(f"router port is outside the valid range: {self.port}")

        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            errors.append(f"router host must be an IP address: {self.host}")
            address = None

        key_file = self.api_key_file.expanduser().resolve() if self.api_key_file else None
        if key_file is not None and not key_file.is_file():
            errors.append(f"API key file not found: {key_file}")
        if address is not None and not address.is_loopback and key_file is None:
            errors.append("non-loopback router binding requires an API key file")
        if errors:
            raise ValueError("; ".join(errors))

        arguments = [
            str(executable),
            "--models-preset",
            str(preset),
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if key_file is not None:
            arguments.extend(("--api-key-file", str(key_file)))
        return tuple(arguments)