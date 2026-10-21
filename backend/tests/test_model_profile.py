import subprocess
from pathlib import Path

import pytest

from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.model_profile import (
    ModelProfile,
    ProfileValidationError,
    combine_presets,
    launch_vector,
    parse_command,
    redact_preset,
    render_command,
    render_preset,
    render_preset_unchecked,
    validate_profile,
    write_combined_preset_atomic,
    write_preset_atomic,
)
from llamawebui.domain.profile_arguments import (
    ProfileArgument,
    errors_of,
    migrate_legacy_configuration,
    warnings_of,
)
from llamawebui.domain.runtime_capabilities import (
    RuntimeCapabilities,
    RuntimeOption,
    parse_help_output,
)

CATALOG = default_catalog()


def capabilities(*options: str) -> RuntimeCapabilities:
    return RuntimeCapabilities(options=frozenset(options), raw_help="help")


def typed(argument_id: str, value: object) -> ProfileArgument:
    definition = CATALOG.get(argument_id)
    assert definition is not None
    stored = tuple(value) if isinstance(value, list) else value
    return ProfileArgument(definition.flag, stored, catalog_id=argument_id)  # type: ignore[arg-type]


def create_shards(tmp_path: Path, count: int = 3) -> Path:
    for index in range(1, count + 1):
        (tmp_path / f"Qwen-UD-IQ4_XS-{index:05d}-of-{count:05d}.gguf").touch()
    return tmp_path / f"Qwen-UD-IQ4_XS-00001-of-{count:05d}.gguf"


LEGACY_QWEN = {
    "no_reasoning_preserve": True,
    "n_gpu_layers": 60,
    "ctx_size": 262144,
    "flash_attn": "on",
    "load_mode": "none",
    "lazy_mode": "on",
    "cache_ram": 0,
    "fit": "off",
    "override_tensor": ["per_layer_token_embd=CPU"],
    "cache_type_k": "q4_0",
    "cache_type_v": "q4_0",
    "threads": 8,
    "batch_size": 1024,
    "ubatch_size": 1024,
}


def test_migrated_legacy_profile_renders_byte_identical_preset(tmp_path: Path) -> None:
    model = create_shards(tmp_path)
    configuration = migrate_legacy_configuration(LEGACY_QWEN, CATALOG)
    profile = ModelProfile(
        alias="qwen3.8-flash-next-ud-iq4-xs",
        model_path=model,
        arguments=configuration.arguments,
    )
    supported = capabilities(*(option.key for option in profile.options(CATALOG)))

    rendered = render_preset(profile, CATALOG, supported)

    # This is exactly what the legacy flat serializer produced for the same profile.
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
    assert all(argument.catalog_id for argument in configuration.arguments)


def test_render_command_quotes_paths_but_keeps_numeric_values_readable(tmp_path: Path) -> None:
    model = tmp_path / "model file.gguf"
    profile = ModelProfile(alias="model", model_path=model, arguments=(typed("ctx-size", 4096),))

    command = render_command(profile, CATALOG, tmp_path / "llama server.exe", platform="nt")
    posix = render_command(profile, CATALOG, tmp_path / "llama-server", platform="posix")

    assert 'llama server.exe"' in command
    assert "model file.gguf\"" in command
    assert "--ctx-size 4096" in command
    assert "'" in posix and "--ctx-size 4096" in posix


def test_launch_vector_expands_booleans_lists_and_two_value_options(tmp_path: Path) -> None:
    profile = ModelProfile(
        alias="model",
        model_path=tmp_path / "model.gguf",
        arguments=(
            typed("kv-offload", False),
            typed("cont-batching", True),
            typed("override-tensor", ["a=CPU", "b=CPU"]),
            typed("tensor-split", [3, 1]),
            typed("control-vector-layer-range", "10 20"),
            typed("hf-token", "secret-token"),
            ProfileArgument("--future-flag", True, origin="custom"),
        ),
    )

    vector = launch_vector(profile, CATALOG, Path("llama-server"))
    unredacted = launch_vector(profile, CATALOG, Path("llama-server"), redact=False)

    assert vector[3:] == (
        "--no-kv-offload",
        "--cont-batching",
        "--override-tensor",
        "a=CPU",
        "--override-tensor",
        "b=CPU",
        "--tensor-split",
        "3,1",
        "--control-vector-layer-range",
        "10",
        "20",
        "--hf-token",
        "******",
        "--future-flag",
    )
    assert "secret-token" in unredacted
    preset = render_preset_unchecked(profile, CATALOG)
    assert "hf-token = secret-token" in preset
    assert "hf-token = ******" in redact_preset(preset, CATALOG)
    assert "hf-token = ******" in render_preset_unchecked(profile, CATALOG, redact=True)
    assert "control-vector-layer-range = 10 20" in preset


