from llamawebui.domain.runtime_capabilities import parse_help_output, parse_version_output


def test_parse_help_output_collects_aliases_and_current_advanced_options() -> None:
    output = """
    -ngl, --gpu-layers, --n-gpu-layers N   max layers in VRAM
    -ot, --override-tensor PATTERN=TYPE    override tensor buffer type
    --load-mode MODE                       model loading mode
    --no-reasoning-preserve                do not preserve reasoning
    """

    capabilities = parse_help_output(output)

    assert capabilities.options == frozenset(
        {"gpu-layers", "n-gpu-layers", "override-tensor", "load-mode", "no-reasoning-preserve"}
    )
    assert capabilities.supports("--override-tensor")
    assert capabilities.supports("N-GPU-LAYERS")
    assert not capabilities.supports("--removed-option")
    assert capabilities.raw_help == output


def test_parse_help_output_ignores_prose_and_short_options() -> None:
    capabilities = parse_help_output("Use -c N. Text-with-dashes is not an option.\n")

    assert capabilities.options == frozenset()


def test_runtime_capabilities_classifies_router_support() -> None:
    capabilities = parse_help_output("--model PATH\n--models-preset PATH\n")

    assert capabilities.router_compatible
    assert capabilities.missing_router_options == ()

    unsupported = parse_help_output("--model PATH\n")
    assert not unsupported.router_compatible
    assert unsupported.missing_router_options == ("models-preset",)


def test_parse_version_output_reads_labeled_build_and_commit() -> None:
    version = parse_version_output("version: b10964 (commit B29C606E28A01B1B)\n")

    assert version.build == "b10964"
    assert version.commit == "b29c606e28a01b1b"


def test_parse_version_output_reads_parenthesized_commit() -> None:
    version = parse_version_output("version: 10964 (b29c606e)\nbuilt with MSVC")

    assert version.build == "10964"
    assert version.commit == "b29c606e"


def test_parse_version_output_preserves_unknown_format() -> None:
    output = "llama.cpp experimental release"

    version = parse_version_output(output)

    assert version.build is None
    assert version.commit is None
    assert version.raw == output