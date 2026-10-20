from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.effective_catalog import argument_status, build_effective_catalog
from llamawebui.domain.runtime_capabilities import (
    RuntimeCapabilities,
    RuntimeOption,
    parse_help_output,
)

CATALOG = default_catalog()


def _definition(argument_id: str):  # type: ignore[no-untyped-def]
    found = CATALOG.get(argument_id)
    assert found is not None
    return found


def test_argument_status_distinguishes_advertised_alias_and_missing() -> None:
    runtime = RuntimeCapabilities(
        options=frozenset({"ctx-size", "no-warmup", "draft-max"}), raw_help=""
    )

    assert argument_status(_definition("ctx-size"), runtime) == ("advertised", None)
    assert argument_status(_definition("warmup"), runtime) == ("advertised", None)
    assert argument_status(_definition("spec-draft-n-max"), runtime) == ("alias", "--draft-max")
    assert argument_status(_definition("threads"), runtime) == ("not-advertised", None)
    unparsed = RuntimeCapabilities(options=frozenset(), raw_help="")
    assert argument_status(_definition("threads"), unparsed) == ("unknown", None)


def test_effective_catalog_merges_runtime_help_and_lists_uncatalogued_rows() -> None:
    runtime = parse_help_output(
        "-t,    --threads N                      CPU threads (default: -1)\n"
        "-bn,   --brand-new N                    a new option\n"
        "-x                                      short only\n"
    )

    payload = build_effective_catalog(CATALOG, runtime, {"id": "runtime-1"})

    arguments = {item["id"]: item for item in payload["arguments"]}  # type: ignore[union-attr]
    assert arguments["threads"]["runtime"] == {
        "status": "advertised",
        "advertisedAs": None,
        "syntax": "-t, --threads N",
        "help": "CPU threads (default: -1)",
        "default": "-1",
    }
    assert arguments["ctx-size"]["runtime"]["status"] == "not-advertised"
    assert payload["uncatalogued"] == [
        {
            "flag": "--brand-new",
            "aliases": ["-bn"],
            "syntax": "-bn, --brand-new N",
            "help": "a new option",
            "default": None,
        }
    ]
    runtime_summary = payload["runtime"]
    assert isinstance(runtime_summary, dict)
    assert runtime_summary["id"] == "runtime-1"
    assert runtime_summary["helpParsed"] is True
    assert [issue["code"] for issue in payload["issues"]] == ["not-advertised"]  # type: ignore[union-attr,index]
    assert payload["catalogVersion"] == "0.4.1"
    assert payload["categories"] and payload["effectTags"]


def test_effective_catalog_without_details_or_help() -> None:
    options_only = RuntimeCapabilities(options=frozenset({"threads", "mystery"}), raw_help="")
    unparsed = RuntimeCapabilities(options=frozenset(), raw_help="")
    catalogued_detail = RuntimeCapabilities(
        options=frozenset({"threads"}),
        raw_help="",
        details=(RuntimeOption("--threads", ("-t",), "-t, --threads N", ""),),
    )

    options_payload = build_effective_catalog(CATALOG, options_only, {})
    unparsed_payload = build_effective_catalog(CATALOG, unparsed, {})
    detail_payload = build_effective_catalog(CATALOG, catalogued_detail, {})

    assert options_payload["uncatalogued"] == [
        {"flag": "--mystery", "aliases": [], "syntax": "--mystery", "help": "", "default": None}
    ]
    assert [issue["code"] for issue in options_payload["issues"]] == [  # type: ignore[union-attr,index]
        "details-missing",
        "not-advertised",
    ]
    assert [issue["code"] for issue in unparsed_payload["issues"]] == ["help-unparsed"]  # type: ignore[union-attr,index]
    assert unparsed_payload["uncatalogued"] == []
    assert detail_payload["uncatalogued"] == []
