from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from llamawebui.app import create_app
from llamawebui.version import __version__

ROOT = Path(__file__).parents[2]


def test_all_emitted_version_metadata_matches_canonical_version() -> None:
    canonical = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    backend_project = tomllib.loads(
        (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    frontend_package = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )["version"]
    frontend_source = (ROOT / "frontend" / "src" / "version.ts").read_text(encoding="utf-8")
    frontend_version = re.search(r"APP_VERSION = ['\"]([^'\"]+)['\"]", frontend_source)

    assert __version__ == canonical
    assert backend_project == canonical
    assert frontend_package == canonical
    assert frontend_version is not None
    assert frontend_version.group(1) == canonical
    assert create_app().openapi()["info"]["version"] == canonical