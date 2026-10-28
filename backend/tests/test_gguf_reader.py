import os
import struct
from pathlib import Path

import pytest
from gguf_files import typed, write_gguf

from llamawebui.services.gguf_reader import GgufError, read_header, read_model


def test_header_reads_every_value_type(tmp_path: Path) -> None:
    path = write_gguf(
        tmp_path / "model.gguf",
        [
            ("u8", typed(0, 200)),
            ("i8", typed(1, -5)),
            ("u16", typed(2, 60000)),
            ("i16", typed(3, -300)),
            ("u32", 70000),
            ("i32", typed(5, -70000)),
            ("f32", 0.5),
            ("flag", True),
            ("name", "demo"),
            ("u64", typed(10, 2**40)),
            ("i64", typed(11, -(2**40))),
            ("f64", typed(12, 0.25)),
            ("heads", [8, 8, 0, 8]),
        ],
    )

    metadata = read_header(path).metadata

    assert metadata == {
        "u8": 200,
        "i8": -5,
        "u16": 60000,
        "i16": -300,
        "u32": 70000,
        "i32": -70000,
        "f32": 0.5,
        "flag": True,
        "name": "demo",
        "u64": 2**40,
        "i64": -(2**40),
        "f64": 0.25,
        "heads": (8, 8, 0, 8),
    }


def test_header_skips_long_values_but_keeps_array_lengths(tmp_path: Path) -> None:
    path = write_gguf(
        tmp_path / "model.gguf",
        [
            ("tokenizer.ggml.tokens", ["a", "bb", "ccc"]),
            ("tokenizer.ggml.scores", [0.0] * 5000),
            ("tokenizer.chat_template", "t" * 2000),
            ("general.name", "after"),
        ],
    )

    header = read_header(path)

    assert header.metadata == {"general.name": "after"}
    assert header.array_lengths == {"tokenizer.ggml.tokens": 3, "tokenizer.ggml.scores": 5000}


def test_tensor_sizes_come_from_offsets_and_the_end_of_the_file(tmp_path: Path) -> None:
    path = write_gguf(
        tmp_path / "model.gguf",
        [("general.architecture", "llama")],
        [("token_embd.weight", (8, 100), 1600), ("output.weight", (8, 100), 64)],
    )

    tensors = read_header(path).tensors

    assert [(tensor.name, tensor.dims, tensor.nbytes) for tensor in tensors] == [
        ("token_embd.weight", (8, 100), 1600),
        ("output.weight", (8, 100), 64),
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"GGML\x03\x00\x00\x00", "not a GGUF file"),
        (b"GGUF\x01\x00\x00\x00" + bytes(16), "unsupported GGUF version"),
        (b"GGUF\x03\x00\x00\x00" + bytes(4), "truncated"),
        (
            b"GGUF\x03\x00\x00\x00" + (0).to_bytes(8, "little") + (10**6).to_bytes(8, "little"),
            "implausible",
        ),
    ],
)
def test_header_rejects_invalid_files(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(content)

    with pytest.raises(GgufError, match=message):
        read_header(path)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (struct.pack("<I", 13), "unknown GGUF value type"),
        (struct.pack("<IIQ", 9, 13, 1), "unknown GGUF array type"),
        (struct.pack("<IIQ", 9, 9, 1), "nested GGUF arrays"),
    ],
)
def test_header_rejects_unknown_value_types(tmp_path: Path, value: bytes, message: str) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF" + struct.pack("<IQQQ", 3, 0, 1, 3) + b"bad" + value)

    with pytest.raises(GgufError, match=message):
        read_header(path)


def test_header_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(GgufError, match="cannot read"):
        read_header(tmp_path / "missing.gguf")


def test_model_collects_tensors_from_every_shard(tmp_path: Path) -> None:
    write_gguf(
        tmp_path / "model-00001-of-00002.gguf",
        [("general.architecture", "llama"), ("split.count", typed(2, 2))],
    )
    write_gguf(
        tmp_path / "model-00002-of-00002.gguf",
        [("split.count", typed(2, 2))],
        [("blk.0.attn_q.weight", (4, 4), 96)],
    )

    model = read_model(tmp_path / "model-00001-of-00002.gguf")

    assert model.architecture == "llama"
    assert len(model.files) == 2
    assert model.total_bytes == 96


def test_model_is_read_again_when_the_file_changes(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "model.gguf", [("general.architecture", "llama")])
    assert read_model(path).arch_value("block_count") is None

    write_gguf(path, [("general.architecture", "llama"), ("llama.block_count", 2), ("pad", "x")])
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert read_model(path).arch_value("block_count") == 2
