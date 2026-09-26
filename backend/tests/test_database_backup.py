from pathlib import Path

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.services.database_backup import (
    DatabaseBackupError,
    backup_database,
    restore_database,
    validate_database,
)


def test_backup_is_valid_and_retention_is_bounded(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    upgrade_database(database)
    backups = tmp_path / "backups"

    created = [backup_database(database, backups, keep=2) for _ in range(4)]

    assert all(path is not None for path in created)
    assert len(tuple(backups.glob("*.db"))) == 2
    assert validate_database(created[-1])


def test_restore_validates_and_preserves_recovery_copy(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    upgrade_database(database)
    engine = create_database_engine(database)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO marker VALUES ('before')")
    backup = backup_database(database, tmp_path / "backups")
    assert backup is not None
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO marker VALUES ('after')")
    engine.dispose()

    recovery = restore_database(backup, database)

    assert recovery is not None and validate_database(recovery)
    with create_database_engine(database).connect() as connection:
        assert connection.exec_driver_sql("SELECT value FROM marker").scalar_one() == "before"


def test_restore_rejects_corrupt_backup_without_touching_database(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    upgrade_database(database)
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")

    with pytest.raises(DatabaseBackupError, match="integrity"):
        restore_database(corrupt, database)

    assert validate_database(database)