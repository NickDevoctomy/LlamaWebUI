"""The TypeScript tools for benchmark runs: type checking with tsc, bundling with esbuild.

An agent on a TypeScript benchmark writes its modules under ``src/`` and a page
template, and ``bundle_typescript`` builds the one self-contained ``index.html`` from
them. Both tools run from the linter's install, with settings written here rather
than read from the workspace, so a planted tsconfig.json cannot change how they run.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath

from llamawebui.services.benchmark_linter import MAX_PROBLEMS, BenchmarkLinter, LinterError
from llamawebui.services.benchmark_workspace import BenchmarkWorkspace, WorkspaceError

TYPESCRIPT_EXTENSIONS = (".ts", ".mts", ".cts")
DEFAULT_ENTRY = "src/main.ts"
DEFAULT_TEMPLATE = "src/index.html"
OUTPUT = "index.html"
# Where the template wants the bundled script, replaced by an inline <script>.
BUNDLE_MARKER = "<!-- typescript-bundle -->"
CHECK_TIMEOUT_SECONDS = 120.0
BUNDLE_TIMEOUT_SECONDS = 60.0
# tsc --pretty false: path(line,col): error TS1234: message, with any detail indented
# on the lines after it.
_DIAGNOSTIC = re.compile(r"^(?P<path>.+?)\((?P<line>\d+),(?P<column>\d+)\): (?P<rest>.*)$")


def typescript_sources(workspace: BenchmarkWorkspace) -> list[str]:
    """Every TypeScript file in the workspace, by relative path."""
    return [
        path
        for path in (str(item["path"]) for item in workspace.files())
        if PurePosixPath(path).suffix.lower() in TYPESCRIPT_EXTENSIONS
    ]


def fingerprint(workspace: BenchmarkWorkspace, paths: list[str]) -> str:
    """One hash of what the bundle is built from, to tell whether it is out of date."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        file = workspace.folder / path
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(file.read_bytes() if file.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def check(linter: BenchmarkLinter, workspace: BenchmarkWorkspace, *, strict: bool) -> str:
    """Type-check every TypeScript file in the workspace; the report is for the model."""
    if not linter.typescript_installed:
        raise LinterError("TypeScript is not installed")
    sources = typescript_sources(workspace)
    if not sources:
        raise WorkspaceError(f"there are no TypeScript files yet; start with {DEFAULT_ENTRY}")
    folder = workspace.folder.resolve()
    with tempfile.TemporaryDirectory(prefix="tsc-", dir=linter.root) as scratch:
        config = Path(scratch) / "tsconfig.json"
        config.write_text(json.dumps(_tsconfig(folder, sources, strict=strict)), encoding="utf-8")
        result = linter.run_node(
            linter.tsc(), ["-p", str(config), "--pretty", "false"], folder, CHECK_TIMEOUT_SECONDS
        )
    output = (result.stdout + "\n" + result.stderr).strip()
    listed = any(_DIAGNOSTIC.match(line) for line in output.splitlines())
    if result.returncode != 0 and not listed:
        # tsc failed without listing problems, such as on a bad setting.
        raise LinterError(f"tsc failed: {_last_line(output)}")
    return _report(output, folder, strict=strict, files=len(sources))


def _tsconfig(folder: Path, sources: list[str], *, strict: bool) -> dict[str, object]:
    return {
        "compilerOptions": {
            "noEmit": True,
            "strict": strict,
            # Spelled out, so a new TypeScript default does not change what is checked.
            "noImplicitAny": strict,
            "strictNullChecks": strict,
            "target": "es2022",
            "module": "esnext",
            "moduleResolution": "bundler",
            "allowImportingTsExtensions": True,
            "lib": ["es2022", "dom", "dom.iterable"],
            "types": [],
            "skipLibCheck": True,
        },
        "files": [str(folder / source) for source in sources],
    }


def _report(output: str, folder: Path, *, strict: bool, files: int) -> str:
    mode = "strict" if strict else "non-strict"
    problems: list[str] = []
    for line in output.splitlines():
        match = _DIAGNOSTIC.match(line)
        if match is not None:
            path = _relative(match["path"], folder)
            problems.append(f"{path}:{match['line']}:{match['column']} {match['rest']}")
        elif line.startswith(" ") and problems and line.strip():
            problems[-1] += f" {line.strip()}"
    checked = f"{files} TypeScript file{'' if files == 1 else 's'}, {mode}"
    if not problems:
        return f"Checked {checked}: no type errors."
    count = len(problems)
    lines = [f"Checked {checked}: {count} error{'' if count == 1 else 's'}"]
    lines.extend(problems[:MAX_PROBLEMS])
    if len(problems) > MAX_PROBLEMS:
        lines.append(f"…and {len(problems) - MAX_PROBLEMS} more")
    return "\n".join(lines)


def _relative(path: str, folder: Path) -> str:
    try:
        return Path(path).resolve().relative_to(folder).as_posix()
    except ValueError:
        return path


def bundle(
    linter: BenchmarkLinter,
    workspace: BenchmarkWorkspace,
    entry: object = None,
    template: object = None,
) -> str:
    """Bundle the entry module and its imports into the template, as index.html."""
    if not linter.typescript_installed:
        raise LinterError("TypeScript is not installed")
    entry = entry if entry is not None else DEFAULT_ENTRY
    template = template if template is not None else DEFAULT_TEMPLATE
    entry_file = workspace.resolve(entry)
    if entry_file.suffix.lower() not in TYPESCRIPT_EXTENSIONS:
        raise WorkspaceError(f"the entry must be a TypeScript file, like {DEFAULT_ENTRY}")
    if not entry_file.is_file():
        raise WorkspaceError(f"'{entry}' does not exist")
    page = workspace.read(template)
    if page.count(BUNDLE_MARKER) != 1:
        raise WorkspaceError(
            f"'{template}' must contain {BUNDLE_MARKER} exactly once, where the script goes"
        )
    folder = workspace.folder.resolve()
    result = linter.run_node(
        linter.esbuild(),
        [
            entry_file.resolve().relative_to(folder).as_posix(),
            "--bundle",
            "--format=iife",
            "--platform=browser",
            "--target=es2022",
            "--charset=utf8",
            "--log-level=error",
            # Ignore any tsconfig.json in the workspace.
            "--tsconfig-raw={}",
        ],
        # From the workspace, so the bundle's file comments show workspace paths.
        folder,
        BUNDLE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"the bundle failed:\n{_bundle_errors(result.stderr, folder)}")
    # esbuild escapes </script> in strings already; this covers anything it left.
    script = result.stdout.replace("</script", "<\\/script")
    built = page.replace(BUNDLE_MARKER, f"<script>\n{script}</script>")
    workspace.write(OUTPUT, built)
    size = len(built.encode("utf-8"))
    return f"Bundled {entry} into {OUTPUT} from {template} ({size:,} bytes)."


def _bundle_errors(stderr: str, folder: Path) -> str:
    # esbuild's Node.js launcher adds its own stack trace after esbuild's messages.
    stderr = re.split(r"(?m)^node:(?:internal|child_process)", stderr, maxsplit=1)[0]
    text = stderr.replace(str(folder) + "\\", "").replace(folder.as_posix() + "/", "").strip()
    lines = text.splitlines()
    if len(lines) > 60:
        lines = [*lines[:60], f"…and {len(lines) - 60} more lines"]
    return "\n".join(lines) or "esbuild gave no reason"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no output"
