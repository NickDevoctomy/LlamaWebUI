"""SQLite engine and schema migration helpers."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine


def database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def create_database_engine(path: Path) -> Engine:
    return create_engine(database_url(path), connect_args={"check_same_thread": False})


def migration_config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url(path))
    return config


def upgrade_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(migration_config(path), "head")
