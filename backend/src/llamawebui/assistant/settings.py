"""The assistant's settings: its name and the limits on what a conversation may spend."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from llamawebui.services.settings_store import SettingsStore

ASSISTANT_SETTINGS = "assistant.settings"
DEFAULT_NAME = "Lama"
# Actions the operator approves before the assistant takes them, and whether each needs it.
APPROVAL_DEFAULTS: dict[str, bool] = {
    "downloads": True,
    "runtimes": False,
    "profiles": True,
    "benchmarks": True,
}


class AssistantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default=DEFAULT_NAME, min_length=1, max_length=40)
    # A conversation pauses when its replies have cost this much, until told to continue.
    spend_limit_usd: float = Field(default=2.0, gt=0, le=1000)
    # Model calls one message may lead to, tool calls included, before the turn stops.
    max_rounds_per_turn: int = Field(default=40, ge=1, le=500)
    approvals: dict[str, bool] = Field(default_factory=lambda: dict(APPROVAL_DEFAULTS))
    # Write research and reports straight into the repository's shipped folders (source
    # checkouts only) instead of waiting for them to be published by hand.
    auto_publish: bool = False
    # The OpenRouter model that answers web searches; None uses the assistant's model.
    search_model: str | None = Field(default=None, max_length=200, pattern=r"^[\w.:/-]+$")

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        name = value.strip()
        if not name or not name.isprintable():
            raise ValueError("the name must be printable text on one line")
        return name

    @field_validator("approvals")
    @classmethod
    def _known_categories(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = set(value) - set(APPROVAL_DEFAULTS)
        if unknown:
            raise ValueError(f"unknown approval categories: {', '.join(sorted(unknown))}")
        # Categories added since the settings were saved take their defaults.
        return {**APPROVAL_DEFAULTS, **value}

    def needs_approval(self, category: str | None) -> bool:
        return category is not None and self.approvals.get(category, True)


def load_settings(store: SettingsStore) -> AssistantSettings:
    """The saved settings; defaults for anything missing or no longer valid."""
    saved = store.get_json(ASSISTANT_SETTINGS) or {}
    try:
        return AssistantSettings.model_validate(saved)
    except ValidationError:
        return AssistantSettings()


def save_settings(store: SettingsStore, settings: AssistantSettings) -> None:
    store.set_json(ASSISTANT_SETTINGS, settings.model_dump())
