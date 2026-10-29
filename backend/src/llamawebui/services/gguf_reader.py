"""Read GGUF headers: metadata and the tensor table, without loading tensor data.

Value type ids follow the GGUF specification. Long strings, string arrays and long numeric
arrays (tokenizer vocabularies and scores) are skipped rather than kept; their lengths are
recorded in ``array_lengths``.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

_MAGIC = b"GGUF"
_SCALAR_FORMATS = {
    0: "<B",  # uint8
    1: "<b",  # int8
    2: "<H",  # uint16
    3: "<h",  # int16
    4: "<I",  # uint32
    5: "<i",  # int32
    6: "<f",  # float32
    7: "<?",  # bool
    10: "<Q",  # uint64
    11: "<q",  # int64
    12: "<d",  # float64
}
_STRING = 8
_ARRAY = 9
_MAX_KEYS = 100_000
_MAX_TENSORS = 1_000_000
_MAX_STRING_BYTES = 64 * 1024 * 1024
_MAX_KEPT_STRING = 1024
_MAX_KEPT_ARRAY = 4096
_DEFAULT_ALIGNMENT = 32
_SHARD_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$", re.IGNORECASE
)

MetadataValue = str | int | float | bool | tuple[int | float | bool, ...]


class GgufError(ValueError):
    """The file is not a readable GGUF header."""


@dataclass(frozen=True, slots=True)
class GgufTensor:
    name: str
    ggml_type: int
    dims: tuple[int, ...]
    # Bytes the tensor occupies in the file, alignment padding included.
    nbytes: int


@dataclass(frozen=True, slots=True)
class GgufHeader:
    version: int
    metadata: Mapping[str, MetadataValue]
    array_lengths: Mapping[str, int]
    tensors: tuple[GgufTensor, ...]


@dataclass(frozen=True, slots=True)
class GgufModel:
    """A model's metadata (from its first file) and the tensors of every shard."""

    files: tuple[Path, ...]
    metadata: Mapping[str, MetadataValue]
    array_lengths: Mapping[str, int]
    tensors: tuple[GgufTensor, ...]

    @property
    def architecture(self) -> str | None:
        value = self.metadata.get("general.architecture")
        return value if isinstance(value, str) else None

    @property
    def total_bytes(self) -> int:
        return sum(tensor.nbytes for tensor in self.tensors)

    def arch_value(self, key: str) -> MetadataValue | None:
        """An architecture-scoped key such as ``block_count`` (``qwen3.block_count``)."""
        architecture = self.architecture
        return None if architecture is None else self.metadata.get(f"{architecture}.{key}")


def read_header(path: Path) -> GgufHeader:
    """Parse one GGUF file's header. Raises ``GgufError`` when it is not valid GGUF."""
    try:
        size = path.stat().st_size
        with path.open("rb", buffering=1024 * 1024) as stream:
            return _read_header(stream, size)
    except (OSError, struct.error, UnicodeDecodeError, OverflowError, MemoryError) as error:
        raise GgufError(f"cannot read GGUF header of {path.name}: {error}") from error


def read_model(primary: Path) -> GgufModel:
    """Read a model and, for a split model, the tensor tables of all its shards."""
    resolved = primary.resolve()
    stat = resolved.stat()
    return _read_model_cached(str(resolved), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=32)
def _read_model_cached(path: str, size: int, mtime_ns: int) -> GgufModel:
    # Size and modification time are part of the key so a replaced file is read again.
    del size, mtime_ns
    primary = Path(path)
    header = read_header(primary)
    files = _shard_files(primary, header.metadata)
    tensors = list(header.tensors)
    for shard in files[1:]:
        tensors.extend(read_header(shard).tensors)
    return GgufModel(
        files=files,
        metadata=header.metadata,
        array_lengths=header.array_lengths,
        tensors=tuple(tensors),
    )


def _shard_files(primary: Path, metadata: Mapping[str, MetadataValue]) -> tuple[Path, ...]:
    count = metadata.get("split.count")
    match = _SHARD_PATTERN.match(primary.name)
    if not isinstance(count, int) or count <= 1 or match is None:
        return (primary,)
    return tuple(
        primary.with_name(f"{match.group('prefix')}-{index:05d}-of-{count:05d}.gguf")
        for index in range(1, count + 1)
    )


