"""Write small GGUF files for tests, encoded as the GGUF specification describes."""

from __future__ import annotations

import struct
from collections.abc import Sequence
from pathlib import Path


def write_gguf(
    path: Path,
    metadata: Sequence[tuple[str, object]] = (),
    tensors: Sequence[tuple[str, Sequence[int], int]] = (),
    *,
    version: int = 3,
    alignment: int = 32,
) -> Path:
    """Write a GGUF header and zero-filled tensor data of the given byte sizes.

    Values are encoded by Python type: str as string, bool as bool, int as uint32,
    float as float32, and lists as arrays of their first item's type. Wrap a value in
    ``typed`` to force another type.
    """
    content = bytearray(_MAGIC)
    content += struct.pack("<IQQ", version, len(tensors), len(metadata))
    for key, value in metadata:
        content += _string(key)
        content += _value(value)
    offset = 0
    for name, dims, nbytes in tensors:
        content += _string(name)
        content += struct.pack("<I", len(dims))
        content += struct.pack(f"<{len(dims)}Q", *dims)
        content += struct.pack("<IQ", 0, offset)
        offset += -(-nbytes // alignment) * alignment
    content += bytes(-len(content) % alignment)
    with path.open("wb") as stream:
        stream.write(content)
        # Extending the file reads back as zeros without writing them.
        stream.truncate(len(content) + offset)
    return path


class typed:  # noqa: N801 - reads as a marker in metadata lists
    """A metadata value with an explicit GGUF type id."""

    def __init__(self, type_id: int, value: object) -> None:
        self.type_id = type_id
        self.value = value


_MAGIC = b"GGUF"
_FORMATS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
            10: "<Q", 11: "<q", 12: "<d"}


def _string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack("<Q", len(encoded)) + encoded


def _type_of(value: object) -> int:
    if isinstance(value, typed):
        return value.type_id
    if isinstance(value, str):
        return 8
    if isinstance(value, bool):
        return 7
    if isinstance(value, int):
        return 4
    if isinstance(value, float):
        return 6
    if isinstance(value, list):
        return 9
    raise TypeError(f"cannot encode {value!r}")


def _value(value: object) -> bytes:
    type_id = _type_of(value)
    return struct.pack("<I", type_id) + _payload(type_id, value)


def _payload(type_id: int, value: object) -> bytes:
    if isinstance(value, typed):
        value = value.value
    if type_id == 8:
        assert isinstance(value, str)
        return _string(value)
    if type_id == 9:
        assert isinstance(value, list)
        item_type = _type_of(value[0]) if value else 4
        return struct.pack("<IQ", item_type, len(value)) + b"".join(
            _payload(item_type, item) for item in value
        )
    return struct.pack(_FORMATS[type_id], value)
