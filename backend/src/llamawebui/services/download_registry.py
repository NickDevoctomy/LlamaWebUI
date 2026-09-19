"""Create and persist safe, revision-pinned download jobs."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.domain.download_job import DownloadState, require_transition
from llamawebui.models import DownloadJobRecord
from llamawebui.services.huggingface_catalog import RepositoryManifest

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


class DownloadPlanError(ValueError):
    pass


class DownloadJobNotFoundError(LookupError):
    pass


class DownloadRegistry:
    def __init__(self, engine: Engine, model_root: Path) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._model_root = model_root.resolve()

    def list(self) -> list[DownloadJobRecord]:
        with self._sessions() as session:
            statement = select(DownloadJobRecord).order_by(DownloadJobRecord.created_at)
            return list(session.scalars(statement))

    def create(self, manifest: RepositoryManifest, group_key: str) -> DownloadJobRecord:
        repo_parts = manifest.repo_id.split("/")
        if (
            not _REPO_PATTERN.fullmatch(manifest.repo_id)
            or any(part in {".", ".."} for part in repo_parts)
        ):
            raise DownloadPlanError(f"unsafe repository ID: {manifest.repo_id}")
        if not _REVISION_PATTERN.fullmatch(manifest.revision):
            raise DownloadPlanError("repository revision must be a full commit SHA")

        group = next((item for item in manifest.groups if item.key == group_key), None)
        if group is None:
            raise DownloadPlanError(f"GGUF group not found: {group_key}")
        if not group.complete:
            raise DownloadPlanError(f"GGUF group is incomplete: {group_key}")
        if group.total_size is None:
            raise DownloadPlanError(f"GGUF group size is unknown: {group_key}")

        destination = (self._model_root / manifest.repo_id / manifest.revision).resolve()
        if not destination.is_relative_to(self._model_root):
            raise DownloadPlanError("download destination escapes the model root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(destination.parent).free < group.total_size:
            raise DownloadPlanError(
                f"insufficient disk space: requires {group.total_size} bytes"
            )

        record = DownloadJobRecord(
            id=str(uuid4()),
            repo_id=manifest.repo_id,
            revision=manifest.revision.lower(),
            group_key=group.key,
            files=[{"path": file.path, "size": file.size} for file in group.files],
            destination=str(destination),
            total_bytes=group.total_size,
            completed_bytes=0,
            state=DownloadState.QUEUED,
            error=None,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
        return record

    def transition(self, job_id: str, target: DownloadState) -> DownloadJobRecord:
        with self._sessions() as session:
            record = session.get(DownloadJobRecord, job_id)
            if record is None:
                raise DownloadJobNotFoundError(f"download job not found: {job_id}")
            require_transition(DownloadState(record.state), target)
            record.state = target
            session.commit()
            return record