from pathlib import Path

import pytest

from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.model_profile import ModelProfile, render_preset_unchecked
from llamawebui.domain.profile_arguments import (
    ArgumentValueError,
    EmittedOption,
    ProfileArgument,
    ProfileConfiguration,
    build_arguments,
    coerce_catalog_value,
    emit_argument,
    errors_of,
    migrate_legacy_configuration,
    normalize_configuration,
    normalize_raw_flag,
    normalize_raw_value,
    parse_structured_configuration,
    validate_arguments,
    warnings_of,
)
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities

CATALOG = default_catalog()
NO_HELP = RuntimeCapabilities(options=frozenset(), raw_help="")


def definition(argument_id: str):  # type: ignore[no-untyped-def]
    found = CATALOG.get(argument_id)
    assert found is not None
    return found


def typed(argument_id: str, value: object) -> ProfileArgument:
    stored = tuple(value) if isinstance(value, list) else value
    return ProfileArgument(definition(argument_id).flag, stored, catalog_id=argument_id)  # type: ignore[arg-type]


def codes(arguments: list[ProfileArgument], **kwargs: object) -> list[tuple[str, str]]:
    issues = validate_arguments(arguments, CATALOG, kwargs.pop("runtime", NO_HELP), **kwargs)  # type: ignore[arg-type]
    return [(issue.level, issue.code) for issue in issues]


@pytest.mark.parametrize(
    ("argument_id", "raw", "expected"),
    (
        ("warmup", True, True),
        ("warmup", "off", False),
        ("warmup", "YES", True),
        ("ctx-size", "4096", 4096),
        ("ctx-size", 4096.0, 4096),
        ("n-gpu-layers", "auto", "auto"),
        ("temperature", "0.7", 0.7),
        ("temperature", 1, 1),
        ("temperature", "2", 2),
        ("flash-attn", "on", "on"),
        ("mirostat", 2, "2"),
        ("chat-template-kwargs", '{"enable_thinking": false}', '{"enable_thinking": false}'),
        ("device", "CUDA0,CUDA1", ("CUDA0", "CUDA1")),
        ("tensor-split", "3,1.5", (3, 1.5)),
        ("fit-target", [1024, "512"], (1024, 512)),
        ("override-tensor", "exps=CPU", ("exps=CPU",)),
        ("cpu-range", "0-7", "0-7"),
    ),
)
def test_coerce_catalog_value_accepts_each_schema_type(
    argument_id: str, raw: object, expected: object
) -> None:
    assert coerce_catalog_value(definition(argument_id), raw) == expected


@pytest.mark.parametrize(
    ("argument_id", "raw", "message"),
    (
        ("warmup", "maybe", "true or false"),
        ("ctx-size", "many", "whole number"),
        ("ctx-size", 1.5, "whole number"),
        ("ctx-size", -1, "at least 0"),
        ("ctx-size", True, "must be a number"),
        ("ctx-size", None, "whole number"),
        ("n-gpu-layers", "most", "auto, all"),
        ("top-p", 1.5, "at most 1"),
        ("temperature", "warm", "must be a number"),
        ("temperature", "nan", "finite"),
        ("flash-attn", "maybe", "one of: on, off, auto"),
        ("chat-template-kwargs", "{", "valid JSON"),
        ("cpu-range", "seven", "invalid format"),
        ("alias", "", "requires a value"),
        ("chat-template", "line\nbreak", "control characters"),
        ("chat-template", False, "text value"),
        ("device", [], "at least one value"),
        ("device", ["a,b"], "must not contain ','"),
        ("override-tensor", ["no-equals"], "invalid format"),
    ),
)
def test_coerce_catalog_value_rejects_invalid_input(
    argument_id: str, raw: object, message: str
) -> None:
    with pytest.raises(ArgumentValueError, match=message):
        coerce_catalog_value(definition(argument_id), raw)


def test_emission_handles_negation_lists_and_raw_values() -> None:
    assert emit_argument(typed("warmup", False), CATALOG) == (EmittedOption("--no-warmup"),)
    assert emit_argument(typed("swa-full", False), CATALOG) == ()
    assert emit_argument(typed("samplers", ["top_k", "temperature"]), CATALOG) == (
        EmittedOption("--samplers", "top_k;temperature"),
    )
    assert emit_argument(typed("tensor-split", [3, 1.5]), CATALOG) == (
        EmittedOption("--tensor-split", "3,1.5"),
    )
    raw = ProfileArgument("--api-key", "secret", origin="custom")
    assert emit_argument(raw, CATALOG) == (EmittedOption("--api-key", "secret", sensitive=True),)
    assert emit_argument(ProfileArgument("--x", ("a", "b"), origin="custom"), CATALOG) == (
        EmittedOption("--x", "a,b"),
    )
    assert EmittedOption("--hf-token", "t", sensitive=True).argv(redact=True) == (
        "--hf-token",
        "******",
    )
    assert EmittedOption("--flag").preset_value() == "true"


