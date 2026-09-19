from pathlib import Path

from sqlalchemy import inspect

from llamawebui.database import create_database_engine, database_url, upgrade_database


def test_database_url_uses_absolute_sqlite_path(tmp_path: Path) -> None:
    path = tmp_path / "app.db"

    assert database_url(path) == f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def test_upgrade_database_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "app.db"

    upgrade_database(path)
    upgrade_database(path)

    assert path.is_file()
    engine = create_database_engine(path)
    try:
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "download_jobs",
            "model_profiles",
            "runtimes",
            "server_runs",
            "settings",
        }
        runtime_columns = {column["name"] for column in inspect(engine).get_columns("runtimes")}
        assert {"executable_path", "options", "help_sha256"} <= runtime_columns
    finally:
        engine.dispose()