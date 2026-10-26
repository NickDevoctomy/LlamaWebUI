"""Persist control-plane preferences in the settings table."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from llamawebui.models import SettingRecord

ALLOW_REMOTE_SECRETS = "security.allow_remote_secrets"
# The model the assistant uses, with its OpenRouter pricing as it was when selected.
OPENROUTER_MODEL = "assistant.openrouter_model"


class SettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def get_bool(self, key: str, default: bool = False) -> bool:
        with self._sessions() as session:
            record = session.get(SettingRecord, key)
        return default if record is None else record.value == "true"

    def set_bool(self, key: str, value: bool) -> None:
        self._set(key, "true" if value else "false")

    def get_json(self, key: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            record = session.get(SettingRecord, key)
        if record is None:
            return None
        value = json.loads(record.value)
        return value if isinstance(value, dict) else None

    def set_json(self, key: str, value: dict[str, Any]) -> None:
        self._set(key, json.dumps(value, sort_keys=True))

    def delete(self, key: str) -> None:
        with self._sessions() as session:
            if (record := session.get(SettingRecord, key)) is not None:
                session.delete(record)
                session.commit()

    def _set(self, key: str, value: str) -> None:
        with self._sessions() as session:
            session.merge(SettingRecord(key=key, value=value))
            session.commit()
