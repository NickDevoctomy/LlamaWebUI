"""Persistence operations for registered llama.cpp runtimes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from llamawebui.models import RuntimeRecord
from llamawebui.services.runtime_probe import RuntimeProber, RuntimeProbeResult


class RuntimeAlreadyRegisteredError(ValueError):
    pass


class RuntimeNotFoundError(LookupError):
    pass


class RuntimeInUseError(ValueError):
    pass


class RuntimeRegistry:
    def __init__(self, engine: Engine, *, prober: RuntimeProber) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._prober = prober

    def list(self) -> list[RuntimeRecord]:
        with self._sessions() as session:
            statement = select(RuntimeRecord).order_by(RuntimeRecord.name, RuntimeRecord.id)
            return list(session.scalars(statement))

    def get(self, runtime_id: str) -> RuntimeRecord:
        with self._sessions() as session:
            return self._get(session, runtime_id)

    async def register(
        self, *, name: str, executable_path: str | Path, backend: str | None
    ) -> RuntimeRecord:
        resolved = Path(executable_path).expanduser().resolve()
        with self._sessions() as session:
            self._ensure_unique(session, resolved)

        probe = await self._prober(resolved)
        record = RuntimeRecord(
            id=str(uuid4()),
            name=name.strip(),
            executable_path=str(probe.executable),
            backend=backend,
            build=None,
            commit=None,
            devices=[],
            options=[],
            help_sha256="",
            probe_error=None,
        )
        self._apply_probe(record, probe)
        with self._sessions() as session:
            session.add(record)
            session.commit()
        return record

    async def reprobe(self, runtime_id: str) -> RuntimeRecord:
        with self._sessions() as session:
            record = self._get(session, runtime_id)
            executable_path = Path(record.executable_path)

        probe = await self._prober(executable_path)
        with self._sessions() as session:
            record = self._get(session, runtime_id)
            self._apply_probe(record, probe)
            session.commit()
            return record

    def remove(self, runtime_id: str) -> None:
        with self._sessions() as session:
            record = self._get(session, runtime_id)
            session.delete(record)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise RuntimeInUseError(f"runtime is in use: {runtime_id}") from error

    @staticmethod
    def _ensure_unique(session: Session, executable_path: Path) -> None:
        statement = select(RuntimeRecord.id).where(
            RuntimeRecord.executable_path == str(executable_path)
        )
        if session.scalar(statement) is not None:
            raise RuntimeAlreadyRegisteredError(
                f"runtime executable is already registered: {executable_path}"
            )

    @staticmethod
    def _get(session: Session, runtime_id: str) -> RuntimeRecord:
        record = session.get(RuntimeRecord, runtime_id)
        if record is None:
            raise RuntimeNotFoundError(f"runtime not found: {runtime_id}")
        return record

    @staticmethod
    def _apply_probe(record: RuntimeRecord, probe: RuntimeProbeResult) -> None:
        raw_help = probe.capabilities.raw_help
        record.executable_path = str(probe.executable)
        record.build = probe.version.build
        record.commit = probe.version.commit
        record.devices = probe.devices_output.splitlines() if probe.devices_output else []
        record.options = sorted(probe.capabilities.options)
        record.help_sha256 = hashlib.sha256(raw_help.encode()).hexdigest()
        record.probe_error = "\n".join(probe.errors) or None