import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
GENERATOR = ROOT / "packaging" / "generate-release-notes.py"

def test_release_notes_generator_uses_matching_changelog_entry(tmp_path: Path) -> None:
    changelog = tmp_path / "changelog.json"
    output = tmp_path / "notes.md"
    changelog.write_text(
        json.dumps(
            {
                "versions": [
                    {
                        "version": "1.2.3",
                        "release_date": "2026-09-26",
                        "changes": [
                            {"type": "Added", "description": "New feature."},
                            {"type": "Fixed", "description": "Corrected issue."},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--tag",
            "v1.2.3",
            "--changelog",
            str(changelog),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    text = output.read_text(encoding="utf-8")
    assert "# LlamaWebUI v1.2.3" in text
    assert "## Added" in text
    assert "- New feature." in text
    assert "## Fixed" in text


def test_release_notes_generator_rejects_undocumented_tag(tmp_path: Path) -> None:
    changelog = tmp_path / "changelog.json"
    changelog.write_text('{"versions": []}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--tag",
            "v9.9.9",
            "--changelog",
            str(changelog),
            "--output",
            str(tmp_path / "notes.md"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "expected exactly one" in result.stderr
