"""Versioned llama-server argument catalog: data contracts, loading, and flag lookup."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

CATALOG_DIRECTORY = Path(__file__).resolve().parent.parent / "catalogs"
DEFAULT_CATALOG_FILE = "llama-server-v0.4.1.json"
SCHEMA_FILE = "argument-catalog.schema.json"

ValueType = Literal["boolean", "integer", "number", "string", "path", "enum", "list", "json"]
ItemType = Literal["string", "integer", "number", "path"]
Control = Literal["editable", "managed", "informational", "removed"]
Direction = Literal["increase", "decrease", "tradeoff", "neutral"]
ScalarValue = str | int | float | bool

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
_LONG_FLAG_PATTERN = r"^--[a-z0-9][a-z0-9-]*$"
_ANY_FLAG_PATTERN = r"^-{1,2}[A-Za-z0-9][A-Za-z0-9-]*$"

LongFlag = Annotated[str, Field(pattern=_LONG_FLAG_PATTERN)]
AnyFlag = Annotated[str, Field(pattern=_ANY_FLAG_PATTERN)]
Identifier = Annotated[str, Field(pattern=_ID_PATTERN)]


class CatalogError(ValueError):
    """Raised when a catalog file is missing, malformed, or internally inconsistent."""


class _CatalogModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class EnumOption(_CatalogModel):
    value: str = Field(min_length=1)
    label: str | None = None


class ValueSchema(_CatalogModel):
    type: ValueType
    default: ScalarValue | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    unit: str | None = None
    options: tuple[EnumOption, ...] = ()
    separator: str | None = Field(default=None, min_length=1, max_length=1)
    repeat: bool = False
    item_type: ItemType | None = None
    path_kind: Literal["file", "directory", "any"] | None = None
    pattern: str | None = None
    special_values: tuple[str, ...] = ()
    negation: LongFlag | None = None
    negation_aliases: tuple[AnyFlag, ...] = ()
    arity: int = Field(default=1, ge=1, le=2)

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as error:
                raise ValueError(f"invalid pattern: {error}") from error
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.type == "enum" and not self.options:
            raise ValueError("enum values require options")
        if self.type != "enum" and self.options:
            raise ValueError("only enum values may define options")
        if self.type == "list" and (self.repeat == (self.separator is not None)):
            raise ValueError("list values require exactly one of separator or repeat")
        if self.type != "list" and (self.repeat or self.separator or self.item_type):
            raise ValueError("separator, repeat and itemType apply only to list values")
        if self.type != "boolean" and (self.negation or self.negation_aliases):
            raise ValueError("only boolean values may define negations")
        if self.special_values and self.type not in {"integer", "number"}:
            raise ValueError("specialValues apply only to numeric values")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum must not exceed maximum")
        if len({option.value for option in self.options}) != len(self.options):
            raise ValueError("enum option values must be unique")
        return self


class Effect(_CatalogModel):
    text: str = Field(min_length=1)
    direction: Direction
    tags: tuple[Identifier, ...] = ()


class ArgumentHelp(_CatalogModel):
    summary: str = Field(min_length=1)
    details: str | None = None
    syntax: str | None = None
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    effects: tuple[Effect, ...] = ()


class Safety(_CatalogModel):
    level: Literal["info", "warning", "danger"]
    warning: str = Field(min_length=1)
    requires_confirmation: bool = False
    sensitive: bool = False


class Deprecation(_CatalogModel):
    message: str = Field(min_length=1)
    replacement: Identifier | None = None


class Availability(_CatalogModel):
    requires_advertised_flag: bool = True


class ArgumentDefinition(_CatalogModel):
    id: Identifier
    flag: LongFlag
    aliases: tuple[AnyFlag, ...] = ()
    label: str = Field(min_length=1)
    category: Identifier
    is_advanced: bool = True
    common_rank: int | None = None
    control: Control = "editable"
    control_reason: str | None = None
    value: ValueSchema
    help: ArgumentHelp
    effect_tags: tuple[Identifier, ...] = ()
    safety: Safety | None = None
    deprecated: Deprecation | None = None
    conflicts: tuple[Identifier, ...] = ()
    requires: tuple[Identifier, ...] = ()
    availability: Availability = Availability()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.control != "editable" and not self.control_reason and self.deprecated is None:
            raise ValueError(f"{self.id}: non-editable arguments require a controlReason")
        if not self.is_advanced and self.control != "editable":
            raise ValueError(f"{self.id}: Simple mode arguments must be editable")
        return self

    @property
    def editable(self) -> bool:
        return self.control == "editable"

    @property
    def flags(self) -> tuple[str, ...]:
        """Every spelling that selects this argument, including negations."""
        negation = (self.value.negation,) if self.value.negation else ()
        return (self.flag, *self.aliases, *negation, *self.value.negation_aliases)


class Category(_CatalogModel):
    id: Identifier
    label: str = Field(min_length=1)
    sort_order: int
    description: str | None = None


class EffectTag(_CatalogModel):
    label: str = Field(min_length=1)
    color: str = Field(min_length=1)
    description: str | None = None


class CatalogSource(_CatalogModel):
    url: str = Field(min_length=1)
    retrieved: str = Field(min_length=1)
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class FlagMatch:
    """A catalog definition selected by one flag spelling."""

    definition: ArgumentDefinition
    negated: bool


class ArgumentCatalog(_CatalogModel):
    schema_uri: str | None = Field(default=None, alias="$schema")
    catalog_id: Identifier
    catalog_version: str = Field(min_length=1)
    source: CatalogSource
    categories: tuple[Category, ...]
    effect_tags: dict[Identifier, EffectTag]
    arguments: tuple[ArgumentDefinition, ...]

    _by_id: dict[str, ArgumentDefinition] = PrivateAttr(default_factory=dict)
    _by_flag: dict[str, FlagMatch] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        category_ids = [category.id for category in self.categories]
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("duplicate category id")
        by_id: dict[str, ArgumentDefinition] = {}
        by_flag: dict[str, FlagMatch] = {}
        for definition in self.arguments:
            if definition.id in by_id:
                raise ValueError(f"duplicate argument id: {definition.id}")
            by_id[definition.id] = definition
            if definition.category not in category_ids:
                raise ValueError(f"{definition.id}: unknown category {definition.category}")
            tags = set(definition.effect_tags)
            for effect in definition.help.effects:
                tags.update(effect.tags)
            unknown_tags = sorted(tags - set(self.effect_tags))
            if unknown_tags:
                raise ValueError(f"{definition.id}: unknown effect tags {', '.join(unknown_tags)}")
            negations = {definition.value.negation, *definition.value.negation_aliases}
            for flag in definition.flags:
                if flag in by_flag:
                    raise ValueError(
                        f"duplicate flag {flag}: {by_flag[flag].definition.id} and {definition.id}"
                    )
                by_flag[flag] = FlagMatch(definition, negated=flag in negations)
        for definition in self.arguments:
            references = (
                *definition.conflicts,
                *definition.requires,
                *((definition.deprecated.replacement,) if definition.deprecated else ()),
            )
            for reference in references:
                if reference is not None and reference not in by_id:
                    raise ValueError(f"{definition.id}: unknown argument reference {reference}")
        self._by_id = by_id
        self._by_flag = by_flag
        return self

    def get(self, argument_id: str) -> ArgumentDefinition | None:
        return self._by_id.get(argument_id)

    def resolve(self, flag: str) -> FlagMatch | None:
        """Resolve any spelling (long, short, alias, or negation) case-sensitively."""
        return self._by_flag.get(flag)

    def category_order(self) -> dict[str, int]:
        return {category.id: category.sort_order for category in self.categories}

    def summary(self) -> dict[str, object]:
        return {
            "catalogId": self.catalog_id,
            "catalogVersion": self.catalog_version,
            "source": self.source.model_dump(by_alias=True, exclude_none=True),
        }

    def to_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def catalog_json_schema() -> dict[str, object]:
    """Return the JSON Schema that every catalog file must satisfy."""
    schema = ArgumentCatalog.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "LlamaWebUI argument catalog"
    return schema


def load_catalog(path: Path) -> ArgumentCatalog:
    """Load and fully validate one catalog file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CatalogError(f"argument catalog could not be read: {path}: {error}") from error
    try:
        return ArgumentCatalog.model_validate(json.loads(text))
    except json.JSONDecodeError as error:
        raise CatalogError(f"argument catalog is not valid JSON: {path}: {error}") from error
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or '<root>'}: {issue['msg']}"
            for issue in error.errors()
        )
        raise CatalogError(f"argument catalog is invalid: {path}: {problems}") from error


@lru_cache(maxsize=1)
def default_catalog() -> ArgumentCatalog:
    """Return the bundled catalog, validated once per process."""
    return load_catalog(CATALOG_DIRECTORY / DEFAULT_CATALOG_FILE)
