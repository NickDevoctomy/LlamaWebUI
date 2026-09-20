"""Persistence operations for validated model profiles."""

from dataclasses import asdict
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.domain.model_profile import ModelProfile, render_preset
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities
from llamawebui.models import ModelProfileRecord, RuntimeRecord
from llamawebui.services.runtime_registry import RuntimeNotFoundError


class ProfileAliasExistsError(ValueError):
    pass


class ProfileNotFoundError(LookupError):
    pass


class ProfileRegistry:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def list(self) -> list[ModelProfileRecord]:
        with self._sessions() as session:
            statement = select(ModelProfileRecord).order_by(
                ModelProfileRecord.alias, ModelProfileRecord.id
            )
            return list(session.scalars(statement))

    def list_enabled(self, runtime_id: str) -> tuple[ModelProfileRecord, ...]:
        with self._sessions() as session:
            statement = (
                select(ModelProfileRecord)
                .where(
                    ModelProfileRecord.runtime_id == runtime_id,
                    ModelProfileRecord.enabled.is_(True),
                )
                .order_by(ModelProfileRecord.alias, ModelProfileRecord.id)
            )
            return tuple(session.scalars(statement))

    def create(
        self, *, profile: ModelProfile, runtime_id: str, enabled: bool = True
    ) -> ModelProfileRecord:
        with self._sessions() as session:
            runtime = session.get(RuntimeRecord, runtime_id)
            if runtime is None:
                raise RuntimeNotFoundError(f"runtime not found: {runtime_id}")
            if session.scalar(
                select(ModelProfileRecord.id).where(ModelProfileRecord.alias == profile.alias)
            ) is not None:
                raise ProfileAliasExistsError(f"model alias is already registered: {profile.alias}")

            capabilities = RuntimeCapabilities(options=frozenset(runtime.options), raw_help="")
            preset = render_preset(profile, capabilities)
            configuration = asdict(profile)
            configuration["model_path"] = str(profile.model_path.resolve())
            configuration["advanced"] = [asdict(option) for option in profile.advanced]
            record = ModelProfileRecord(
                id=str(uuid4()),
                alias=profile.alias,
                runtime_id=runtime_id,
                model_path=str(profile.model_path.resolve()),
                configuration=configuration,
                preset=preset,
                enabled=enabled,
            )
            session.add(record)
            session.commit()
            return record

    def remove(self, profile_id: str) -> None:
        with self._sessions() as session:
            record = session.get(ModelProfileRecord, profile_id)
            if record is None:
                raise ProfileNotFoundError(f"model profile not found: {profile_id}")
            session.delete(record)
            session.commit()

    def update(
        self, profile_id: str, *, profile: ModelProfile, runtime_id: str, enabled: bool
    ) -> ModelProfileRecord:
        with self._sessions() as session:
            record = session.get(ModelProfileRecord, profile_id)
            if record is None:
                raise ProfileNotFoundError(f"model profile not found: {profile_id}")
            runtime = session.get(RuntimeRecord, runtime_id)
            if runtime is None:
                raise RuntimeNotFoundError(f"runtime not found: {runtime_id}")
            duplicate = session.scalar(
                select(ModelProfileRecord.id).where(
                    ModelProfileRecord.alias == profile.alias,
                    ModelProfileRecord.id != profile_id,
                )
            )
            if duplicate is not None:
                raise ProfileAliasExistsError(f"model alias is already registered: {profile.alias}")
            capabilities = RuntimeCapabilities(options=frozenset(runtime.options), raw_help="")
            record.alias = profile.alias
            record.runtime_id = runtime_id
            record.model_path = str(profile.model_path.resolve())
            record.configuration = asdict(profile)
            record.configuration["model_path"] = record.model_path
            record.configuration["advanced"] = [asdict(option) for option in profile.advanced]
            record.preset = render_preset(profile, capabilities)
            record.enabled = enabled
            session.commit()
            return record

    def clone(self, profile_id: str, alias: str) -> ModelProfileRecord:
        with self._sessions() as session:
            source = session.get(ModelProfileRecord, profile_id)
            if source is None:
                raise ProfileNotFoundError(f"model profile not found: {profile_id}")
            if not alias or alias != alias.strip():
                raise ValueError("profile alias must not be empty or padded")
            if session.scalar(
                select(ModelProfileRecord.id).where(ModelProfileRecord.alias == alias)
            ):
                raise ProfileAliasExistsError(f"model alias is already registered: {alias}")
            clone = ModelProfileRecord(
                id=str(uuid4()),
                alias=alias,
                runtime_id=source.runtime_id,
                model_path=source.model_path,
                configuration=dict(source.configuration),
                preset=source.preset,
                enabled=False,
            )
            session.add(clone)
            session.commit()
            return clone