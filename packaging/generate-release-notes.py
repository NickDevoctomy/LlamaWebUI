from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--changelog", type=Path, default=Path("changelog.json"))
    parser.add_argument("--output", type=Path, default=Path("release-notes.md"))
    args = parser.parse_args()

    version = args.tag.removeprefix("v")
    document = json.loads(args.changelog.read_text(encoding="utf-8"))
    entries = [entry for entry in document["versions"] if entry["version"] == version]
    if len(entries) != 1:
        raise SystemExit(f"expected exactly one changelog entry for {args.tag}")
    entry = entries[0]

    lines = [f"# LlamaWebUI {args.tag}", "", f"Released: {entry['release_date']}", ""]
    grouped: dict[str, list[str]] = {}
    for change in entry["changes"]:
        grouped.setdefault(change["type"], []).append(change["description"])
    for change_type, descriptions in grouped.items():
        lines.extend([f"## {change_type}", ""])
        lines.extend(f"- {description}" for description in descriptions)
        lines.append("")
    args.output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