def _read_header(stream: BinaryIO, size: int) -> GgufHeader:
    if stream.read(4) != _MAGIC:
        raise GgufError("not a GGUF file")
    version = _unpack(stream, "<I")
    if version not in {2, 3}:
        raise GgufError(f"unsupported GGUF version {version}")
    tensor_count = _unpack(stream, "<Q")
    key_count = _unpack(stream, "<Q")
    if key_count > _MAX_KEYS or tensor_count > _MAX_TENSORS:
        raise GgufError("GGUF header counts are implausible")
    metadata: dict[str, MetadataValue] = {}
    array_lengths: dict[str, int] = {}
    for _ in range(key_count):
        key = _read_string(stream)
        value_type = _unpack(stream, "<I")
        if value_type == _ARRAY:
            item_type = _unpack(stream, "<I")
            length = _unpack(stream, "<Q")
            array_lengths[key] = length
            items = _read_array(stream, item_type, length)
            if items is not None:
                metadata[key] = items
            continue
        value = _read_value(stream, value_type)
        if value is not None:
            metadata[key] = value
    entries: list[tuple[str, int, tuple[int, ...], int]] = []
    for _ in range(tensor_count):
        name = _read_string(stream)
        dimension_count = _unpack(stream, "<I")
        if dimension_count > 8:
            raise GgufError(f"tensor {name} has {dimension_count} dimensions")
        dims = struct.unpack(f"<{dimension_count}Q", _read_exact(stream, 8 * dimension_count))
        ggml_type = _unpack(stream, "<I")
        offset = _unpack(stream, "<Q")
        entries.append((name, ggml_type, dims, offset))
    alignment = metadata.get("general.alignment", _DEFAULT_ALIGNMENT)
    if not isinstance(alignment, int) or alignment <= 0:
        alignment = _DEFAULT_ALIGNMENT
    data_start = -(-stream.tell() // alignment) * alignment
    data_bytes = size - data_start
    # Each tensor runs to the next one's offset; the last runs to the end of the file.
    ordered = sorted(range(len(entries)), key=lambda index: entries[index][3])
    nbytes = [0] * len(entries)
    for position, index in enumerate(ordered):
        end = entries[ordered[position + 1]][3] if position + 1 < len(ordered) else data_bytes
        nbytes[index] = max(0, end - entries[index][3])
    tensors = tuple(
        GgufTensor(name, ggml_type, dims, nbytes[index])
        for index, (name, ggml_type, dims, _) in enumerate(entries)
    )
    return GgufHeader(version, metadata, array_lengths, tensors)


def _read_value(stream: BinaryIO, value_type: int) -> str | int | float | bool | None:
    if value_type == _STRING:
        length = _unpack(stream, "<Q")
        if length > _MAX_KEPT_STRING:
            _skip(stream, length)
            return None
        return _read_exact(stream, length).decode("utf-8")
    fmt = _SCALAR_FORMATS.get(value_type)
    if fmt is None:
        raise GgufError(f"unknown GGUF value type {value_type}")
    value: int | float | bool = struct.unpack(fmt, _read_exact(stream, struct.calcsize(fmt)))[0]
    return value


def _read_array(
    stream: BinaryIO, item_type: int, length: int
) -> tuple[int | float | bool, ...] | None:
    if item_type == _STRING:
        for _ in range(length):
            _skip(stream, _unpack(stream, "<Q"))
        return None
    if item_type == _ARRAY:
        raise GgufError("nested GGUF arrays are not supported")
    fmt = _SCALAR_FORMATS.get(item_type)
    if fmt is None:
        raise GgufError(f"unknown GGUF array type {item_type}")
    width = struct.calcsize(fmt)
    if length > _MAX_KEPT_ARRAY:
        _skip(stream, width * length)
        return None
    values: tuple[int | float | bool, ...] = struct.unpack(
        f"<{length}{fmt[1]}", _read_exact(stream, width * length)
    )
    return values


def _read_string(stream: BinaryIO) -> str:
    length = _unpack(stream, "<Q")
    if length > _MAX_STRING_BYTES:
        raise GgufError("GGUF string is too long")
    return _read_exact(stream, length).decode("utf-8")


def _unpack(stream: BinaryIO, fmt: str) -> int:
    """One unsigned integer field of the header structure."""
    value: int = struct.unpack(fmt, _read_exact(stream, struct.calcsize(fmt)))[0]
    return value


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise GgufError("GGUF header is truncated")
    return data


def _skip(stream: BinaryIO, length: int) -> None:
    if length > _MAX_STRING_BYTES * 16:
        raise GgufError("GGUF header field is too long")
    if length:
        stream.seek(length, 1)
