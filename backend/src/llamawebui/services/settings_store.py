"""Persist control-plane preferences in the settings table."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from llamawebui.models import SettingRecord

ALLOW_REMOTE_SECRETS = "security.allow_remote_secrets"


class SettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def get_bool(self, key: str, default: bool = False) -> bool:
        with self._sessions() as session:
            record = session.get(SettingRecord, key)
        return default if record is None else record.value == "true"

    def set_bool(self, key: str, value: bool) -> None:
        with self._sessions() as session:
            session.merge(SettingRecord(key=key, value="true" if value else "false"))
            session.commit()
