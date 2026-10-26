"""The assistant's settings: its name and the limits on what a conversation may spend."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from llamawebui.services.settings_store import SettingsStore

ASSISTANT_SETTINGS = "assistant.settings"
DEFAULT_NAME = "Lama"


class AssistantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default=DEFAULT_NAME, min_length=1, max_length=40)
    # A conversation pauses when its replies have cost this much, until told to continue.
    spend_limit_usd: float = Field(default=2.0, gt=0, le=1000)
    # Model calls one message may lead to, tool calls included, before the turn stops.
    max_rounds_per_turn: int = Field(default=40, ge=1, le=500)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        name = value.strip()
        if not name or not name.isprintable():
            raise ValueError("the name must be printable text on one line")
        return name


def load_settings(store: SettingsStore) -> AssistantSettings:
    """The saved settings; defaults for anything missing or no longer valid."""
    saved = store.get_json(ASSISTANT_SETTINGS) or {}
    try:
        return AssistantSettings.model_validate(saved)
    except ValidationError:
        return AssistantSettings()


def save_settings(store: SettingsStore, settings: AssistantSettings) -> None:
    store.set_json(ASSISTANT_SETTINGS, settings.model_dump())