def test_legacy_migration_keeps_mismatches_as_custom_parameters() -> None:
    legacy = {
        "n_gpu_layers": -1,
        "flash_attn": "enabled",
        "override_tensor": ["valid=CPU", "invalid"],
        "advanced": [
            {"name": "--future-flag", "value": 3},
            {"name": "no-mmproj", "value": True},
            {"name": "kv-offload", "value": False},
            {"name": "parallel", "value": 4},
            {"name": "warmup", "value": "maybe"},
            {"name": "draft", "value": 5},
            {"name": "port", "value": 8080},
            {"name": "draft-max", "value": 8},
            {"value": "nameless"},
            "not-an-entry",
        ],
    }

    configuration = migrate_legacy_configuration(legacy, CATALOG)

    assert [argument.to_payload() for argument in configuration.arguments] == [
        {"flag": "--n-gpu-layers", "value": "-1", "origin": "catalog-mismatch"},
        {"flag": "--flash-attn", "value": "enabled", "origin": "catalog-mismatch"},
        {"flag": "--override-tensor", "value": "valid=CPU", "origin": "catalog-mismatch"},
        {"flag": "--override-tensor", "value": "invalid", "origin": "catalog-mismatch"},
        {"flag": "--future-flag", "value": "3", "origin": "catalog-mismatch"},
        # An alias negation would change the emitted spelling, so it stays raw.
        {"flag": "--no-mmproj", "value": True, "origin": "catalog-mismatch"},
        {"flag": "--kv-offload", "value": "false", "origin": "catalog-mismatch"},
        {"id": "parallel", "value": 4},
        {"flag": "--warmup", "value": "maybe", "origin": "catalog-mismatch"},
        {"flag": "--draft", "value": "5", "origin": "deprecated"},
        {"flag": "--port", "value": "8080", "origin": "catalog-mismatch"},
        {"flag": "--draft-max", "value": "8", "origin": "catalog-mismatch"},
    ]


def test_legacy_migration_round_trips_every_key_into_identical_presets(tmp_path: Path) -> None:
    legacy = {
        "no_reasoning_preserve": True,
        "ctx_size": 0,
        "override_tensor": ["a=CPU", "b=CPU"],
        "advanced": [{"name": "no-kv-offload", "value": True}, {"name": "x-new", "value": "1"}],
    }
    legacy_lines = [
        "no-reasoning-preserve = true",
        "ctx-size = 0",
        "override-tensor = a=CPU",
        "override-tensor = b=CPU",
        "no-kv-offload = true",
        "x-new = 1",
    ]

    configuration = migrate_legacy_configuration(legacy, CATALOG)
    stored = configuration.to_payload(CATALOG)
    reloaded = normalize_configuration(stored, CATALOG)
    preset = render_preset_unchecked(
        ModelProfile("legacy", tmp_path / "m.gguf", reloaded.arguments), CATALOG
    )

    assert reloaded == configuration
    assert preset.splitlines()[4:] == legacy_lines
    assert stored["schema_version"] == 2


def test_structured_configuration_tolerates_unknown_and_malformed_entries() -> None:
    configuration = parse_structured_configuration(
        {
            "schema_version": 2,
            "arguments": [
                {"id": "from-the-future", "value": 3},
                {"id": "lora", "value": ["one.gguf", {"bad": 1}]},
                {"id": "chat-template", "value": {"unexpected": True}},
                {"flag": "--x", "value": {"nested": 1}, "origin": "invented"},
                "junk",
            ],
            "acknowledged": ["agent", 4],
        },
        CATALOG,
    )

    assert [argument.to_payload() for argument in configuration.arguments] == [
        {"flag": "--from-the-future", "value": "3", "origin": "catalog-mismatch"},
        {"id": "lora", "value": ["one.gguf"]},
        {"id": "chat-template", "value": "{'unexpected': True}"},
        {"flag": "--x", "value": '{"nested": 1}', "origin": "custom"},
    ]
    assert configuration.acknowledged == frozenset({"agent"})
    empty = parse_structured_configuration({"schema_version": 2, "arguments": "x"}, CATALOG)
    assert empty == ProfileConfiguration()


def test_build_arguments_reports_every_input_problem() -> None:
    built = build_arguments(
        [
            {"id": "ctx-size", "value": "8192"},
            {"flag": "future", "value": 2},
            {"flag": "--toggle", "value": False},
            {"flag": "--bare"},
        ],
        CATALOG,
    )

    assert [argument.to_payload() for argument in built] == [
        {"id": "ctx-size", "value": 8192},
        {"flag": "--future", "value": "2", "origin": "custom"},
        {"flag": "--toggle", "value": "false", "origin": "custom"},
        {"flag": "--bare", "value": True, "origin": "custom"},
    ]
    with pytest.raises(ArgumentValueError) as caught:
        build_arguments(
            [
                {"id": "nope", "value": 1},
                {"id": "ctx-size", "value": "x"},
                {"flag": " "},
                {"flag": "--x", "origin": "made-up"},
                {"flag": "--y", "value": ["list"]},
            ],
            CATALOG,
        )
    message = str(caught.value)
    assert "unknown catalog argument: nope" in message
    assert "--ctx-size must be a whole number" in message
    assert "custom parameters require a flag" in message
    assert "invalid custom parameter origin: made-up" in message
    assert "--y: raw option values must be text or true" in message
    assert normalize_raw_flag(" -x ") == "-x"
    assert normalize_raw_value(1.5) == "1.5"


