from pathlib import Path

import pytest
from gguf_files import write_gguf

from llamawebui.domain.profile_arguments import ArgumentValue
from llamawebui.services.gguf_reader import read_model
from llamawebui.services.memory_estimate import (
    MIB,
    Devices,
    MemoryEstimate,
    estimate_memory,
    estimate_profile_memory,
)

GPU = Devices(gpu_count=1, capacity_bytes=24 * 1024 * MIB)
CPU_ONLY = Devices()
LAYER_BYTES = 4096
EXPERT_BYTES = 8192
EMBEDDING_BYTES = 64_000


def dense_model(path: Path, *, layers: int = 4, extra: tuple[tuple[str, object], ...] = ()) -> Path:
    """A llama-style model: 8 heads of 8, 2 KV heads, a 1,000-token vocabulary."""
    tensors = [
        ("token_embd.weight", (64, 1000), EMBEDDING_BYTES),
        ("output_norm.weight", (64,), 256),
        ("output.weight", (64, 1000), EMBEDDING_BYTES),
        *((f"blk.{index}.attn_q.weight", (64, 64), LAYER_BYTES) for index in range(layers)),
    ]
    return write_gguf(
        path,
        [
            ("general.architecture", "llama"),
            ("llama.block_count", layers),
            ("llama.context_length", 8192),
            ("llama.embedding_length", 64),
            ("llama.feed_forward_length", 128),
            ("llama.attention.head_count", 8),
            ("llama.attention.head_count_kv", 2),
            *extra,
        ],
        tensors,
    )


def moe_model(path: Path) -> Path:
    tensors = [
        ("token_embd.weight", (64, 1000), EMBEDDING_BYTES),
        ("output.weight", (64, 1000), EMBEDDING_BYTES),
        *(
            tensor
            for index in range(4)
            for tensor in (
                (f"blk.{index}.attn_q.weight", (64, 64), LAYER_BYTES),
                (f"blk.{index}.ffn_up_exps.weight", (64, 32, 8), EXPERT_BYTES),
                (f"blk.{index}.ffn_down_exps.weight", (32, 64, 8), EXPERT_BYTES),
            )
        ),
    ]
    return write_gguf(
        path,
        [
            ("general.architecture", "qwen3moe"),
            ("qwen3moe.block_count", 4),
            ("qwen3moe.context_length", 4096),
            ("qwen3moe.embedding_length", 64),
            ("qwen3moe.attention.head_count", 8),
            ("qwen3moe.attention.head_count_kv", 2),
            ("qwen3moe.expert_feed_forward_length", 32),
            ("qwen3moe.expert_used_count", 2),
        ],
        tensors,
    )


def hybrid_model(path: Path) -> Path:
    """Qwen3-Next style: every fourth layer attends, the rest are gated delta nets."""
    return write_gguf(
        path,
        [
            ("general.architecture", "qwen35"),
            ("qwen35.block_count", 8),
            ("qwen35.context_length", 262144),
            ("qwen35.embedding_length", 64),
            ("qwen35.feed_forward_length", 128),
            ("qwen35.attention.head_count", 8),
            ("qwen35.attention.head_count_kv", 2),
            ("qwen35.attention.key_length", 16),
            ("qwen35.attention.value_length", 16),
            ("qwen35.full_attention_interval", 4),
            ("qwen35.ssm.conv_kernel", 4),
            ("qwen35.ssm.state_size", 16),
            ("qwen35.ssm.group_count", 2),
            ("qwen35.ssm.inner_size", 128),
        ],
        [("token_embd.weight", (64, 1000), EMBEDDING_BYTES)],
    )


def estimate(
    path: Path,
    values: dict[str, ArgumentValue] | None = None,
    devices: Devices = GPU,
    **files: Path,
) -> MemoryEstimate:
    draft = read_model(files["draft"]) if "draft" in files else None
    projector = files["projector"].stat().st_size if "projector" in files else None
    return estimate_memory(
        read_model(path), values or {}, devices, draft=draft, projector_bytes=projector
    )


def part(result: MemoryEstimate, part_id: str) -> tuple[int, int]:
    found = next(item for item in (*result.parts, *result.growth) if item.id == part_id)
    return found.gpu_bytes, found.cpu_bytes


