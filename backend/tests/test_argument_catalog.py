import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from llamawebui.domain.argument_catalog import (
    CATALOG_DIRECTORY,
    DEFAULT_CATALOG_FILE,
    SCHEMA_FILE,
    ArgumentCatalog,
    CatalogError,
    catalog_json_schema,
    default_catalog,
    load_catalog,
)

REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "llama-server-v0.4.1-cli-reference.md"
CATALOG_PATH = CATALOG_DIRECTORY / DEFAULT_CATALOG_FILE


def _raw_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _reference_flags() -> list[str]:
    rows = re.findall(r"^\| `(--[a-z0-9-]+)", REFERENCE.read_text(encoding="utf-8"), re.M)
    return rows


def test_bundled_catalog_models_every_reference_process_argument() -> None:
    catalog = default_catalog()
    flags = _reference_flags()

    missing = [flag for flag in flags if catalog.resolve(flag) is None]

    assert missing == []
    # Every row plus --cache-type-k/--cache-type-v, which the reference's support summary
    # lists but its tables omit. Nothing else (for example API request fields) is modelled.
    assert len(catalog.arguments) == len(flags) + 2
    assert catalog.get("cache-type-k") is not None
    assert catalog.get("cache-type-v") is not None
    assert catalog.catalog_version == "0.4.1"


def test_simple_mode_is_the_set_the_legacy_editor_exposed() -> None:
    catalog = default_catalog()
    simple = sorted(
        (definition for definition in catalog.arguments if not definition.is_advanced),
        key=lambda definition: definition.common_rank or 0,
    )

    assert [definition.id for definition in simple] == [
        "ctx-size",
        "n-gpu-layers",
        "flash-attn",
        "threads",
        "batch-size",
        "reasoning-preserve",
    ]


def test_catalog_lookup_is_case_sensitive_and_understands_negations() -> None:
    catalog = default_catalog()

    ctx = catalog.resolve("-c")
    mask = catalog.resolve("-C")
    no_offload = catalog.resolve("--no-kv-offload")
    short_negation = catalog.resolve("-nr")

    assert ctx is not None and ctx.definition.id == "ctx-size" and not ctx.negated
    assert mask is not None and mask.definition.id == "cpu-mask"
    assert no_offload is not None and no_offload.negated
    assert short_negation is not None and short_negation.definition.id == "repack"
    assert catalog.resolve("--not-an-option") is None
    assert catalog.category_order()["context-kv"] == 20
    assert catalog.summary()["catalogId"] == "llama-server"
    assert catalog.to_payload()["arguments"]


def test_shipped_json_schema_matches_the_catalog_contract() -> None:
    shipped = json.loads((CATALOG_DIRECTORY / SCHEMA_FILE).read_text(encoding="utf-8"))

    assert shipped == catalog_json_schema()
    assert _raw_catalog()["$schema"] == f"./{SCHEMA_FILE}"


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _mutated(mutate: Any) -> dict[str, Any]:
    document = copy.deepcopy(_raw_catalog())
    mutate(document)
    return document


def _argument(document: dict[str, Any], argument_id: str) -> dict[str, Any]:
    return next(item for item in document["arguments"] if item["id"] == argument_id)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda d: d["arguments"].append(copy.deepcopy(d["arguments"][0])),
            "duplicate argument id",
        ),
        (
            lambda d: _argument(d, "threads")["aliases"].append("-c"),
            "duplicate flag -c",
        ),
        (lambda d: _argument(d, "threads").update(category="nowhere"), "unknown category"),
        (lambda d: _argument(d, "threads").update(effectTags=["warp"]), "unknown effect tags"),
        (lambda d: _argument(d, "threads").update(conflicts=["missing"]), "unknown argument"),
        (lambda d: d["categories"].append(copy.deepcopy(d["categories"][0])), "duplicate category"),
        (lambda d: _argument(d, "fit")["value"].update(options=[]), "enum values require options"),
        (
            lambda d: _argument(d, "threads")["value"].update(options=[{"value": "1"}]),
            "only enum values",
        ),
        (
            lambda d: _argument(d, "fit")["value"].update(
                options=[{"value": "on"}, {"value": "on"}]
            ),
            "unique",
        ),
        (
            lambda d: _argument(d, "device")["value"].update(repeat=True),
            "exactly one of separator or repeat",
        ),
        (lambda d: _argument(d, "threads")["value"].update(repeat=True), "apply only to list"),
        (
            lambda d: _argument(d, "threads")["value"].update(negation="--no-threads"),
            "only boolean",
        ),
        (
            lambda d: _argument(d, "fit")["value"].update(specialValues=["auto"]),
            "specialValues",
        ),
        (lambda d: _argument(d, "threads")["value"].update(minimum=5, maximum=1), "minimum"),
        (lambda d: _argument(d, "cpu-range")["value"].update(pattern="("), "invalid pattern"),
        (lambda d: _argument(d, "threads")["value"].update(fileType="gguf"), "fileType"),
        (lambda d: _argument(d, "path")["value"].update(fileType="gguf"), "fileType"),
        (lambda d: _argument(d, "port").pop("controlReason"), "controlReason"),
        (lambda d: _argument(d, "threads").update(control="managed", controlReason="x",
                                                  isAdvanced=False), "Simple mode"),
        (lambda d: _argument(d, "threads").update(flag="threads"), "flag"),
        (lambda d: _argument(d, "threads").update(surprise=True), "surprise"),
    ),
)
def test_loader_rejects_inconsistent_catalogs(
    tmp_path: Path, mutate: Any, message: str
) -> None:
    with pytest.raises(CatalogError, match=message):
        load_catalog(_write(tmp_path, _mutated(mutate)))


def test_model_file_arguments_are_marked_as_gguf() -> None:
    marked = {
        definition.id
        for definition in default_catalog().arguments
        if definition.value.file_type == "gguf"
    }

    assert marked == {"model", "mmproj", "spec-draft-model", "lora", "control-vector"}


def test_loader_reports_unreadable_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="could not be read"):
        load_catalog(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid JSON"):
        load_catalog(broken)


def test_loaded_catalog_is_equivalent_to_default(tmp_path: Path) -> None:
    loaded = load_catalog(_write(tmp_path, _raw_catalog()))

    assert isinstance(loaded, ArgumentCatalog)
    assert loaded.to_payload() == default_catalog().to_payload()
