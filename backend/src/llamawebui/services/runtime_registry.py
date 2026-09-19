"""Persistence operations for registered llama.cpp runtimes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from llamawebui.models import RuntimeRecord
from llamawebui.services.runtime_probe import RuntimeProber


class RuntimeAlreadyRegisteredError(ValueError):
    pass


class RuntimeRegistry:
    def __init__(self, engine: Engine, *, prober: RuntimeProber) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._prober = prober

    def list(self) -> list[RuntimeRecord]:
        with self._sessions() as session:
            statement = select(RuntimeRecord).order_by(RuntimeRecord.name, RuntimeRecord.id)
            return list(session.scalars(statement))

    async def register(
        self, *, name: str, executable_path: str | Path, backend: str | None
    ) -> RuntimeRecord:
        resolved = Path(executable_path).expanduser().resolve()
        with self._sessions() as session:
            self._ensure_unique(session, resolved)

        probe = await self._prober(resolved)
        raw_help = probe.capabilities.raw_help
        record = RuntimeRecord(
            id=str(uuid4()),
            name=name.strip(),
            executable_path=str(probe.executable),
            build=probe.version.build,
            commit=probe.version.commit,
            backend=backend,
            devices=probe.devices_output.splitlines() if probe.devices_output else [],
            options=sorted(probe.capabilities.options),
            help_sha256=hashlib.sha256(raw_help.encode()).hexdigest(),
            probe_error="\n".join(probe.errors) or None,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
        return record

    @staticmethod
    def _ensure_unique(session: Session, executable_path: Path) -> None:
        statement = select(RuntimeRecord.id).where(
            RuntimeRecord.executable_path == str(executable_path)
        )
        if session.scalar(statement) is not None:
            raise RuntimeAlreadyRegisteredError(
                f"runtime executable is already registered: {executable_path}"
            )