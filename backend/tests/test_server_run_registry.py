from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.router_lifecycle import RouterState
from llamawebui.models import RuntimeRecord
from llamawebui.services.server_run_registry import (
    ServerRunNotFoundError,
    ServerRunRegistry,
)


def create_registry(tmp_path: Path) -> tuple[ServerRunRegistry, str]:
    database = tmp_path / "app.db"
    upgrade_database(database)
    engine = create_database_engine(database)
    with Session(engine) as session:
        runtime = RuntimeRecord(
            id="runtime-id",
            name="CPU",
            executable_path=str(tmp_path / "llama-server.exe"),
            build="1",
            commit=None,
            backend="cpu",
            devices=[],
            options=[],
            help_sha256="0" * 64,
            probe_error=None,
        )
        session.add(runtime)
        session.commit()
        runtime_id = runtime.id
    return ServerRunRegistry(engine), runtime_id


def test_server_run_registry_records_lifecycle(tmp_path: Path) -> None:
    registry, runtime_id = create_registry(tmp_path)
    run = registry.create(runtime_id, "http://127.0.0.1:1234")

    registry.update(run.id, RouterState.READY, pid=42, exit_code=None)
    stopped = registry.update(run.id, RouterState.STOPPED, pid=None, exit_code=0)

    assert stopped.state == RouterState.STOPPED
    assert stopped.pid == 42
    assert stopped.exit_code == 0
    assert stopped.ended_at is not None
    assert registry.list()[0].id == run.id


def test_server_run_registry_records_failure_and_rejects_missing(tmp_path: Path) -> None:
    registry, runtime_id = create_registry(tmp_path)
    run = registry.create(runtime_id, "http://127.0.0.1:1234")

    failed = registry.update(
        run.id,
        RouterState.CRASHED,
        pid=None,
        exit_code=17,
        error="unexpected exit",
    )

    assert failed.error == "unexpected exit"
    assert failed.ended_at is not None
    with pytest.raises(ServerRunNotFoundError):
        registry.update("missing", RouterState.STOPPED, pid=None, exit_code=0)