def test_windows_command_round_trip_preserves_crt_quoting_cases() -> None:
    from llamawebui.domain.model_profile import _split_windows_command_line

    arguments = [
        r"C:\Program Files\llama\llama-server.exe",
        "--model",
        "C:\\models\\quoted folder\\model-00001-of-00003.gguf",
        "--future-value",
        'a value with \\"quotes\\" and \\\\ slashes',
        "--trailing",
        "C:\\folder with space\\",
        "--tabbed",
        "tab\tvalue",
    ]
    command = subprocess.list2cmdline(arguments)

    assert _split_windows_command_line(command) == arguments


def test_parse_command_maps_representative_short_aliases_on_windows() -> None:
    command = (
        r'"C:\Program Files\llama\llama-server.exe" -m '
        r'"C:\models\Qwen model\model-00001-of-00003.gguf" '
        "--no-reasoning-preserve -ngl 60 -c 262144 -fa on --load-mode none "
        "-lzm on --cache-ram 0 --fit off -ot per_layer_token_embd=CPU "
        "-ctk q4_0 -ctv q4_0 --threads 8 -b 1024 -ub 1024 "
        '--future-option "custom value" --future-toggle'
    )

    parsed = parse_command(command, CATALOG, platform="nt")

    assert parsed.model_path == r"C:\models\Qwen model\model-00001-of-00003.gguf"
    assert [argument.to_payload() for argument in parsed.arguments] == [
        {"id": "reasoning-preserve", "value": False},
        {"id": "n-gpu-layers", "value": 60},
        {"id": "ctx-size", "value": 262144},
        {"id": "flash-attn", "value": "on"},
        {"id": "load-mode", "value": "none"},
        {"id": "lazy-mode", "value": "on"},
        {"id": "cache-ram", "value": 0},
        {"id": "fit", "value": "off"},
        {"id": "override-tensor", "value": ["per_layer_token_embd=CPU"]},
        {"id": "cache-type-k", "value": "q4_0"},
        {"id": "cache-type-v", "value": "q4_0"},
        {"id": "threads", "value": 8},
        {"id": "batch-size", "value": 1024},
        {"id": "ubatch-size", "value": 1024},
        {"flag": "--future-option", "value": "custom value", "origin": "catalog-mismatch"},
        {"flag": "--future-toggle", "value": True, "origin": "catalog-mismatch"},
    ]


def test_parse_command_supports_equals_syntax_and_rejects_open_windows_quote() -> None:
    parsed = parse_command(
        "--model=model.gguf --ctx-size=4096 --no-warmup --kv-offload=false", CATALOG,
        platform="posix",
    )

    assert parsed.model_path == "model.gguf"
    assert [argument.to_payload() for argument in parsed.arguments] == [
        {"id": "ctx-size", "value": 4096},
        {"id": "warmup", "value": False},
        {"id": "kv-offload", "value": False},
    ]
    with pytest.raises(ValueError, match="unterminated quoted"):
        parse_command('--model "model.gguf', CATALOG, platform="nt")
    with pytest.raises(ValueError, match="invalid value"):
        parse_command("--model m.gguf --warmup=maybe", CATALOG, platform="posix")


