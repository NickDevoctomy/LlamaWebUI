"""Command-line entry point for development and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from llamawebui.services.runtime_probe import RuntimeProbeResult, probe_runtime


def _probe_payload(result: RuntimeProbeResult) -> dict[str, Any]:
    return {
        "executable": str(result.executable),
        "usable": result.usable,
        "version": {
            "build": result.version.build,
            "commit": result.version.commit,
        },
        "options": sorted(result.capabilities.options),
        "devices": result.devices_output,
        "errors": list(result.errors),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llamawebui")
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_parser = subparsers.add_parser("probe-runtime", help="inspect a llama-server executable")
    probe_parser.add_argument("executable", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "probe-runtime":
        try:
            result = asyncio.run(probe_runtime(args.executable))
        except FileNotFoundError as error:
            print(json.dumps({"usable": False, "errors": [str(error)]}, indent=2))
            return 2

        print(json.dumps(_probe_payload(result), indent=2))
        return 0 if result.usable else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
