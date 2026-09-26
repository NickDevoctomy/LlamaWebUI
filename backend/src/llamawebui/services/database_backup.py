"""Validated, bounded SQLite backups and atomic restores."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class DatabaseBackupError(RuntimeError):
    """Raised when a database backup or restore cannot be completed safely."""


def backup_database(database: Path, backup_dir: Path, *, keep: int = 5) -> Path | None:
    """Create a consistent SQLite backup before migration or maintenance."""
    if not database.exists() or database.stat().st_size == 0:
        return None
    if keep < 1:
        raise ValueError("backup retention must be at least one")
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"llamawebui-{name}-{uuid4().hex[:8]}.db"
    _copy_database(database, destination)
    _retain_backups(backup_dir, keep)
    return destination


def restore_database(
    backup: Path, database: Path, *, recovery_dir: Path | None = None
) -> Path | None:
    """Validate and atomically restore a backup, preserving the current database."""
    if not validate_database(backup):
        raise DatabaseBackupError("backup failed SQLite integrity validation")
    database.parent.mkdir(parents=True, exist_ok=True)
    recovery: Path | None = None
    if database.exists() and database.stat().st_size:
        recovery_root = recovery_dir or database.parent / "backups"
        recovery = backup_database(database, recovery_root)
    temporary = database.with_name(f".{database.name}.{uuid4().hex}.restore")
    try:
        _copy_database(backup, temporary)
        if not validate_database(temporary):
            raise DatabaseBackupError("restored database failed SQLite integrity validation")
        temporary.replace(database)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return recovery


def validate_database(database: Path) -> bool:
    """Return whether SQLite reports the database as structurally sound."""
    if not database.is_file():
        return False
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        result = connection.execute("PRAGMA integrity_check").fetchone()
        return bool(result == ("ok",))
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()


def _copy_database(source: Path, destination: Path) -> None:
    source_connection: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source)
        target = sqlite3.connect(destination)
        source_connection.backup(target)
        target.commit()
        if not validate_database(destination):
            raise DatabaseBackupError("database backup failed SQLite integrity validation")
    except sqlite3.Error as error:
        destination.unlink(missing_ok=True)
        raise DatabaseBackupError("database backup failed") from error
    finally:
        if target is not None:
            target.close()
        if source_connection is not None:
            source_connection.close()


def _retain_backups(backup_dir: Path, keep: int) -> None:
    backups = sorted(backup_dir.glob("llamawebui-*.db"), key=lambda path: path.stat().st_mtime)
    for path in backups[:-keep]:
        path.unlink(missing_ok=True)