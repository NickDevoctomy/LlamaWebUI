"""Persist router process attempts and lifecycle outcomes."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from llamawebui.domain.router_lifecycle import RouterState
from llamawebui.models import ServerRunRecord

_TERMINAL_STATES = frozenset({RouterState.STOPPED, RouterState.CRASHED})


class ServerRunNotFoundError(LookupError):
    pass


class ServerRunRegistry:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def list(self) -> list[ServerRunRecord]:
        with self._sessions() as session:
            statement = select(ServerRunRecord).order_by(ServerRunRecord.started_at.desc())
            return list(session.scalars(statement))

    def get(self, run_id: str) -> ServerRunRecord:
        with self._sessions() as session:
            return self._get(session, run_id)

    def previous_runtime_id(self, current_run_id: str) -> str | None:
        """Return the most recent distinct runtime used before the active run."""
        with self._sessions() as session:
            current = self._get(session, current_run_id)
            statement = select(ServerRunRecord).order_by(ServerRunRecord.started_at.desc())
            for run in session.scalars(statement):
                if (
                    run.id != current.id
                    and run.runtime_id
                    and run.runtime_id != current.runtime_id
                    and run.error is None
                    and run.state in {RouterState.READY, RouterState.STOPPED}
                ):
                    return run.runtime_id
        return None

    def create(self, runtime_id: str, endpoint: str) -> ServerRunRecord:
        record = ServerRunRecord(
            id=str(uuid4()),
            runtime_id=runtime_id,
            endpoint=endpoint,
            state=RouterState.STARTING,
            pid=None,
            exit_code=None,
            error=None,
            ended_at=None,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
        return record

    def update(
        self,
        run_id: str,
        state: RouterState,
        *,
        pid: int | None,
        exit_code: int | None,
        error: str | None = None,
    ) -> ServerRunRecord:
        with self._sessions() as session:
            record = self._get(session, run_id)
            record.state = state
            record.pid = pid if pid is not None else record.pid
            record.exit_code = exit_code
            if error is not None:
                record.error = error
            if state in _TERMINAL_STATES and record.ended_at is None:
                record.ended_at = datetime.now()
            session.commit()
            return record

    @staticmethod
    def _get(session: Session, run_id: str) -> ServerRunRecord:
        record = session.get(ServerRunRecord, run_id)
        if record is None:
            raise ServerRunNotFoundError(f"server run not found: {run_id}")
        return record