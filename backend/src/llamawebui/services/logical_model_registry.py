"""Durable records for locally discovered logical models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.models import LogicalModelRecord


@dataclass(frozen=True, slots=True)
class LogicalModel:
    id: str
    canonical_path: Path
    primary_path: Path
    files: tuple[Path, ...]
    metadata: dict[str, object]
    validation_state: str


class LogicalModelRegistry:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def list(self) -> tuple[LogicalModel, ...]:
        with self._sessions() as session:
            records = session.scalars(
                select(LogicalModelRecord).order_by(LogicalModelRecord.primary_path)
            )
            return tuple(self._domain(record) for record in records)

    def reconcile(self, models: tuple[LogicalModel, ...]) -> tuple[LogicalModel, ...]:
        with self._sessions() as session:
            existing = {
                record.canonical_path
                : record
                for record in session.scalars(select(LogicalModelRecord))
            }
            for model in models:
                record = existing.get(str(model.canonical_path))
                if record is None:
                    record = LogicalModelRecord(
                        id=str(uuid4()), canonical_path=str(model.canonical_path)
                    )
                    session.add(record)
                record.primary_path = str(model.primary_path)
                record.files = [str(path) for path in model.files]
                record.attributes = model.metadata
                record.validation_state = model.validation_state
            session.commit()
            return self.list()

    @staticmethod
    def _domain(record: LogicalModelRecord) -> LogicalModel:
        return LogicalModel(
            id=record.id,
            canonical_path=Path(record.canonical_path),
            primary_path=Path(record.primary_path),
            files=tuple(Path(path) for path in record.files),
            metadata=dict(record.attributes),
            validation_state=record.validation_state,
        )
