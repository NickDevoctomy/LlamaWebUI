"""Initial persistence models."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RuntimeRecord(Base):
    __tablename__ = "runtimes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    executable_path: Mapped[str] = mapped_column(Text, unique=True)
    build: Mapped[str | None] = mapped_column(String(100))
    commit: Mapped[str | None] = mapped_column(String(40))
    backend: Mapped[str | None] = mapped_column(String(50))
    devices: Mapped[list[str]] = mapped_column(JSON, default=list)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    help_sha256: Mapped[str] = mapped_column(String(64))
    probe_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ModelProfileRecord(Base):
    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), unique=True)
    runtime_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtimes.id", ondelete="RESTRICT"), index=True
    )
    model_path: Mapped[str] = mapped_column(Text)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON)
    preset: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class DownloadJobRecord(Base):
    __tablename__ = "download_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(400))
    revision: Mapped[str] = mapped_column(String(64))
    group_key: Mapped[str] = mapped_column(Text)
    files: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    destination: Mapped[str] = mapped_column(Text)
    total_bytes: Mapped[int] = mapped_column()
    completed_bytes: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ServerRunRecord(Base):
    __tablename__ = "server_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runtimes.id", ondelete="SET NULL"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    pid: Mapped[int | None] = mapped_column()
    exit_code: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
