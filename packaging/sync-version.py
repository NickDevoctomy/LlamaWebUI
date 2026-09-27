from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    version = args.version
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.\d+\.\d+", version):
        raise SystemExit(f"invalid semantic version: {version}")

    pyproject = args.root / "backend" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    updated, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{version}"', text, count=1)
    if count != 1:
        raise SystemExit("could not find backend project version")
    pyproject.write_text(updated, encoding="utf-8", newline="\n")

    version_module = args.root / "frontend" / "src" / "version.ts"
    version_module.write_text(
        f"export const APP_VERSION = {version!r}\n", encoding="utf-8", newline="\n"
    )

    for name in ("package.json", "package-lock.json"):
        path = args.root / "frontend" / name
        document = json.loads(path.read_text(encoding="utf-8"))
        document["version"] = version
        if name == "package-lock.json":
            document["packages"][""]["version"] = version
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
