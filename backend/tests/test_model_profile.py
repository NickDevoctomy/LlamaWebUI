from pathlib import Path

import pytest

from llamawebui.domain.model_profile import (
    AdvancedOption,
    ModelProfile,
    ProfileValidationError,
    combine_presets,
    parse_command,
    render_command,
    render_preset,
    validate_profile,
    write_combined_preset_atomic,
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


def test_render_command_quotes_paths_but_keeps_numeric_values_readable(tmp_path: Path) -> None:
    model = tmp_path / "model file.gguf"
    profile = ModelProfile(alias="model", model_path=model, ctx_size=4096)

    command = render_command(profile, tmp_path / "llama server.exe", platform="nt")

    assert 'llama server.exe"' in command
    assert "model file.gguf\"" in command
    assert "--ctx-size 4096" in command


def test_parse_command_maps_known_and_preserves_unknown_options() -> None:
    configuration = parse_command(
        'llama-server --model "C:\\models\\model file.gguf" '
        "--ctx-size 4096 --no-reasoning-preserve --future-flag value"
    )

    assert configuration["model_path"] == "C:\\models\\model file.gguf"
    assert configuration["ctx_size"] == 4096
    assert configuration["no_reasoning_preserve"] is True
    assert configuration["advanced"] == [{"name": "future-flag", "value": "value"}]


def test_parse_command_rejects_missing_model_and_values() -> None:
    with pytest.raises(ValueError, match="--model"):
        parse_command("llama-server --ctx-size 4096")
    with pytest.raises(ValueError, match="requires a value"):
        parse_command("llama-server --model")


def test_parse_command_maps_all_typed_options_and_overrides() -> None:
    configuration = parse_command(
        "--model model.gguf --n-gpu-layers 20 --threads 8 --batch-size 256 "
        "--ubatch-size 64 --flash-attn on --load-mode none --lazy-mode on "
        "--cache-ram 0 --fit off --cache-type-k q4_0 --cache-type-v q4_0 "
        "--override-tensor tensor=CPU --future-flag"
    )

    assert configuration["n_gpu_layers"] == 20
    assert configuration["threads"] == 8
    assert configuration["batch_size"] == 256
    assert configuration["ubatch_size"] == 64
    assert configuration["flash_attn"] == "on"
    assert configuration["load_mode"] == "none"
    assert configuration["lazy_mode"] == "on"
    assert configuration["cache_ram"] == 0
    assert configuration["fit"] == "off"
    assert configuration["cache_type_k"] == "q4_0"
    assert configuration["cache_type_v"] == "q4_0"
    assert configuration["override_tensor"] == ["tensor=CPU"]
    assert configuration["advanced"] == [{"name": "future-flag", "value": True}]


def test_parse_command_rejects_invalid_typed_values() -> None:
    with pytest.raises(ValueError, match="invalid value"):
        parse_command("--model model.gguf --ctx-size many")
    with pytest.raises(ValueError, match="--override-tensor requires a value"):
        parse_command("--model model.gguf --override-tensor")


def test_parse_command_rejects_unexpected_positional_argument() -> None:
    with pytest.raises(ValueError, match="unexpected command argument"):
        parse_command("llama-server positional --model model.gguf")


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


def test_combine_presets_emits_one_version_and_each_section(tmp_path: Path) -> None:
    first_model = tmp_path / "first.gguf"
    second_model = tmp_path / "second.gguf"
    first_model.touch()
    second_model.touch()
    supported = capabilities("model")
    presets = (
        render_preset(ModelProfile("first", first_model), supported),
        render_preset(ModelProfile("second", second_model), supported),
    )
    destination = tmp_path / "generated" / "llama-models.ini"

    write_combined_preset_atomic(destination, presets)

    combined = destination.read_text(encoding="utf-8")
    assert combined.count("version = 1") == 1
    assert combined.count("[first]") == 1
    assert combined.count("[second]") == 1
    assert combined == combine_presets(presets)


@pytest.mark.parametrize(
    ("presets", "message"),
    (
        ((), "at least one enabled"),
        (("[model]\nmodel = x\n",), "malformed"),
        (("version = 1\n\n[one]\n[x]\n",), "malformed"),
        (
            ("version = 1\n\n[same]\nmodel = one\n",) * 2,
            "duplicate or empty",
        ),
    ),
)
def test_combine_presets_rejects_invalid_input(
    presets: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        combine_presets(presets)