def test_all_layers_on_gpu_keep_only_input_embeddings_in_ram(tmp_path: Path) -> None:
    result = estimate(dense_model(tmp_path / "m.gguf"), {"ctx-size": 1024, "parallel": 1})

    gpu, cpu = part(result, "weights")
    assert cpu == EMBEDDING_BYTES
    assert gpu == 4 * LAYER_BYTES + EMBEDDING_BYTES + 256
    # 2 KV heads x (8 + 8) values x 2 bytes, for 1,024 tokens in each of 4 layers.
    assert part(result, "kv") == (4 * 1024 * 64, 0)
    assert result.context_size == 1024
    assert result.gpu_bytes == sum(item.gpu_bytes for item in result.parts)
    assert "All 4 layers on the GPU, output layer too" in result.parts[0].detail


def test_partial_offload_splits_layers_cache_and_output(tmp_path: Path) -> None:
    result = estimate(dense_model(tmp_path / "m.gguf"), {"ctx-size": 1024, "n-gpu-layers": 2})

    gpu, cpu = part(result, "weights")
    assert gpu == 2 * LAYER_BYTES
    assert cpu == 2 * LAYER_BYTES + 2 * EMBEDDING_BYTES + 256
    assert part(result, "kv") == (2 * 1024 * 64, 2 * 1024 * 64)
    assert "2 of 4 layers on the GPU, output layer in RAM" in result.parts[0].detail
    _, compute_cpu = part(result, "compute")
    assert compute_cpu >= 1000 * 512 * 4  # the logits are computed on the CPU


def test_kv_cache_follows_cache_types_and_offload(tmp_path: Path) -> None:
    path = dense_model(tmp_path / "m.gguf")

    quantized = estimate(path, {"ctx-size": 1024, "cache-type-k": "q8_0", "cache-type-v": "q4_0"})
    in_ram = estimate(path, {"ctx-size": 1024, "kv-offload": False})

    assert part(quantized, "kv")[0] == int(4 * 1024 * 2 * 8 * (34 / 32 + 18 / 32))
    assert part(in_ram, "kv") == (0, 4 * 1024 * 64)


def test_cpu_runtime_keeps_everything_in_ram(tmp_path: Path) -> None:
    result = estimate(dense_model(tmp_path / "m.gguf"), {"ctx-size": 1024}, CPU_ONLY)

    assert result.gpu_bytes == 0
    assert result.gpu_capacity_bytes is None
    assert result.parts[0].detail == "All weights in system RAM."
    assert not any("--fit" in note for note in result.notes)


def test_expert_offload_keeps_the_first_layers_experts_in_ram(tmp_path: Path) -> None:
    path = moe_model(tmp_path / "moe.gguf")

    some = estimate(path, {"n-cpu-moe": 3, "ctx-size": 512})
    every = estimate(path, {"cpu-moe": True, "ctx-size": 512})

    assert part(some, "weights")[1] == EMBEDDING_BYTES + 3 * 2 * EXPERT_BYTES
    assert "experts of 3 layers in RAM" in some.parts[0].detail
    assert part(every, "weights")[1] == EMBEDDING_BYTES + 4 * 2 * EXPERT_BYTES
    # Large batches copy one layer's RAM-resident experts to the GPU.
    with_copy = part(some, "compute")[0]
    without = part(estimate(path, {"ctx-size": 512}), "compute")[0]
    assert with_copy - without == 2 * EXPERT_BYTES


def test_tensor_overrides_apply_first_match_and_report_bad_patterns(tmp_path: Path) -> None:
    path = moe_model(tmp_path / "moe.gguf")

    result = estimate(
        path,
        {
            "ctx-size": 512,
            "override-tensor": ("blk\\.0\\.ffn_up_exps=CPU,token_embd=CUDA0", "blk\\.(=CPU"),
        },
    )

    gpu, cpu = part(result, "weights")
    assert cpu == EXPERT_BYTES
    assert gpu > EMBEDDING_BYTES * 2
    assert any("invalid patterns: blk\\.(" in note for note in result.notes)


