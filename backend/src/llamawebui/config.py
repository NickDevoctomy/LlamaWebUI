"""Validated application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LLAMAWEBUI_",
        extra="ignore",
        validate_default=True,
    )

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    hf_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HF_TOKEN", "LLAMAWEBUI_HF_TOKEN"),
    )

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "llamawebui.db"
