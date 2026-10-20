"""Merge the argument catalog with the flags one runtime advertises in ``--help``."""

from __future__ import annotations

from typing import Literal

from llamawebui.domain.argument_catalog import ArgumentCatalog, ArgumentDefinition
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeOption

AvailabilityStatus = Literal["advertised", "alias", "not-advertised", "unknown"]


def argument_status(
    definition: ArgumentDefinition, capabilities: RuntimeCapabilities
) -> tuple[AvailabilityStatus, str | None]:
    """Whether the runtime advertises a catalog argument, and under which spelling.

    Advertisement is advisory only: a missing flag never blocks saving or launching.
    """
    if not capabilities.help_parsed:
        return "unknown", None
    negation = (definition.value.negation,) if definition.value.negation else ()
    primary = (definition.flag, *negation)
    if any(capabilities.supports(flag) for flag in primary):
        return "advertised", None
    alias = next(
        (
            flag
            for flag in definition.flags
            if flag.startswith("--") and capabilities.supports(flag)
        ),
        None,
    )
    if alias is not None:
        return "alias", alias
    return "not-advertised", None


def build_effective_catalog(
    catalog: ArgumentCatalog,
    capabilities: RuntimeCapabilities,
    runtime: dict[str, object],
) -> dict[str, object]:
    """Return catalog definitions annotated with runtime availability and help text."""
    details_by_flag: dict[str, RuntimeOption] = {}
    for detail in capabilities.details:
        for flag in detail.flags:
            details_by_flag.setdefault(flag, detail)

    arguments: list[dict[str, object]] = []
    matched: set[str] = set()
    counts = {"advertised": 0, "alias": 0, "not-advertised": 0, "unknown": 0}
    for definition in catalog.arguments:
        payload = definition.model_dump(mode="json", by_alias=True, exclude_none=True)
        option = next(
            (details_by_flag[flag] for flag in definition.flags if flag in details_by_flag), None
        )
        if option is not None:
            matched.add(option.flag)
        status, advertised_as = argument_status(definition, capabilities)
        if definition.editable:
            counts[status] += 1
        payload["runtime"] = {
            "status": status,
            "advertisedAs": advertised_as,
            "syntax": option.syntax if option else None,
            "help": option.help if option else None,
            "default": option.default if option else None,
        }
        arguments.append(payload)

    uncatalogued: list[dict[str, object]] = []
    if capabilities.details:
        for detail in capabilities.details:
            if detail.flag in matched or not detail.flag.startswith("--"):
                continue
            if any(catalog.resolve(flag) is not None for flag in detail.flags):
                continue
            uncatalogued.append(detail.to_payload())
    else:
        for name in sorted(capabilities.options):
            if catalog.resolve(f"--{name}") is None:
                uncatalogued.append(
                    RuntimeOption(flag=f"--{name}", aliases=(), syntax=f"--{name}", help="")
                    .to_payload()
                )

    issues: list[dict[str, object]] = []
    if not capabilities.help_parsed:
        issues.append(
            {
                "level": "warning",
                "code": "help-unparsed",
                "message": "The runtime's --help output could not be parsed, so compatibility "
                "checks are skipped. Every setting is still passed to llama-server.",
            }
        )
    elif not capabilities.details:
        issues.append(
            {
                "level": "info",
                "code": "details-missing",
                "message": "Re-probe this runtime to load its per-option syntax, help, and "
                "defaults.",
            }
        )
    if counts["not-advertised"]:
        issues.append(
            {
                "level": "info",
                "code": "not-advertised",
                "message": f"{counts['not-advertised']} catalogued arguments are not advertised "
                "by this runtime. They remain available and are passed through with a warning.",
            }
        )

    category_payload = catalog.model_dump(
        mode="json", by_alias=True, exclude_none=True, include={"categories", "effect_tags"}
    )
    return {
        **catalog.summary(),
        "categories": category_payload["categories"],
        "effectTags": category_payload["effectTags"],
        "runtime": {**runtime, "helpParsed": capabilities.help_parsed, "counts": counts},
        "arguments": arguments,
        "uncatalogued": uncatalogued,
        "issues": issues,
    }
