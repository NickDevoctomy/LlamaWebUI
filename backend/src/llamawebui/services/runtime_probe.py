"""Probe a llama-server executable without invoking a command shell."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.runtime_capabilities import (
    RuntimeCapabilities,
    RuntimeVersion,
    parse_help_output,
    parse_version_output,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


CommandRunner = Callable[[Sequence[str], float], Awaitable[CommandResult]]


@dataclass(frozen=True, slots=True)
class RuntimeProbeResult:
    executable: Path
    version: RuntimeVersion
    capabilities: RuntimeCapabilities
    devices_output: str | None
    errors: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return not self.errors and bool(self.capabilities.options)


RuntimeProber = Callable[[Path], Awaitable[RuntimeProbeResult]]


async def run_command(arguments: Sequence[str], timeout_seconds: float) -> CommandResult:
    """Run one executable directly and capture decoded output."""

    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise

    return CommandResult(
        returncode=process.returncode or 0,
        stdout=stdout_bytes.decode(errors="replace").strip(),
        stderr=stderr_bytes.decode(errors="replace").strip(),
    )


async def probe_runtime(
    executable: Path,
    *,
    runner: CommandRunner = run_command,
    timeout_seconds: float = 15.0,
) -> RuntimeProbeResult:
    """Probe version, options, and devices advertised by a llama-server executable."""

    resolved = executable.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"llama-server executable not found: {resolved}")

    errors: list[str] = []
    results: dict[str, CommandResult] = {}
    for probe_name, arguments in (
        ("version", (str(resolved), "--version")),
        ("help", (str(resolved), "--help")),
        ("devices", (str(resolved), "--list-devices")),
    ):
        try:
            result = await runner(arguments, timeout_seconds)
        except TimeoutError:
            errors.append(f"{probe_name} probe timed out after {timeout_seconds:g}s")
            continue
        except OSError as error:
            errors.append(f"{probe_name} probe could not start: {error}")
            continue

        results[probe_name] = result
        if result.returncode != 0:
            detail = result.combined_output or "no output"
            errors.append(f"{probe_name} probe exited with {result.returncode}: {detail}")

    version_output = results.get("version", CommandResult(1, "", "")).combined_output
    help_output = results.get("help", CommandResult(1, "", "")).combined_output
    device_result = results.get("devices")
    return RuntimeProbeResult(
        executable=resolved,
        version=parse_version_output(version_output),
        capabilities=parse_help_output(help_output),
        devices_output=device_result.combined_output if device_result else None,
        errors=tuple(errors),
    )