def test_hybrid_models_cache_only_attention_layers_and_keep_recurrent_state(
    tmp_path: Path,
) -> None:
    result = estimate(hybrid_model(tmp_path / "h.gguf"), {"ctx-size": 4096, "parallel": 2})

    # Layers 3 and 7 attend: 2 heads x (16 + 16) values x 2 bytes per token.
    assert part(result, "kv") == (2 * 4096 * 128, 0)
    # Six recurrent layers: (4 - 1) x (128 + 2 x 2 x 16) conv values and 16 x 128 state
    # values, as float32, for two slots.
    per_layer = ((4 - 1) * (128 + 2 * 2 * 16) + 16 * 128) * 4 * 2
    assert part(result, "recurrent") == (6 * per_layer, 0)
    assert "6 linear-attention layers, for 2 slots" in result.parts[2].detail
    checkpoints = part(result, "checkpoints")[1]
    assert checkpoints == 32 * 2 * (6 * per_layer // 2)


def test_rwkv_recurrent_state_uses_the_head_size(tmp_path: Path) -> None:
    path = write_gguf(
        tmp_path / "rwkv.gguf",
        [
            ("general.architecture", "rwkv6"),
            ("rwkv6.block_count", 2),
            ("rwkv6.context_length", 4096),
            ("rwkv6.embedding_length", 64),
            ("rwkv6.wkv.head_size", 16),
        ],
    )

    result = estimate(path, {"parallel": 1, "ctx-checkpoints": 0})

    assert part(result, "recurrent")[0] == 2 * (2 * 64 + 64 * 16) * 4
    assert part(result, "kv") == (0, 0)
    assert not any(item.id == "checkpoints" for item in result.growth)


def test_sliding_window_layers_keep_only_the_window(tmp_path: Path) -> None:
    path = dense_model(
        tmp_path / "g.gguf",
        layers=6,
        extra=(("llama.attention.sliding_window", 512),),
    )
    gemma = write_gguf(
        tmp_path / "gemma.gguf",
        [
            ("general.architecture", "gemma3"),
            ("gemma3.block_count", 6),
            ("gemma3.context_length", 32768),
            ("gemma3.embedding_length", 64),
            ("gemma3.attention.head_count", 8),
            ("gemma3.attention.head_count_kv", 2),
            ("gemma3.attention.sliding_window", 512),
        ],
    )
    patterned = dense_model(
        tmp_path / "p.gguf",
        layers=2,
        extra=(
            ("llama.attention.sliding_window", 512),
            ("llama.attention.sliding_window_pattern", [True, False]),
        ),
    )

    values: dict[str, ArgumentValue] = {"ctx-size": 8192, "parallel": 1}
    result = estimate(gemma, values)
    unknown = estimate(path, values)
    full = estimate(gemma, {**values, "swa-full": True})

    # Five window layers keep 512 + 512 tokens; the sixth keeps all 8,192.
    assert part(result, "kv")[0] == (5 * 1024 + 8192) * 64
    assert part(full, "kv")[0] == 6 * 8192 * 64
    assert part(unknown, "kv")[0] == 6 * 8192 * 64
    assert any("layer pattern is unknown" in note for note in unknown.notes)
    assert part(estimate(patterned, values), "kv")[0] == (1024 + 8192) * 64
    assert part(result, "checkpoints")[1] == 32 * 5 * 1024 * 64


def test_unset_context_uses_the_model_and_warns_about_fit(tmp_path: Path) -> None:
    result = estimate(dense_model(tmp_path / "m.gguf"))

    assert result.context_size == 8192
    assert result.slots == 4
    assert "the model's own context" in result.parts[1].detail
    assert any("--n-gpu-layers and --ctx-size" in note for note in result.notes)


def test_fit_notes_compare_against_gpu_memory(tmp_path: Path) -> None:
    path = dense_model(tmp_path / "m.gguf")
    tiny = Devices(gpu_count=1, capacity_bytes=1024 * MIB)

    fitted = estimate(path, {"ctx-size": 0}, tiny)
    unfitted = estimate(
        path, {"ctx-size": 8192, "n-gpu-layers": 99, "fit": "off", "flash-attn": "off"},
        Devices(gpu_count=1, capacity_bytes=1),
    )
    fit_target = estimate(path, {"fit-target": "4096"}, tiny)

    assert any("does not fit in GPU memory" in note for note in fitted.notes)
    assert any("spills into system RAM" in note for note in unfitted.notes)
    assert any("4,096 MiB free" in note for note in fit_target.notes)
    assert "attention matrix" in unfitted.parts[-1].detail


def test_draft_projector_and_mtp_parts(tmp_path: Path) -> None:
    main = dense_model(tmp_path / "main.gguf")
    draft = dense_model(tmp_path / "draft.gguf", layers=2)
    projector = tmp_path / "mmproj.gguf"
    projector.write_bytes(bytes(1000))

    result = estimate(
        main,
        {"ctx-size": 1024, "spec-draft-ngl": 0, "mmproj-offload": False},
        draft=draft,
        projector=projector,
    )
    mtp = estimate(main, {"spec-type": ("draft-mtp",)})

    draft_gpu, draft_cpu = part(result, "draft")
    assert draft_gpu == 0
    assert draft_cpu >= 2 * LAYER_BYTES + 2 * 1024 * 64
    assert part(result, "projector") == (0, 1000)
    assert any("Multi-token prediction" in note for note in mtp.notes)


def test_prompt_cache_growth_follows_cache_ram(tmp_path: Path) -> None:
    path = dense_model(tmp_path / "m.gguf")

    assert part(estimate(path), "prompt-cache") == (0, 8192 * MIB)
    assert not any(item.id == "prompt-cache" for item in estimate(path, {"cache-ram": 0}).growth)
    unlimited = estimate(path, {"cache-ram": -1})
    assert "No limit" in next(item for item in unlimited.growth if item.id == "prompt-cache").detail


def test_unmodelled_attention_compression_is_flagged(tmp_path: Path) -> None:
    path = dense_model(tmp_path / "m.gguf", extra=(("llama.attention.compress_ratios", [0, 4]),))

    assert any("compresses and indexes" in note for note in estimate(path).notes)


def test_payload_carries_totals_parts_and_growth(tmp_path: Path) -> None:
    payload = estimate(dense_model(tmp_path / "m.gguf"), {"ctx-size": 1024}).to_payload()

    assert payload["gpu_capacity_bytes"] == 24 * 1024 * MIB
    assert [item["id"] for item in payload["parts"]] == ["weights", "kv", "compute"]  # type: ignore[union-attr]
    assert payload["parts"][2]["approximate"] is True  # type: ignore[index]
    assert payload["growth"][0]["id"] == "prompt-cache"  # type: ignore[index]


def test_profile_estimate_reads_files_and_gives_up_on_unreadable_ones(tmp_path: Path) -> None:
    main = dense_model(tmp_path / "main.gguf")
    draft = dense_model(tmp_path / "draft.gguf", layers=2)
    (tmp_path / "mmproj.gguf").write_bytes(bytes(10))
    not_a_model = write_gguf(tmp_path / "vocab.gguf", [("general.architecture", "llama")])

    found = estimate_profile_memory(
        main,
        {"spec-draft-model": str(draft), "mmproj": str(tmp_path / "mmproj.gguf")},
        GPU,
    )

    assert found is not None
    assert [item.id for item in found.parts][-2:] == ["draft", "projector"]
    assert estimate_profile_memory(tmp_path / "missing.gguf", {}, GPU) is None
    assert estimate_profile_memory(main, {"spec-draft-model": "missing.gguf"}, GPU) is None
    assert estimate_profile_memory(not_a_model, {}, GPU) is None


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (
            [
                "Available devices:",
                "  CUDA0: NVIDIA GeForce RTX 4090 (24563 MiB, 23036 MiB free)",
                "  CUDA1: NVIDIA GeForce RTX 3090 (24576 MiB, 24000 MiB free)",
            ],
            Devices(2, (24563 + 24576) * MIB),
        ),
        (["Available devices:"], Devices(0, None)),
        (["  CPU: AMD Ryzen (65536 MiB, 60000 MiB free)"], Devices(0, None)),
    ],
)
def test_devices_come_from_the_runtime_listing(lines: list[str], expected: Devices) -> None:
    assert Devices.from_listing(lines) == expected