def test_parse_exported_full_command_ignores_router_preset_option(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.touch()
    profile = ModelProfile("round-trip", model, arguments=(typed("ctx-size", 4096),))

    command = render_command(
        profile, CATALOG, tmp_path / "llama-server.exe", platform="nt"
    ) + ' --models-preset "C:\\data\\llama-models.ini" --port 8080 --api-key secret --help'

    parsed = parse_command(command, CATALOG, platform="nt")

    assert parsed.model_path == str(model.resolve())
    assert parsed.arguments == (typed("ctx-size", 4096),)
    assert parsed.ignored == (
        r"--models-preset C:\data\llama-models.ini",
        "--port 8080",
        "--api-key ******",
        "--help",
    )


def test_parse_command_uses_runtime_help_for_uncatalogued_options() -> None:
    runtime = RuntimeCapabilities(
        options=frozenset({"model", "brand-new"}),
        raw_help="",
        details=(RuntimeOption("--brand-new", ("-bn",), "-bn, --brand-new N", "new"),),
    )

    parsed = parse_command(
        "--model m.gguf -bn 3 --other-new -1 --threads -1", CATALOG, platform="posix",
        capabilities=runtime,
    )

    assert [argument.to_payload() for argument in parsed.arguments] == [
        {"flag": "--brand-new", "value": "3", "origin": "runtime"},
        {"flag": "--other-new", "value": "-1", "origin": "catalog-mismatch"},
        {"id": "threads", "value": -1},
    ]
    with pytest.raises(ValueError, match="unknown short option -zz"):
        parse_command("--model m.gguf -zz 1", CATALOG, platform="posix")


def test_parse_command_merges_repeated_lists_and_rejects_duplicates() -> None:
    parsed = parse_command(
        "--model m.gguf -ot a=CPU --lora one.gguf -ot b=CPU", CATALOG, platform="posix"
    )

    assert parsed.arguments[0] == typed("override-tensor", ["a=CPU", "b=CPU"])
    assert parsed.arguments[1] == typed("lora", ["one.gguf"])
    with pytest.raises(ValueError, match="more than once"):
        parse_command("--model m.gguf -c 1 --ctx-size 2", CATALOG, platform="posix")
    with pytest.raises(ValueError, match="only once"):
        parse_command("-m one.gguf --model two.gguf", CATALOG, platform="posix")
    with pytest.raises(ValueError, match="requires a value"):
        parse_command("--model m.gguf --control-vector-layer-range 1", CATALOG, platform="posix")
    with pytest.raises(ValueError, match="separate values"):
        parse_command(
            "--model m.gguf --control-vector-layer-range=1", CATALOG, platform="posix"
        )


def test_parse_command_rejects_missing_model_and_values() -> None:
    with pytest.raises(ValueError, match="--model"):
        parse_command("llama-server --ctx-size 4096", CATALOG, platform="posix")
    with pytest.raises(ValueError, match="requires a value"):
        parse_command("llama-server --model", CATALOG, platform="posix")
    with pytest.raises(ValueError, match="must not be empty"):
        parse_command("   ", CATALOG)


def test_parse_command_rejects_invalid_typed_values() -> None:
    with pytest.raises(ValueError, match="invalid value"):
        parse_command("--model model.gguf --ctx-size many", CATALOG, platform="posix")
    with pytest.raises(ValueError, match="--override-tensor requires a value"):
        parse_command("--model model.gguf --override-tensor", CATALOG, platform="posix")


def test_parse_command_rejects_unexpected_positional_argument() -> None:
    with pytest.raises(ValueError, match="unexpected command argument"):
        parse_command("llama-server positional --model model.gguf", CATALOG, platform="posix")


def test_validation_reports_alias_shard_errors_and_runtime_warnings(tmp_path: Path) -> None:
    model = create_shards(tmp_path)
    model.with_name("Qwen-UD-IQ4_XS-00002-of-00003.gguf").unlink()
    profile = ModelProfile(
        alias="models",
        model_path=model,
        arguments=(ProfileArgument("--unknown", "value", origin="custom"),),
    )

    issues = validate_profile(profile, CATALOG, capabilities("model"))

    errors = errors_of(issues)
    assert "unsafe or reserved model alias: models" in errors
    assert any("00002-of-00003.gguf" in error for error in errors)
    assert not any("--unknown" in error for error in errors)
    assert (
        "runtime does not advertise --unknown; it will still be passed to llama-server"
        in warnings_of(issues)
    )


def test_validation_requires_first_shard(tmp_path: Path) -> None:
    create_shards(tmp_path)
    profile = ModelProfile(
        alias="qwen",
        model_path=tmp_path / "Qwen-UD-IQ4_XS-00002-of-00003.gguf",
    )

    assert errors_of(validate_profile(profile, CATALOG, capabilities("model"))) == (
        f"primary model must be the first shard: {profile.model_path.resolve()}",
    )


def test_render_rejects_missing_model_and_unsafe_value(tmp_path: Path) -> None:
    profile = ModelProfile(
        alias="valid-alias",
        model_path=tmp_path / "missing.gguf",
        arguments=(ProfileArgument("--bad=name", "line\nbreak", origin="custom"),),
    )

    with pytest.raises(ProfileValidationError) as caught:
        render_preset(profile, CATALOG, capabilities("model", "bad=name"))

    assert "model file not found" in str(caught.value)
    assert "unsafe option name" in str(caught.value)
    assert "contains a newline" in str(caught.value)


def test_render_saves_flag_absent_from_help_with_a_warning(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.touch()
    profile = ModelProfile(alias="model", model_path=model, arguments=(typed("ctx-size", 4096),))
    runtime = parse_help_output("--model FNAME   model\n--models-preset PATH  presets\n")

    rendered = render_preset(profile, CATALOG, runtime)
    issues = validate_profile(profile, CATALOG, runtime)

    assert "ctx-size = 4096" in rendered
    assert errors_of(issues) == ()
    assert any("--ctx-size" in warning for warning in warnings_of(issues))


def test_write_preset_atomically_replaces_existing_file(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.touch()
    destination = tmp_path / "config" / "llama-models.ini"
    destination.parent.mkdir()
    destination.write_text("old", encoding="utf-8")
    profile = ModelProfile(alias="model", model_path=model)

    write_preset_atomic(destination, profile, CATALOG, capabilities("model"))

    assert destination.read_text(encoding="utf-8").startswith("version = 1\n")
    assert list(destination.parent.iterdir()) == [destination]


def test_combine_presets_emits_one_version_and_each_section(tmp_path: Path) -> None:
    first_model = tmp_path / "first.gguf"
    second_model = tmp_path / "second.gguf"
    first_model.touch()
    second_model.touch()
    supported = capabilities("model")
    presets = (
        render_preset(ModelProfile("first", first_model), CATALOG, supported),
        render_preset(ModelProfile("second", second_model), CATALOG, supported),
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


# The primary-test preset from section 7.3 of docs/plans/llama-web-ui-plan.md as a command.
PLAN_MODEL = (
    r"C:\Users\nickp\.lmstudio\models\unsloth\Qwen3.8-Flash-Next-GGUF"
    r"\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"
)
PLAN_OPTIONS = (
    "--no-reasoning-preserve",
    "--n-gpu-layers 60",
    "--ctx-size 262144",
    "--flash-attn on",
    "--load-mode none",
    "--lazy-mode on",
    "--cache-ram 0",
    "--fit off",
    "--override-tensor per_layer_token_embd=CPU",
    "--cache-type-k q4_0",
    "--cache-type-v q4_0",
    "--threads 8",
    "--batch-size 1024",
    "--ubatch-size 1024",
)
PLAN_ARGUMENTS = (
    typed("reasoning-preserve", False),
    typed("n-gpu-layers", 60),
    typed("ctx-size", 262144),
    typed("flash-attn", "on"),
    typed("load-mode", "none"),
    typed("lazy-mode", "on"),
    typed("cache-ram", 0),
    typed("fit", "off"),
    typed("override-tensor", ("per_layer_token_embd=CPU",)),
    typed("cache-type-k", "q4_0"),
    typed("cache-type-v", "q4_0"),
    typed("threads", 8),
    typed("batch-size", 1024),
    typed("ubatch-size", 1024),
)


@pytest.mark.parametrize(
    ("prefix", "separator"),
    (
        ("llama-server.exe", " "),
        ("llama-server.exe", " ^\r\n  "),
        (r'& "C:\llama.cpp\llama-server.exe"', " `\n  "),
    ),
    ids=("single-line", "cmd-continuations", "powershell-continuations"),
)
def test_parse_command_imports_the_plan_preset_command_on_windows(
    prefix: str, separator: str
) -> None:
    command = separator.join((prefix, f"-m {PLAN_MODEL}", *PLAN_OPTIONS))

    parsed = parse_command(command, CATALOG, platform="nt")

    assert parsed.model_path == PLAN_MODEL
    assert parsed.arguments == PLAN_ARGUMENTS
    assert parsed.ignored == ()


def test_parse_command_imports_the_plan_preset_command_with_posix_continuations() -> None:
    command = " \\\n  ".join(
        ("./llama-server", "-m '/models/Qwen3.8 Flash/model-00001-of-00003.gguf'", *PLAN_OPTIONS)
    )

    parsed = parse_command(command, CATALOG, platform="posix")

    assert parsed.model_path == "/models/Qwen3.8 Flash/model-00001-of-00003.gguf"
    assert parsed.arguments == PLAN_ARGUMENTS


def test_parse_command_reports_hugging_face_model_sources_without_a_local_model() -> None:
    parsed = parse_command(
        "llama-server -hf unsloth/Qwen3-GGUF:Q4_K_M --hf-file Qwen3-Q4_K_M.gguf -c 4096",
        CATALOG,
        platform="posix",
    )

    assert parsed.model_path is None
    assert parsed.hf_repo == "unsloth/Qwen3-GGUF:Q4_K_M"
    assert parsed.hf_file == "Qwen3-Q4_K_M.gguf"
    assert parsed.arguments == (typed("ctx-size", 4096),)
    assert parsed.ignored == (
        "-hf unsloth/Qwen3-GGUF:Q4_K_M",
        "--hf-file Qwen3-Q4_K_M.gguf",
    )
    with pytest.raises(ValueError, match="--model or --hf-repo"):
        parse_command("llama-server --hf-file x.gguf -c 1", CATALOG, platform="posix")
