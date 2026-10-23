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

LLAMA_HELP = """----- common params -----

-h,    --help, --usage                  print usage and exit
-t,    --threads N                      number of CPU threads to use during generation (default: -1)
                                        (env: LLAMA_ARG_THREADS)
-fa,   --flash-attn [on|off|auto]       set Flash Attention ('on', 'off', 'auto', default: 'auto')
-kvo,  --kv-offload, -nkvo, --no-kv-offload
                                        whether to enable KV cache offloading (default: enabled)
-ts,   --tensor-split N0,N1,N2,...      fraction of the model to offload to each GPU
--cpu-mask M                            CPU affinity mask (default: "")
--no-only                               negative-only option
- not an option row
-x                                      short-only option
"""


def test_parse_help_options_reads_flags_aliases_syntax_and_defaults() -> None:
    capabilities = parse_help_output(LLAMA_HELP)
    rows = {option.flag: option for option in capabilities.details}

    assert list(rows) == [
        "--help",
        "--threads",
        "--flash-attn",
        "--kv-offload",
        "--tensor-split",
        "--cpu-mask",
        "--no-only",
        "-x",
    ]
    assert rows["--help"].aliases == ("-h", "--usage")
    assert rows["--threads"].default == "-1"
    assert rows["--threads"].help.endswith("(env: LLAMA_ARG_THREADS)")
    assert rows["--flash-attn"].syntax == "-fa, --flash-attn [on|off|auto]"
    assert rows["--flash-attn"].default == "auto"
    assert rows["--flash-attn"].help.startswith("set Flash Attention")
    assert rows["--kv-offload"].aliases == ("-kvo", "-nkvo", "--no-kv-offload")
    assert rows["--kv-offload"].default == "enabled"
    assert rows["--tensor-split"].syntax == "-ts, --tensor-split N0,N1,N2,..."
    assert rows["--cpu-mask"].default is None
    assert capabilities.help_parsed


def test_runtime_option_payload_round_trip_and_fingerprint() -> None:
    from llamawebui.domain.runtime_capabilities import RuntimeOption, flags_fingerprint

    capabilities = parse_help_output(LLAMA_HELP)
    restored = tuple(
        RuntimeOption.from_payload(option.to_payload()) for option in capabilities.details
    )
    reworded = parse_help_output(LLAMA_HELP.replace("print usage and exit", "show usage"))

    assert restored == capabilities.details
    assert RuntimeOption.from_payload({"aliases": "bad", "default": 3}) == RuntimeOption(
        "", (), "", ""
    )
    assert capabilities.flags_sha256 == reworded.flags_sha256
    assert capabilities.flags_sha256 != flags_fingerprint(frozenset({"other"}), ())
    assert not parse_help_output("").help_parsed
