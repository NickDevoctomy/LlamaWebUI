from pathlib import Path

import pytest

from llamawebui.domain.model_profile import (
    AdvancedOption,
    ModelProfile,
    ProfileValidationError,
    render_preset,
    validate_profile,
    write_preset_atomic,
)
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities


def capabilities(*options: str) -> RuntimeCapabilities:
    return RuntimeCapabilities(options=frozenset(options), raw_help="help")


def create_shards(tmp_path: Path, count: int = 3) -> Path:
    for index in range(1, count + 1):
        (tmp_path / f"Qwen-UD-IQ4_XS-{index:05d}-of-{count:05d}.gguf").touch()
    return tmp_path / f"Qwen-UD-IQ4_XS-00001-of-{count:05d}.gguf"


def test_render_qwen_profile_as_deterministic_preset(tmp_path: Path) -> None:
    model = create_shards(tmp_path)
    profile = ModelProfile(
        alias="qwen3.8-flash-next-ud-iq4-xs",
        model_path=model,
        no_reasoning_preserve=True,
        n_gpu_layers=60,
        ctx_size=262144,
        flash_attn="on",
        load_mode="none",
        lazy_mode="on",
        cache_ram=0,
        fit="off",
        override_tensor=("per_layer_token_embd=CPU",),
        cache_type_k="q4_0",
        cache_type_v="q4_0",
        threads=8,
        batch_size=1024,
        ubatch_size=1024,
    )
    supported = capabilities(*(option.name for option in profile.options()))

    rendered = render_preset(profile, supported)

    assert rendered == (
        "version = 1\n\n"
        "[qwen3.8-flash-next-ud-iq4-xs]\n"
        f"model = {model.resolve()}\n"
        "no-reasoning-preserve = true\n"
        "n-gpu-layers = 60\n"
        "ctx-size = 262144\n"
        "flash-attn = on\n"
        "load-mode = none\n"
        "lazy-mode = on\n"
        "cache-ram = 0\n"
        "fit = off\n"
        "override-tensor = per_layer_token_embd=CPU\n"
        "cache-type-k = q4_0\n"
        "cache-type-v = q4_0\n"
        "threads = 8\n"
        "batch-size = 1024\n"
        "ubatch-size = 1024\n"
    )


def test_validation_reports_alias_capability_and_shard_errors(tmp_path: Path) -> None:
    model = create_shards(tmp_path)
    model.with_name("Qwen-UD-IQ4_XS-00002-of-00003.gguf").unlink()
    profile = ModelProfile(
        alias="models",
        model_path=model,
        advanced=(AdvancedOption("--unknown", "value"),),
    )

    errors = validate_profile(profile, capabilities("model"))

    assert "unsafe or reserved model alias: models" in errors
    assert any("00002-of-00003.gguf" in error for error in errors)
    assert "runtime does not support --unknown" in errors


def test_validation_requires_first_shard(tmp_path: Path) -> None:
    create_shards(tmp_path)
    profile = ModelProfile(
        alias="qwen",
        model_path=tmp_path / "Qwen-UD-IQ4_XS-00002-of-00003.gguf",
    )

    assert validate_profile(profile, capabilities("model")) == (
        f"primary model must be the first shard: {profile.model_path.resolve()}",
    )


def test_render_rejects_missing_model_and_unsafe_value(tmp_path: Path) -> None:
    profile = ModelProfile(
        alias="valid-alias",
        model_path=tmp_path / "missing.gguf",
        advanced=(AdvancedOption("bad=name", "line\nbreak"),),
    )

    with pytest.raises(ProfileValidationError) as caught:
        render_preset(profile, capabilities("model", "bad=name"))

    assert "model file not found" in str(caught.value)
    assert "unsafe option name" in str(caught.value)
    assert "contains a newline" in str(caught.value)


def test_write_preset_atomically_replaces_existing_file(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.touch()
    destination = tmp_path / "config" / "llama-models.ini"
    destination.parent.mkdir()
    destination.write_text("old", encoding="utf-8")
    profile = ModelProfile(alias="model", model_path=model)

    write_preset_atomic(destination, profile, capabilities("model"))

    assert destination.read_text(encoding="utf-8").startswith("version = 1\n")
    assert list(destination.parent.iterdir()) == [destination]