def test_validation_blocks_definite_faults() -> None:
    assert codes([typed("ctx-size", "many")]) == [("error", "invalid-value")]
    assert codes([typed("ctx-size", 1), ProfileArgument("--ctx-size", "2", origin="custom")]) == [
        ("error", "duplicate-flag"),
        ("warning", "catalogued-custom"),
    ]
    assert ("error", "unsafe-flag") in codes([ProfileArgument("-x", True, origin="custom")])
    assert codes([ProfileArgument("--x", "a\nb", origin="custom")]) == [
        ("error", "control-characters")
    ]
    for value in ("$(whoami)", "`id`", "a ; rm", "a | b", "x > out", "a && b", "${HOME}"):
        assert codes([ProfileArgument("--x", value, origin="custom")]) == [
            ("error", "shell-syntax")
        ], value
    for value in ("top_k;top_p", r"blk\.(up|down)=CPU", "a>b"):
        assert codes([ProfileArgument("--x", value, origin="custom")]) == [], value
    assert codes([typed("port", 8080)]) == [("error", "managed-flag")]
    assert codes([ProfileArgument("--help", True, origin="custom")]) == [
        ("error", "managed-flag")
    ]
    assert codes([ProfileArgument("--unknown", True, origin="unexpected")]) == []
    assert codes([ProfileArgument("--z", True, catalog_id="missing")]) == [
        ("error", "unknown-argument")
    ]


def test_validation_requires_security_acknowledgement() -> None:
    assert codes([typed("agent", True)]) == [("error", "unconfirmed-security")]
    assert codes([typed("agent", True)], acknowledged=frozenset({"agent"})) == [
        ("warning", "security")
    ]
    assert codes([ProfileArgument("--agent", True, origin="custom")]) == [
        ("warning", "catalogued-custom"),
        ("error", "unconfirmed-security"),
    ]
    assert codes(
        [typed("cors-origins", ["*"])], acknowledged=frozenset({"cors-origins"})
    ) == [("warning", "open-cors"), ("warning", "security")]
    assert codes([typed("metrics", True)]) == []


def test_validation_warns_about_deprecation_relationships_and_paths(tmp_path: Path) -> None:
    existing = tmp_path / "grammar.gbnf"
    existing.touch()
    assert codes([typed("defrag-thold", 0.1)]) == [("warning", "deprecated")]
    assert codes([ProfileArgument("--draft", "5", origin="deprecated")]) == [
        ("warning", "deprecated")
    ]
    assert codes([ProfileArgument("--draft-n", "5", origin="custom")]) == [
        ("warning", "deprecated"),
        ("warning", "deprecated"),
    ]
    assert codes([ProfileArgument("--old", "5", origin="deprecated")]) == [
        ("warning", "deprecated")
    ]
    assert codes([ProfileArgument("--new", "5", origin="catalog-mismatch")]) == [
        ("warning", "catalog-mismatch")
    ]
    assert codes([ProfileArgument("--threads", "x", origin="catalog-mismatch")]) == [
        ("warning", "catalog-mismatch")
    ]
    assert codes([typed("cpu-moe", True), typed("n-cpu-moe", 2)]) == [("warning", "conflict")]
    assert codes([typed("ssl-key-file", str(existing))]) == [("warning", "missing-dependency")]
    assert codes([typed("grammar-file", "relative.gbnf")]) == [("warning", "relative-path")]
    assert codes([typed("grammar-file", str(tmp_path / "missing.gbnf"))]) == [
        ("warning", "path-not-found")
    ]
    assert codes([typed("grammar-file", str(existing))]) == []
    assert codes([typed("slot-save-path", str(existing))]) == [("warning", "path-not-found")]
    assert codes([typed("slot-save-path", str(tmp_path))]) == []
    assert codes([typed("lora", [str(existing)])]) == []


def test_validation_checks_runtime_advertisement_only_when_help_parsed() -> None:
    runtime = RuntimeCapabilities(options=frozenset({"model", "warmup"}), raw_help="")
    arguments = [
        typed("warmup", False),
        typed("ctx-size", 1),
        ProfileArgument("--new-flag", True, origin="runtime"),
    ]

    issues = validate_arguments(arguments, CATALOG, runtime)

    assert errors_of(issues) == ()
    assert warnings_of(issues) == (
        "runtime does not advertise --no-warmup; it will still be passed to llama-server",
        "runtime does not advertise --ctx-size; it will still be passed to llama-server",
        "runtime does not advertise --new-flag; it will still be passed to llama-server",
    )
    assert validate_arguments(arguments, CATALOG, NO_HELP) == ()
    managed = validate_arguments([typed("host", "0.0.0.0")], CATALOG, runtime)
    assert [issue.code for issue in managed] == ["managed-flag"]
    assert managed[0].to_payload()["argument"] == "host"
