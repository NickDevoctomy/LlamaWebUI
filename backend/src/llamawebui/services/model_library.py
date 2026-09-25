"""Project validated completed downloads into usable local models."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from llamawebui.domain.download_job import DownloadState
from llamawebui.models import DownloadJobRecord
from llamawebui.services.download_registry import DownloadRegistry

_SHARD_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LibraryModel:
    download_id: str
    repo_id: str
    revision: str
    group_key: str
    primary_path: Path
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class LibraryReconcileResult:
    managed_jobs: int
    valid_models: int
    invalid_jobs: int
    stray_gguf_files: int


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    primary_path: Path
    files: tuple[Path, ...]
    total_bytes: int
    model_name: str
    metadata: dict[str, str | int]


class ModelLibrary:
    def __init__(self, downloads: DownloadRegistry, model_root: Path) -> None:
        self._downloads = downloads
        self._model_root = model_root.resolve()

    def list(self) -> tuple[LibraryModel, ...]:
        models: dict[Path, LibraryModel] = {}
        for job in self._downloads.list(include_hidden=True):
            if DownloadState(job.state) is not DownloadState.COMPLETED:
                continue
            model = self._project(job)
            if model is not None:
                models.setdefault(model.primary_path.resolve(), model)
        return tuple(models.values())

    def reconcile(self) -> LibraryReconcileResult:
        jobs = self._downloads.list(include_hidden=True)
        valid = sum(
            1
            for job in jobs
            if DownloadState(job.state) is DownloadState.COMPLETED
            and self._project(job) is not None
        )
        completed = sum(1 for job in jobs if DownloadState(job.state) is DownloadState.COMPLETED)
        referenced = {
            path.resolve()
            for job in jobs
            if DownloadState(job.state) is DownloadState.COMPLETED
            for raw in job.files
            if isinstance(raw.get("path"), str)
            for path in [Path(job.destination) / str(raw["path"])]
        }
        stray = sum(
            1
            for path in self._model_root.rglob("*.gguf")
            if path.is_file() and path.resolve() not in referenced
        )
        return LibraryReconcileResult(
            managed_jobs=len(jobs),
            valid_models=valid,
            invalid_jobs=completed - valid,
            stray_gguf_files=stray,
        )

    def discover(self) -> tuple[DiscoveredModel, ...]:
        candidates: dict[str, list[Path]] = {}
        for path in self._model_root.rglob("*.gguf"):
            if not path.is_file() or path.is_symlink():
                continue
            match = _SHARD_PATTERN.match(path.name)
            key = match.group("prefix") if match else path.stem
            candidates.setdefault(key, []).append(path)
        discovered: list[DiscoveredModel] = []
        for paths in candidates.values():
            paths.sort()
            primary = next(
                (path for path in paths if _is_primary(path.name)),
                None,
            )
            if primary is None:
                continue
            match = _SHARD_PATTERN.match(primary.name)
            if match is not None:
                count = int(match.group("count"))
                expected = {
                    primary.with_name(
                        f"{match.group('prefix')}-{index:05d}-of-{count:05d}.gguf"
                    ).resolve()
                    for index in range(1, count + 1)
                }
                if {path.resolve() for path in paths} != expected:
                    continue
            discovered.append(
                DiscoveredModel(
                    primary,
                    tuple(paths),
                    sum(path.stat().st_size for path in paths),
                    primary.parent.name or primary.stem,
                    read_gguf_metadata(primary),
                )
            )
        return tuple(discovered)

    def _project(self, job: DownloadJobRecord) -> LibraryModel | None:
        destination = Path(job.destination).resolve()
        if not destination.is_relative_to(self._model_root):
            return None

        primary: Path | None = None
        for file_data in job.files:
            raw_path = file_data.get("path")
            expected_size = file_data.get("size")
            if not isinstance(raw_path, str) or not isinstance(expected_size, int):
                return None
            relative_path = Path(raw_path)
            if relative_path.is_absolute() or any(
                part in {".", ".."} for part in relative_path.parts
            ):
                return None
            file_path = (destination / relative_path).resolve()
            if (
                not file_path.is_relative_to(destination)
                or not file_path.is_file()
                or file_path.stat().st_size != expected_size
            ):
                return None
            expected_sha256 = file_data.get("sha256")
            if (
                isinstance(expected_sha256, str)
                and _sha256_file(file_path) != expected_sha256.lower()
            ):
                return None
            if file_path.suffix.lower() != ".gguf":
                continue
            shard = _SHARD_PATTERN.match(file_path.name)
            if shard is None or shard.group("index") == "00001":
                primary = primary or file_path

        if primary is None:
            return None
        return LibraryModel(
            download_id=job.id,
            repo_id=job.repo_id,
            revision=job.revision,
            group_key=job.group_key,
            primary_path=primary,
            file_count=len(job.files),
            total_bytes=job.total_bytes,
        )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_primary(name: str) -> bool:
    match = _SHARD_PATTERN.match(name)
    return match is None or match.group("index") == "00001"


def read_gguf_metadata(path: Path) -> dict[str, str | int]:
    """Read a bounded set of scalar GGUF header metadata without loading tensors."""
    metadata: dict[str, str | int] = {}
    try:
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                return metadata
            version, _, count = struct.unpack("<IQQ", stream.read(20))
            if version not in {1, 2, 3} or count > 100_000:
                return metadata
            for _ in range(count):
                key = _read_gguf_string(stream)
                value_type = struct.unpack("<I", stream.read(4))[0]
                value = _read_gguf_scalar(stream, value_type)
                if key in {
                    "general.architecture",
                    "general.name",
                    "general.file_type",
                    "general.quantization_version",
                    "general.parameter_count",
                    "general.context_length",
                } and isinstance(value, (str, int)):
                    metadata[key] = value
    except (OSError, struct.error, UnicodeDecodeError, ValueError):
        return {}
    return metadata


def _read_gguf_string(stream: BinaryIO) -> str:
    length = struct.unpack("<Q", stream.read(8))[0]
    if length > 4096:
        raise ValueError("GGUF metadata string is too long")
    return stream.read(length).decode("utf-8")


def _read_gguf_scalar(stream: BinaryIO, value_type: int) -> str | int | None:
    if value_type == 4:
        return _read_gguf_string(stream)
    formats = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 5: "<I", 6: "<i", 7: "<?", 10: "<Q", 11: "<q"}
    fmt = formats.get(value_type)
    if fmt is None:
        raise ValueError("unsupported GGUF metadata type")
    value = struct.unpack(fmt, stream.read(struct.calcsize(fmt)))[0]
    return value if isinstance(value, (str, int)) else None