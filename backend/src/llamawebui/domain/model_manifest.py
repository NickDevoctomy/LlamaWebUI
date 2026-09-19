"""Pure GGUF repository manifest grouping."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SHARD_PATTERN = re.compile(
    r"^(?P<base>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$", re.IGNORECASE
)
_QUANT_PATTERN = re.compile(r"(?:UD-)?(?:I?Q)\d[A-Z0-9_]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class HubFile:
    path: str
    size: int | None


@dataclass(frozen=True, slots=True)
class GgufGroup:
    key: str
    quantization: str | None
    files: tuple[HubFile, ...]
    total_size: int | None
    complete: bool


def group_gguf_files(files: tuple[HubFile, ...]) -> tuple[GgufGroup, ...]:
    grouped: dict[str, list[tuple[int, int, HubFile]]] = {}
    for file in files:
        if not file.path.lower().endswith(".gguf"):
            continue
        match = _SHARD_PATTERN.match(file.path)
        if match is None:
            key = file.path.removesuffix(".gguf")
            grouped.setdefault(key, []).append((1, 1, file))
        else:
            key = match.group("base")
            grouped.setdefault(key, []).append(
                (int(match.group("index")), int(match.group("count")), file)
            )

    results: list[GgufGroup] = []
    for key, entries in sorted(grouped.items()):
        entries.sort(key=lambda entry: entry[0])
        expected_count = entries[0][1]
        complete = (
            all(count == expected_count for _, count, _ in entries)
            and [index for index, _, _ in entries] == list(range(1, expected_count + 1))
        )
        sizes = [file.size for _, _, file in entries]
        quant_match = _QUANT_PATTERN.search(key)
        results.append(
            GgufGroup(
                key=key,
                quantization=quant_match.group(0).upper() if quant_match else None,
                files=tuple(file for _, _, file in entries),
                total_size=sum(size for size in sizes if size is not None)
                if all(size is not None for size in sizes)
                else None,
                complete=complete,
            )
        )
    return tuple(results)
