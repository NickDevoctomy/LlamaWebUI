"""Estimate the memory a profile's model takes once llama-server has loaded it.

The figures follow how llama.cpp places a model: weights come from the GGUF tensor table
and are split between the GPU and system RAM the way ``--n-gpu-layers``, ``--n-cpu-moe``
and ``--override-tensor`` place them; the KV cache and recurrent state follow from the
architecture's metadata, the context size and the cache types. Compute buffers depend on
the backend and are only approximated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from llamawebui.domain.profile_arguments import ArgumentValue
from llamawebui.services.gguf_reader import GgufError, GgufModel, GgufTensor, read_model

MIB = 1024 * 1024

# Bytes per element of each KV cache type: block size over elements per block.
_CACHE_TYPE_BYTES = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34 / 32,
    "q4_0": 18 / 32,
    "q4_1": 20 / 32,
    "iq4_nl": 18 / 32,
    "q5_0": 22 / 32,
    "q5_1": 24 / 32,
}
# Architectures whose sliding-window layers llama.cpp sets by a fixed pattern: every
# n-th layer attends globally and the rest use the window.
_SWA_PATTERNS = {"gemma2": 2, "gemma3": 6, "cohere2": 4, "gpt-oss": 2}
# Metadata llama.cpp uses to shrink the cache in ways this estimate does not model.
_UNMODELLED_CACHE_KEYS = ("attention.compress_ratios", "attention.indexer.head_count")
_EXPERT_TENSOR = re.compile(r"\.ffn_(up|down|gate|gate_up)_(ch|)exps")
_LAYER_TENSOR = re.compile(r"^blk\.(\d+)\.")
_DEVICE_MEMORY = re.compile(r"^\s*([^:\s]+):.*\((\d+) MiB,\s*\d+ MiB free\)")
# llama-server's defaults when an option is not set.
_DEFAULT_UBATCH = 512
_DEFAULT_BATCH = 2048
_DEFAULT_AUTO_SLOTS = 4
_DEFAULT_CHECKPOINTS = 32
_DEFAULT_CACHE_RAM_MIB = 8192
_DEFAULT_FIT_TARGET_MIB = 1024
_PAD = 256


@dataclass(frozen=True, slots=True)
class MemoryPart:
    id: str
    label: str
    gpu_bytes: int
    cpu_bytes: int
    detail: str
    # True when the figure is a rule of thumb rather than computed from the model.
    approximate: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "gpu_bytes": self.gpu_bytes,
            "cpu_bytes": self.cpu_bytes,
            "detail": self.detail,
            "approximate": self.approximate,
        }


@dataclass(frozen=True, slots=True)
class MemoryEstimate:
    parts: tuple[MemoryPart, ...]
    # System RAM that fills up while the server runs, not counted in the totals.
    growth: tuple[MemoryPart, ...]
    notes: tuple[str, ...]
    context_size: int
    slots: int
    gpu_capacity_bytes: int | None

    @property
    def gpu_bytes(self) -> int:
        return sum(part.gpu_bytes for part in self.parts)

    @property
    def cpu_bytes(self) -> int:
        return sum(part.cpu_bytes for part in self.parts)

    def to_payload(self) -> dict[str, object]:
        return {
            "gpu_bytes": self.gpu_bytes,
            "cpu_bytes": self.cpu_bytes,
            "gpu_capacity_bytes": self.gpu_capacity_bytes,
            "context_size": self.context_size,
            "slots": self.slots,
            "parts": [part.to_payload() for part in self.parts],
            "growth": [part.to_payload() for part in self.growth],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class Devices:
    """The GPUs a runtime reported, from ``llama-server --list-devices``."""

    gpu_count: int = 0
    capacity_bytes: int | None = None

    @classmethod
    def from_listing(cls, lines: Iterable[str]) -> Devices:
        matches = (_DEVICE_MEMORY.match(line) for line in lines)
        sizes = [
            int(match.group(2)) * MIB
            for match in matches
            if match and not match.group(1).upper().startswith("CPU")
        ]
        return cls(len(sizes), sum(sizes) or None)


def estimate_profile_memory(
    model_path: Path, values: Mapping[str, ArgumentValue], devices: Devices
) -> MemoryEstimate | None:
    """Estimate a profile from its model file and catalog argument values, keyed by id.

    Returns None when the model (or a file it names, such as a draft) cannot be read.
    """
    try:
        model = read_model(model_path)
        draft_path = _text(values.get("spec-draft-model"))
        draft = read_model(Path(draft_path)) if draft_path else None
        projector_path = _text(values.get("mmproj"))
        projector = Path(projector_path).stat().st_size if projector_path else None
    except (OSError, GgufError):
        return None
    if model.arch_value("block_count") is None:
        return None
    return estimate_memory(model, values, devices, draft=draft, projector_bytes=projector)


def estimate_memory(
    model: GgufModel,
    values: Mapping[str, ArgumentValue],
    devices: Devices,
    *,
    draft: GgufModel | None = None,
    projector_bytes: int | None = None,
) -> MemoryEstimate:
    settings = _Settings.from_values(values, model, gpu=devices.gpu_count > 0)
    notes: list[str] = []
    main = _ModelEstimate.build(model, settings, _Placement.main(values, settings.gpu))
    parts = [main.weights, main.kv, *([main.recurrent] if main.recurrent else []), main.compute]
    if draft is not None and draft.arch_value("block_count") is not None:
        draft_settings = settings.for_draft(values, draft)
        estimate = _ModelEstimate.build(
            draft, draft_settings, _Placement.draft(values, settings.gpu)
        )
        parts.append(estimate.as_draft())
    elif any("mtp" in item for item in _items(values.get("spec-type"))):
        notes.append(
            "Multi-token prediction uses the model's own extra layers; the small extra "
            "context it needs is not included."
        )
    if projector_bytes is not None:
        offload = settings.gpu and values.get("mmproj-offload") is not False
        parts.append(
            MemoryPart(
                "projector",
                "Multimodal projector",
                projector_bytes if offload else 0,
                0 if offload else projector_bytes,
                "Projector weights; its compute buffer is not included.",
            )
        )
    growth = _growth(values, settings, main)
    notes.extend(main.notes)
    notes.extend(_fit_notes(values, settings, devices, sum(part.gpu_bytes for part in parts)))
    return MemoryEstimate(
        parts=tuple(parts),
        growth=tuple(growth),
        notes=tuple(dict.fromkeys(notes)),
        context_size=settings.context,
        slots=settings.slots,
        gpu_capacity_bytes=devices.capacity_bytes if settings.gpu else None,
    )


@dataclass(frozen=True, slots=True)
class _Settings:
    gpu: bool
    context: int
    context_from_model: bool
    slots: int
    ubatch: int
    cache_k: str
    cache_v: str
    flash_attention: bool
    kv_offload: bool
    swa_full: bool

    @classmethod
    def from_values(
        cls, values: Mapping[str, ArgumentValue], model: GgufModel, *, gpu: bool
    ) -> _Settings:
        trained = _int(model.arch_value("context_length")) or 4096
        requested = _int(values.get("ctx-size"))
        context = requested if requested else trained
        parallel = _int(values.get("parallel"))
        slots = parallel if parallel is not None and parallel > 0 else _DEFAULT_AUTO_SLOTS
        batch = _int(values.get("batch-size")) or _DEFAULT_BATCH
        ubatch = min(_int(values.get("ubatch-size")) or _DEFAULT_UBATCH, batch, context)
        return cls(
            gpu=gpu,
            context=context,
            context_from_model=not requested,
            slots=slots,
            ubatch=ubatch,
            cache_k=_cache_type(values.get("cache-type-k")),
            cache_v=_cache_type(values.get("cache-type-v")),
            flash_attention=values.get("flash-attn") != "off",
            kv_offload=values.get("kv-offload") is not False,
            swa_full=values.get("swa-full") is True,
        )

    def for_draft(self, values: Mapping[str, ArgumentValue], draft: GgufModel) -> _Settings:
        return _Settings(
            gpu=self.gpu,
            context=self.context,
            context_from_model=self.context_from_model,
            slots=self.slots,
            ubatch=self.ubatch,
            cache_k=_cache_type(values.get("spec-draft-cache-type-k")),
            cache_v=_cache_type(values.get("spec-draft-cache-type-v")),
            flash_attention=self.flash_attention,
            kv_offload=self.kv_offload,
            swa_full=self.swa_full,
        )


@dataclass(frozen=True, slots=True)
class _Placement:
    """Where llama.cpp puts each tensor, from the offload options of one model."""

    gpu: bool
    gpu_layers: int | None  # None: every layer (auto or all)
    cpu_moe_layers: int | None  # None: no experts forced to RAM; -1: all of them
    overrides: tuple[tuple[str, bool], ...] = ()  # (pattern, on GPU)
    invalid_overrides: tuple[str, ...] = ()

    @classmethod
    def main(cls, values: Mapping[str, ArgumentValue], gpu: bool) -> _Placement:
        overrides, invalid = _overrides(values.get("override-tensor"))
        return cls(
            gpu=gpu,
            gpu_layers=_layers(values.get("n-gpu-layers")),
            cpu_moe_layers=-1 if values.get("cpu-moe") is True else _int(values.get("n-cpu-moe")),
            overrides=overrides,
            invalid_overrides=invalid,
        )

    @classmethod
    def draft(cls, values: Mapping[str, ArgumentValue], gpu: bool) -> _Placement:
        return cls(
            gpu=gpu,
            gpu_layers=_layers(values.get("spec-draft-ngl")),
            cpu_moe_layers=(
                -1
                if values.get("spec-draft-cpu-moe") is True
                else _int(values.get("spec-draft-n-cpu-moe"))
            ),
        )

    def layer_on_gpu(self, layer: int, layer_count: int) -> bool:
        """Repeating layers offload from the last one down; the output layer counts last."""
        if not self.gpu:
            return False
        if self.gpu_layers is None:
            return True
        first = max(layer_count - self.gpu_layers, 0)
        if layer >= layer_count:
            return self.gpu_layers > layer_count
        return layer >= first

    def tensor_on_gpu(self, tensor: GgufTensor, layer_count: int) -> bool:
        if not self.gpu:
            return False
        for pattern, on_gpu in self.overrides:
            if re.search(pattern, tensor.name):
                return on_gpu
        layer = _tensor_layer(tensor.name)
        experts = self.cpu_moe_layers
        if (
            experts is not None
            and layer is not None
            and (experts < 0 or layer < experts)
            and _EXPERT_TENSOR.search(tensor.name)
        ):
            return False
        if layer is not None:
            return self.layer_on_gpu(layer, layer_count)
        if tensor.name.startswith("output"):
            return self.layer_on_gpu(layer_count, layer_count)
        # Input embeddings and other tensors outside the layers stay in system RAM.
        return False


@dataclass(frozen=True, slots=True)
class _ModelEstimate:
    weights: MemoryPart
    kv: MemoryPart
    recurrent: MemoryPart | None
    compute: MemoryPart
    # Per-slot sizes of the state a context checkpoint copies.
    checkpoint_bytes: int
    notes: tuple[str, ...]

    @classmethod
    def build(cls, model: GgufModel, settings: _Settings, placement: _Placement) -> _ModelEstimate:
        layer_count = _int(model.arch_value("block_count")) or 0
        notes: list[str] = []
        if placement.invalid_overrides:
            notes.append(
                "Ignored tensor overrides with invalid patterns: "
                + ", ".join(placement.invalid_overrides)
            )
        weights, largest_cpu_layer = _weights(model, placement, layer_count)
        layers = _layer_kinds(model, layer_count)
        kv, window_bytes = _kv_cache(model, settings, placement, layers, layer_count)
        recurrent = _recurrent_state(model, settings, placement, layers, layer_count)
        compute = _compute(model, settings, placement, layer_count, largest_cpu_layer)
        if any(model.arch_value(key) is not None for key in _UNMODELLED_CACHE_KEYS):
            notes.append(
                "This architecture compresses and indexes its attention cache in ways the "
                "estimate does not model, so the KV cache and compute figures are less "
                "certain than usual."
            )
        if (
            _int(model.arch_value("attention.sliding_window"))
            and model.architecture not in _SWA_PATTERNS
            and not isinstance(model.arch_value("attention.sliding_window_pattern"), tuple)
        ):
            notes.append(
                "The model uses sliding-window attention but its layer pattern is unknown, "
                "so every layer's cache is counted at full size."
            )
        # A checkpoint copies one slot's state that cannot be rewound: the recurrent state
        # and the sliding-window cache.
        recurrent_bytes = recurrent.gpu_bytes + recurrent.cpu_bytes if recurrent else 0
        checkpoint = (recurrent_bytes + window_bytes) // settings.slots
        return cls(weights, kv, recurrent, compute, checkpoint, tuple(notes))

    def as_draft(self) -> MemoryPart:
        parts = [self.weights, self.kv, *([self.recurrent] if self.recurrent else []), self.compute]
        return MemoryPart(
            "draft",
            "Draft model",
            sum(part.gpu_bytes for part in parts),
            sum(part.cpu_bytes for part in parts),
            "; ".join(
                f"{part.label} {_format_bytes(part.gpu_bytes + part.cpu_bytes)}" for part in parts
            )
            + ".",
            approximate=True,
        )


def _weights(
    model: GgufModel, placement: _Placement, layer_count: int
) -> tuple[MemoryPart, int]:
    """The weights part, and the most RAM-resident weight bytes any one layer has."""
    gpu = cpu = 0
    cpu_by_layer: dict[int, int] = {}
    cpu_experts: set[int] = set()
    output_on_gpu = False
    for tensor in model.tensors:
        layer = _tensor_layer(tensor.name)
        if placement.tensor_on_gpu(tensor, layer_count):
            gpu += tensor.nbytes
            if layer is None and tensor.name.startswith("output"):
                output_on_gpu = True
        else:
            cpu += tensor.nbytes
            if layer is not None:
                cpu_by_layer[layer] = cpu_by_layer.get(layer, 0) + tensor.nbytes
                if _EXPERT_TENSOR.search(tensor.name):
                    cpu_experts.add(layer)
    if not placement.gpu:
        detail = "All weights in system RAM."
    else:
        offloaded = sum(
            1 for layer in range(layer_count) if placement.layer_on_gpu(layer, layer_count)
        )
        detail = (
            f"All {layer_count} layers on the GPU"
            if offloaded == layer_count
            else f"{offloaded} of {layer_count} layers on the GPU"
        )
        detail += ", output layer too" if output_on_gpu else ", output layer in RAM"
        if cpu_experts and len(cpu_experts) < offloaded + 1:
            detail += f"; experts of {len(cpu_experts)} layers in RAM"
        detail += "; input embeddings in RAM."
    largest_cpu_layer = max(cpu_by_layer.values(), default=0)
    return MemoryPart("weights", "Model weights", gpu, cpu, detail), largest_cpu_layer


@dataclass(frozen=True, slots=True)
class _Layer:
    attention: bool
    recurrent: bool
    sliding: bool
    kv_heads: int


def _layer_kinds(model: GgufModel, layer_count: int) -> list[_Layer]:
    head_count_kv = model.arch_value("attention.head_count_kv")
    head_count = model.arch_value("attention.head_count")
    interval = _int(model.arch_value("full_attention_interval"))
    recurrent_model = (
        model.arch_value("ssm.state_size") is not None
        or model.arch_value("wkv.head_size") is not None
    )
    window = _int(model.arch_value("attention.sliding_window"))
    pattern = model.arch_value("attention.sliding_window_pattern")
    swa_every = _SWA_PATTERNS.get(model.architecture or "")
    layers: list[_Layer] = []
    for index in range(layer_count):
        heads = _per_layer(head_count_kv, index)
        if heads is None:
            heads = _per_layer(head_count, index) or 0
        if recurrent_model:
            if interval:
                attention = (index + 1) % interval == 0
            elif isinstance(head_count_kv, tuple):
                attention = heads > 0
            else:
                attention = False
        else:
            attention = heads > 0
        sliding = False
        if attention and window:
            if isinstance(pattern, tuple) and index < len(pattern):
                sliding = bool(pattern[index])
            elif swa_every:
                sliding = index % swa_every < swa_every - 1
        layers.append(_Layer(attention, recurrent_model and not attention, sliding, heads))
    return layers


def _kv_bytes_per_token(model: GgufModel, settings: _Settings, heads: int) -> float:
    """Cache bytes one token takes in an attention layer with this many KV heads."""
    embedding = _int(model.arch_value("embedding_length")) or 0
    head_count = _per_layer(model.arch_value("attention.head_count"), 0) or 1
    key = _int(model.arch_value("attention.key_length")) or embedding // head_count
    value = _int(model.arch_value("attention.value_length")) or key
    key_bytes = key * _CACHE_TYPE_BYTES[settings.cache_k]
    return heads * (key_bytes + value * _CACHE_TYPE_BYTES[settings.cache_v])


def _kv_cache(
    model: GgufModel,
    settings: _Settings,
    placement: _Placement,
    layers: Sequence[_Layer],
    layer_count: int,
) -> tuple[MemoryPart, int]:
    """The KV cache part, and the bytes of it that sliding-window layers hold."""
    full_tokens = _pad(settings.context)
    window = _int(model.arch_value("attention.sliding_window")) or 0
    swa_tokens = (
        full_tokens
        if settings.swa_full
        else min(full_tokens, _pad(window * settings.slots + settings.ubatch))
    )
    gpu = cpu = window_bytes = 0.0
    attention_layers = swa_layers = 0
    for index, layer in enumerate(layers):
        if not layer.attention:
            continue
        attention_layers += 1
        tokens = swa_tokens if layer.sliding else full_tokens
        size = tokens * _kv_bytes_per_token(model, settings, layer.kv_heads)
        if layer.sliding:
            swa_layers += 1
            window_bytes += size
        if settings.kv_offload and placement.layer_on_gpu(index, layer_count):
            gpu += size
        else:
            cpu += size
    source = " (the model's own context, since none is set)" if settings.context_from_model else ""
    detail = (
        f"{settings.context:,} tokens{source} across {attention_layers} attention layers, "
        f"{settings.cache_k}/{settings.cache_v} cache"
    )
    if swa_layers:
        detail += f"; {swa_layers} sliding-window layers keep {swa_tokens:,} tokens"
    if attention_layers < len(layers):
        detail += f"; the other {len(layers) - attention_layers} layers keep no KV cache"
    part = MemoryPart("kv", "KV cache", int(gpu), int(cpu), detail + ".")
    return part, int(window_bytes)


def _recurrent_state(
    model: GgufModel,
    settings: _Settings,
    placement: _Placement,
    layers: Sequence[_Layer],
    layer_count: int,
) -> MemoryPart | None:
    count = sum(1 for layer in layers if layer.recurrent)
    if not count:
        return None
    embedding = _int(model.arch_value("embedding_length")) or 0
    head_size = _int(model.arch_value("wkv.head_size"))
    if head_size is not None:
        shift = _int(model.arch_value("token_shift_count")) or 2
        values_per_layer = shift * embedding + embedding * head_size
    else:
        conv = _int(model.arch_value("ssm.conv_kernel")) or 4
        inner = _int(model.arch_value("ssm.inner_size")) or 2 * embedding
        state = _int(model.arch_value("ssm.state_size")) or 16
        groups = _int(model.arch_value("ssm.group_count")) or 0
        values_per_layer = (conv - 1) * (inner + 2 * groups * state) + state * inner
    per_layer = values_per_layer * 4 * settings.slots
    gpu = cpu = 0
    for index, layer in enumerate(layers):
        if not layer.recurrent:
            continue
        if placement.layer_on_gpu(index, layer_count):
            gpu += per_layer
        else:
            cpu += per_layer
    slots = "slot" if settings.slots == 1 else "slots"
    return MemoryPart(
        "recurrent",
        "Recurrent state",
        gpu,
        cpu,
        f"Fixed-size state of {count} linear-attention layers, for {settings.slots} {slots}; "
        "it does not grow with the context.",
    )


def _compute(
    model: GgufModel,
    settings: _Settings,
    placement: _Placement,
    layer_count: int,
    largest_cpu_layer: int,
) -> MemoryPart:
    embedding = _int(model.arch_value("embedding_length")) or 0
    vocabulary = _vocabulary(model)
    feed_forward = max(
        _max_value(model.arch_value("feed_forward_length")),
        (_int(model.arch_value("expert_feed_forward_length")) or 0)
        * (_int(model.arch_value("expert_used_count")) or 0),
    )
    heads = _per_layer(model.arch_value("attention.head_count"), 0) or 1
    tokens = settings.ubatch
    logits = vocabulary * tokens * 4
    activations = tokens * (4 * embedding + 3 * feed_forward) * 4
    attention = 0 if settings.flash_attention else _pad(settings.context) * tokens * heads * 4
    output_on_gpu = placement.layer_on_gpu(layer_count, layer_count)
    any_gpu_layer = placement.gpu and any(
        placement.layer_on_gpu(layer, layer_count) for layer in range(layer_count)
    )
    all_gpu = placement.gpu and all(
        placement.layer_on_gpu(layer, layer_count) for layer in range(layer_count)
    )
    # The allocator reuses the layers' activations for the logits at the end of the graph,
    # so the larger of the two is what a device needs, plus any attention matrix.
    gpu = cpu = 0
    if any_gpu_layer:
        gpu = max(activations, logits if output_on_gpu else 0) + attention
        if largest_cpu_layer and tokens >= 32:
            # Large batches copy RAM-resident weights to the GPU a layer at a time.
            gpu += largest_cpu_layer
    cpu = tokens * embedding * 4 * 2
    if not all_gpu or not output_on_gpu:
        cpu += max(
            activations if not all_gpu else 0,
            logits if not output_on_gpu else 0,
        ) + (0 if any_gpu_layer else attention)
    detail = f"Scratch space for {tokens:,}-token batches"
    if not settings.flash_attention:
        detail += ", including the attention matrix flash attention would avoid"
    return MemoryPart(
        "compute",
        "Compute buffers",
        gpu,
        cpu,
        detail + ". Depends on the backend, so this is a rough figure.",
        approximate=True,
    )


def _growth(
    values: Mapping[str, ArgumentValue], settings: _Settings, main: _ModelEstimate
) -> list[MemoryPart]:
    growth: list[MemoryPart] = []
    checkpoints = _int(values.get("ctx-checkpoints"))
    checkpoints = _DEFAULT_CHECKPOINTS if checkpoints is None else checkpoints
    if checkpoints > 0 and main.checkpoint_bytes:
        size = checkpoints * settings.slots * main.checkpoint_bytes
        slots = "slot" if settings.slots == 1 else "slots"
        growth.append(
            MemoryPart(
                "checkpoints",
                "Context checkpoints",
                0,
                size,
                f"Up to {checkpoints} per slot for {settings.slots} {slots}, each a copy of "
                "the state that cannot be rewound.",
            )
        )
    cache_ram = _int(values.get("cache-ram"))
    cache_ram = _DEFAULT_CACHE_RAM_MIB if cache_ram is None else cache_ram
    if cache_ram > 0:
        growth.append(
            MemoryPart(
                "prompt-cache",
                "Prompt cache",
                0,
                cache_ram * MIB,
                "Up to this much RAM keeps earlier prompts for reuse (--cache-ram).",
            )
        )
    elif cache_ram < 0:
        growth.append(
            MemoryPart(
                "prompt-cache", "Prompt cache", 0, 0, "No limit is set (--cache-ram -1)."
            )
        )
    return growth


def _fit_notes(
    values: Mapping[str, ArgumentValue], settings: _Settings, devices: Devices, gpu_bytes: int
) -> list[str]:
    if not settings.gpu:
        return []
    notes: list[str] = []
    fit = values.get("fit") != "off"
    unset = [
        flag
        for flag, key in (("--n-gpu-layers", "n-gpu-layers"), ("--ctx-size", "ctx-size"))
        if key not in values or values.get(key) in {"auto", 0}
    ]
    target = _first_int(values.get("fit-target")) or _DEFAULT_FIT_TARGET_MIB
    capacity = devices.capacity_bytes
    if fit and unset:
        notes.append(
            f"--fit is on, so llama.cpp may change {' and '.join(unset)} at load to leave "
            f"{target:,} MiB free per GPU; these figures assume it changes nothing."
        )
        if capacity is not None and gpu_bytes > capacity - target * MIB:
            notes.append(
                "As set, this does not fit in GPU memory, so --fit will move layers to "
                "system RAM or shorten the context when it loads."
            )
    elif capacity is not None and gpu_bytes > capacity:
        notes.append(
            "This is more than the GPU memory the runtime reported. On Windows the driver "
            "spills into system RAM instead of failing, which is much slower."
        )
    return notes


def _overrides(value: ArgumentValue | None) -> tuple[tuple[tuple[str, bool], ...], tuple[str, ...]]:
    rules: list[tuple[str, bool]] = []
    invalid: list[str] = []
    for item in _items(value):
        for rule in item.split(","):
            pattern, separator, buffer = rule.partition("=")
            if not separator:
                continue
            try:
                re.compile(pattern)
            except re.error:
                invalid.append(pattern)
                continue
            rules.append((pattern, not buffer.strip().upper().startswith("CPU")))
    return tuple(rules), tuple(invalid)


def _tensor_layer(name: str) -> int | None:
    match = _LAYER_TENSOR.match(name)
    return int(match.group(1)) if match else None


def _vocabulary(model: GgufModel) -> int:
    for tensor in model.tensors:
        if tensor.name in {"output.weight", "token_embd.weight"} and len(tensor.dims) >= 2:
            return tensor.dims[1]
    return model.array_lengths.get("tokenizer.ggml.tokens", 0)


def _per_layer(value: object, index: int) -> int | None:
    if isinstance(value, tuple):
        item = value[index] if index < len(value) else None
        return int(item) if isinstance(item, int | float) else None
    return _int(value)


def _max_value(value: object) -> int:
    if isinstance(value, tuple):
        return max((int(item) for item in value if isinstance(item, int | float)), default=0)
    return _int(value) or 0


def _layers(value: ArgumentValue | None) -> int | None:
    number = _int(value)
    return None if number is None or number < 0 else number


def _cache_type(value: ArgumentValue | None) -> str:
    text = _text(value)
    return text.lower() if text and text.lower() in _CACHE_TYPE_BYTES else "f16"


def _items(value: ArgumentValue | None) -> list[str]:
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _first_int(value: ArgumentValue | None) -> int | None:
    items = _items(value) if not isinstance(value, int) else [str(value)]
    return _int(items[0]) if items else None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _pad(tokens: int) -> int:
    return -(-tokens // _PAD) * _PAD


def _format_bytes(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB" if value >= 1024**3 else f"{value / MIB:.0f} MiB"
