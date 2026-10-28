"""Named snapshots of model profiles: take one before changing a profile, restore it later.

A snapshot keeps everything that decides how the profile launches (runtime, model,
arguments, enabled and exclusive), its description and its version. Restoring keeps the
profile's current alias, and needs the router stopped like any other profile edit.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.domain.argument_catalog import ArgumentCatalog
from llamawebui.domain.model_profile import ModelProfile
from llamawebui.domain.profile_arguments import normalize_configuration
from llamawebui.models import ModelProfileRecord, ProfileSnapshotRecord
from llamawebui.services.profile_registry import ProfileRegistry


class SnapshotNotFoundError(LookupError):
    pass


class ProfileSnapshotRegistry:
    def __init__(self, engine: Engine, profiles: ProfileRegistry, catalog: ArgumentCatalog) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._profiles = profiles
        self._catalog = catalog

    def create(
        self,
        profile_id: str,
        name: str,
        *,
        created_by: str,
        conversation_id: str | None = None,
    ) -> ProfileSnapshotRecord:
        profile = self._profiles.get(profile_id)
        record = ProfileSnapshotRecord(
            id=str(uuid4()),
            profile_id=profile.id,
            name=name.strip()[:120] or "Snapshot",
            created_by=created_by,
            conversation_id=conversation_id,
            runtime_id=profile.runtime_id,
            model_path=profile.model_path,
            description=profile.description,
            configuration=dict(profile.configuration),
            enabled=profile.enabled,
            exclusive=profile.exclusive,
            version=profile.version,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
        return record

    def list(
        self, *, profile_id: str | None = None, conversation_id: str | None = None
    ) -> list[ProfileSnapshotRecord]:
        """Snapshots, newest first."""
        statement = select(ProfileSnapshotRecord).order_by(
            ProfileSnapshotRecord.created_at.desc(), ProfileSnapshotRecord.id
        )
        if profile_id is not None:
            statement = statement.where(ProfileSnapshotRecord.profile_id == profile_id)
        if conversation_id is not None:
            statement = statement.where(ProfileSnapshotRecord.conversation_id == conversation_id)
        with self._sessions() as session:
            return list(session.scalars(statement))

    def get(self, snapshot_id: str) -> ProfileSnapshotRecord:
        with self._sessions() as session:
            record = session.get(ProfileSnapshotRecord, snapshot_id)
        if record is None:
            raise SnapshotNotFoundError(f"profile snapshot not found: {snapshot_id}")
        return record

    def delete(self, snapshot_id: str) -> None:
        with self._sessions() as session:
            record = session.get(ProfileSnapshotRecord, snapshot_id)
            if record is None:
                raise SnapshotNotFoundError(f"profile snapshot not found: {snapshot_id}")
            session.delete(record)
            session.commit()

    def restore(self, snapshot_id: str) -> ModelProfileRecord:
        """Put the profile back as the snapshot has it; the caller checks the router is stopped."""
        snapshot = self.get(snapshot_id)
        current = self._profiles.get(snapshot.profile_id)
        configuration = normalize_configuration(snapshot.configuration, self._catalog)
        profile = ModelProfile(
            alias=current.alias,
            model_path=Path(snapshot.model_path),
            arguments=configuration.arguments,
            acknowledged=configuration.acknowledged,
        )
        return self._profiles.update(
            current.id,
            profile=profile,
            runtime_id=snapshot.runtime_id,
            enabled=snapshot.enabled,
            exclusive=snapshot.exclusive,
            description=snapshot.description,
            version=snapshot.version,
        )


def snapshot_payload(record: ProfileSnapshotRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "profile_id": record.profile_id,
        "name": record.name,
        "created_by": record.created_by,
        "conversation_id": record.conversation_id,
        "runtime_id": record.runtime_id,
        "model_path": record.model_path,
        "description": record.description,
        "configuration": record.configuration,
        "enabled": record.enabled,
        "exclusive": record.exclusive,
        "version": record.version,
        "created_at": record.created_at,
    }
