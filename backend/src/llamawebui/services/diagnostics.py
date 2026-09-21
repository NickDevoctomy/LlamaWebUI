"""Bounded, redacted diagnostics bundle creation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text

from llamawebui.config import Settings
from llamawebui.domain.model_profile import ProfileValidationError, validate_profile
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities
from llamawebui.models import ModelProfileRecord
from llamawebui.services.logging_utils import recent_logs, redact_text
from llamawebui.services.model_artifact_registry import ModelArtifactRegistry
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.runtime_registry import RuntimeRegistry

_MAX_RECORDS = 100
_EXPECTED_TABLES = (
    "access_tokens",
    "download_jobs",
    "logical_model_profiles",
    "logical_models",
    "model_profiles",
    "runtimes",
    "server_runs",
    "settings",
)


def _safe_text(value: object) -> str | None:
    return redact_text(str(value)) if value is not None else None


def _path_summary(value: str) -> dict[str, object]:
    path = Path(value)
    return {
        "name": path.name,
        "exists": path.exists(),
        "sha256": hashlib.sha256(str(path).encode()).hexdigest()[:16],
    }


class DiagnosticsExporter:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self._settings = settings
        self._engine = engine

    def export(
        self,
        *,
        runtimes: RuntimeRegistry,
        profiles: ProfileRegistry,
        artifacts: ModelArtifactRegistry,
        supervisor: Any,
        server_runs: Any,
        downloads: Any,
    ) -> Path:
        directory = self._settings.data_dir / "diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        payload = self._payload(runtimes, profiles, artifacts, supervisor, server_runs, downloads)
        destination = directory / "diagnostics-latest.json"
        fd, temporary_name = tempfile.mkstemp(prefix=".diagnostics-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return destination

    def _payload(
        self, runtimes: RuntimeRegistry, profiles: ProfileRegistry,
        artifacts: ModelArtifactRegistry, supervisor: Any, server_runs: Any, downloads: Any,
    ) -> dict[str, object]:
        runtime_records = runtimes.list()[:_MAX_RECORDS]
        profile_records = profiles.list()[:_MAX_RECORDS]
        return {
            "format": 1,
            "application": {
                "name": "LlamaWebUI",
                "data_dir": _path_summary(str(self._settings.data_dir)),
                "host": self._settings.host,
                "port": self._settings.port,
                "router_host": self._settings.router_host,
                "router_port": self._settings.router_port,
                "log_level": self._settings.log_level,
                "hugging_face_token_configured": self._settings.hf_token is not None,
            },
            "database": self._database_info(),
            "runtimes": [self._runtime(record) for record in runtime_records],
            "profiles": [
                self._profile(record, runtime_records, artifacts) for record in profile_records
            ],
            "server": {
                "state": getattr(supervisor.state, "value", supervisor.state),
                "last_exit_code": supervisor.last_exit_code,
                "logs": [redact_text(line) for line in tuple(supervisor.logs)[-_MAX_RECORDS:]],
                "timing": dict(supervisor.timing),
            },
            "server_runs": [self._server_run(run) for run in server_runs.list()[:_MAX_RECORDS]],
            "downloads": [
                self._download(job) for job in downloads.list(include_hidden=True)[-_MAX_RECORDS:]
            ],
            "logs": list(recent_logs()),
        }

    @staticmethod
    def _runtime(record: Any) -> dict[str, object]:
        return {
            "id": record.id,
            "name": _safe_text(record.name),
            "executable": _path_summary(record.executable_path),
            "build": record.build, "commit": record.commit, "backend": record.backend,
            "devices": list(record.devices)[:50], "option_count": len(record.options),
            "help_sha256": record.help_sha256, "probe_error": _safe_text(record.probe_error),
        }

    def _profile(
        self, record: ModelProfileRecord, runtimes: list[Any], artifacts: ModelArtifactRegistry
    ) -> dict[str, object]:
        runtime = next((item for item in runtimes if item.id == record.runtime_id), None)
        errors: tuple[str, ...]
        if runtime is None:
            errors = (f"runtime not found: {record.runtime_id}",)
        else:
            try:
                configuration = dict(record.configuration)
                configuration.update({"alias": record.alias, "runtime_id": record.runtime_id})
                from llamawebui.app import ProfileCreateRequest
                profile = ProfileCreateRequest.model_validate(configuration).to_domain()
                errors = validate_profile(
                    profile,
                    RuntimeCapabilities(options=frozenset(runtime.options), raw_help=""),
                )
            except (ValueError, ProfileValidationError) as error:
                errors = (str(error),)
        return {
            "id": record.id, "alias": _safe_text(record.alias), "runtime_id": record.runtime_id,
            "enabled": record.enabled, "model": _path_summary(record.model_path),
            "available": artifacts.profile_available(record),
            "validation_errors": [_safe_text(error) for error in errors],
        }

    @staticmethod
    def _server_run(run: Any) -> dict[str, object]:
        return {
            "id": run.id,
            "runtime_id": run.runtime_id,
            "endpoint": run.endpoint,
            "state": run.state,
            "pid": run.pid,
            "exit_code": run.exit_code,
            "error": _safe_text(run.error),
            "started_at": run.started_at,
            "ended_at": run.ended_at,
        }

    @staticmethod
    def _download(job: Any) -> dict[str, object]:
        return {
            "id": job.id,
            "repo_id": job.repo_id,
            "revision": job.revision,
            "group_key": job.group_key,
            "file_count": len(job.files),
            "total_bytes": job.total_bytes,
            "completed_bytes": job.completed_bytes,
            "state": job.state,
            "error": _safe_text(job.error),
        }

    def _database_info(self) -> dict[str, object]:
        with self._engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        tables = sorted(inspect(self._engine).get_table_names())
        return {
            "path": _path_summary(str(self._settings.database_path)),
            "schema_revision": revision,
            "tables": tables,
            "expected_tables": list(_EXPECTED_TABLES),
            "missing_tables": sorted(set(_EXPECTED_TABLES) - set(tables)),
        }