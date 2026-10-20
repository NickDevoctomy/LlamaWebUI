"""Persistence operations for validated model profiles."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.model_profile import ModelProfile, render_preset, render_preset_unchecked
from llamawebui.domain.profile_arguments import (
    LEGACY_FIELDS,
    ProfileConfiguration,
    is_structured,
    migrate_legacy_configuration,
    normalize_configuration,
)
from llamawebui.models import ModelProfileRecord, RuntimeRecord
from llamawebui.services.runtime_registry import RuntimeNotFoundError, runtime_capabilities

LOGGER = logging.getLogger("llamawebui.profiles")


class ProfileAliasExistsError(ValueError):
    pass


class ProfileNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyMigrationReport:
    migrated: int
    preset_mismatches: tuple[str, ...]


def stored_configuration(
    profile: ModelProfile, catalog: ArgumentCatalog, *, model_path: str | None = None
) -> dict[str, object]:
    """The JSON document persisted for a profile in the structured (v2) format."""
    configuration = profile.configuration.to_payload(catalog)
    configuration["alias"] = profile.alias
    configuration["model_path"] = (
        model_path if model_path is not None else str(profile.model_path.resolve())
    )
    return configuration


def profile_from_record(record: ModelProfileRecord, catalog: ArgumentCatalog) -> ModelProfile:
    """Build the domain profile for a stored record, reading legacy rows transparently."""
    configuration = normalize_configuration(record.configuration, catalog)
    return ModelProfile(
        alias=record.alias,
        model_path=Path(record.model_path),
        arguments=configuration.arguments,
        acknowledged=configuration.acknowledged,
    )


def configuration_payload(
    record: ModelProfileRecord, catalog: ArgumentCatalog
) -> dict[str, object]:
    """The structured configuration for API responses, converting legacy rows on the fly."""
    if is_structured(record.configuration):
        return dict(record.configuration)
    configuration = normalize_configuration(record.configuration, catalog)
    payload = {
        key: value for key, value in record.configuration.items() if key not in LEGACY_FIELDS
    }
    payload.update(configuration.to_payload(catalog))
    return payload


class ProfileRegistry:
    def __init__(self, engine: Engine, catalog: ArgumentCatalog) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._catalog = catalog

    def list(self) -> list[ModelProfileRecord]:
        with self._sessions() as session:
            statement = select(ModelProfileRecord).order_by(
                ModelProfileRecord.alias, ModelProfileRecord.id
            )
            return list(session.scalars(statement))

    def get(self, profile_id: str) -> ModelProfileRecord:
        with self._sessions() as session:
            record = session.get(ModelProfileRecord, profile_id)
            if record is None:
                raise ProfileNotFoundError(f"model profile not found: {profile_id}")
            return record

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

            preset = render_preset(profile, self._catalog, runtime_capabilities(runtime))
            record = ModelProfileRecord(
                id=str(uuid4()),
                alias=profile.alias,
                runtime_id=runtime_id,
                model_path=str(profile.model_path.resolve()),
                configuration=stored_configuration(profile, self._catalog),
                preset=preset,
                enabled=enabled,
            )
            session.add(record)
            session.commit()
            return record

    def create_unresolved_import(
        self,
        *,
        alias: str,
        runtime_id: str,
        configuration: ProfileConfiguration,
    ) -> ModelProfileRecord:
        """Persist an imported profile whose model path needs local repair."""
        with self._sessions() as session:
            if session.get(RuntimeRecord, runtime_id) is None:
                raise RuntimeNotFoundError(f"runtime not found: {runtime_id}")
            if session.scalar(
                select(ModelProfileRecord.id).where(ModelProfileRecord.alias == alias)
            ) is not None:
                raise ProfileAliasExistsError(f"model alias is already registered: {alias}")
            profile = ModelProfile(
                alias=alias,
                model_path=Path(),
                arguments=configuration.arguments,
                acknowledged=configuration.acknowledged,
            )
            stored = stored_configuration(profile, self._catalog, model_path="")
            stored["enabled"] = False
            record = ModelProfileRecord(
                id=str(uuid4()),
                alias=alias,
                runtime_id=runtime_id,
                model_path="",
                configuration=stored,
                preset=f"version = 1\n\n[{alias}]\n",
                enabled=False,
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
            record.preset = render_preset(profile, self._catalog, runtime_capabilities(runtime))
            record.alias = profile.alias
            record.runtime_id = runtime_id
            record.model_path = str(profile.model_path.resolve())
            record.configuration = stored_configuration(profile, self._catalog)
            record.enabled = enabled
            session.commit()
            return record

    def reset(self, profile_id: str) -> ModelProfileRecord:
        with self._sessions() as session:
            record = session.get(ModelProfileRecord, profile_id)
            if record is None:
                raise ProfileNotFoundError(f"model profile not found: {profile_id}")
            runtime = session.get(RuntimeRecord, record.runtime_id)
            if runtime is None:
                raise RuntimeNotFoundError(f"runtime not found: {record.runtime_id}")
            reset_profile = ModelProfile(alias=record.alias, model_path=Path(record.model_path))
            record.preset = render_preset(
                reset_profile, self._catalog, runtime_capabilities(runtime)
            )
            record.configuration = stored_configuration(
                reset_profile, self._catalog, model_path=record.model_path
            )
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

    def migrate_legacy_configurations(self) -> LegacyMigrationReport:
        """Rewrite legacy flat configurations in the structured format, keeping every key.

        The stored preset, which is what the router launches, is never changed here. The
        original document is retained under ``legacy_configuration`` until the next edit.
        """
        migrated = 0
        mismatches: list[str] = []
        with self._sessions() as session:
            for record in session.scalars(select(ModelProfileRecord)):
                original: Mapping[str, object] = record.configuration
                if is_structured(original):
                    continue
                configuration = migrate_legacy_configuration(original, self._catalog)
                stored = {key: value for key, value in original.items() if key not in LEGACY_FIELDS}
                stored.update(configuration.to_payload(self._catalog))
                stored["legacy_configuration"] = dict(original)
                if record.model_path:
                    rendered = render_preset_unchecked(
                        ModelProfile(
                            alias=record.alias,
                            model_path=Path(record.model_path),
                            arguments=configuration.arguments,
                        ),
                        self._catalog,
                    )
                    if rendered != record.preset:
                        mismatches.append(record.alias)
                record.configuration = stored
                migrated += 1
            session.commit()
        for alias in mismatches:
            LOGGER.warning(
                "profile_migration_preset_mismatch",
                extra={"component": "profiles", "event": "migration_mismatch", "alias": alias},
            )
        return LegacyMigrationReport(migrated=migrated, preset_mismatches=tuple(mismatches))
