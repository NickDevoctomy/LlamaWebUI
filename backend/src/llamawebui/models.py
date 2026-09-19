"""Initial persistence models."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text, func
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
