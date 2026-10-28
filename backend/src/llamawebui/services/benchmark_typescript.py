"""The TypeScript tools for benchmark runs: type checking with tsc, bundling with esbuild,
and unit tests with Node.js's own test runner.

An agent on a TypeScript benchmark writes its modules under ``src/`` and a page
template, and ``bundle_typescript`` builds the one self-contained ``index.html`` from
them. Every tool runs from the linter's install, with settings written here rather
than read from the workspace, so a planted tsconfig.json cannot change how they run.

``run_tests`` is the only tool that runs code the model wrote. Its tests run in one
Node.js process under the permission model, which may read only the compiled tests and
cannot write files, start processes or load add-ons; a preload script turns the network
APIs off as well. A time limit and a heap limit stop runaway tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import cast

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
TEST_TIMEOUT_SECONDS = 60.0
# Heap for a test run, in MB, so a test that allocates without end fails quickly.
TEST_HEAP_MB = 512
# Test files: src/board.test.ts, src/game.spec.ts and the like.
_TEST_FILE = re.compile(r"\.(test|spec)\.(ts|mts|cts)$", re.IGNORECASE)
# Failures listed in full; the rest are counted.
MAX_FAILURES = 15
# Lines a test printed itself, kept after the results.
MAX_TEST_OUTPUT_LINES = 20
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
        types = Path(scratch) / "node-test.d.ts"
        types.write_text(NODE_TEST_TYPES, encoding="utf-8")
        settings = _tsconfig(folder, sources, strict=strict)
        cast(list[str], settings["files"]).append(str(types))
        config = Path(scratch) / "tsconfig.json"
        config.write_text(json.dumps(settings), encoding="utf-8")
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


# Types for the parts of node:test and node:assert a unit test uses, so check_typescript
# accepts test files without the whole of @types/node.
NODE_TEST_TYPES = """
declare module 'node:test' {
  type Body = (context: unknown) => void | Promise<void>;
  type Hook = () => void | Promise<void>;
  export function test(name: string, fn: Body): Promise<void>;
  export function test(name: string, options: object, fn: Body): Promise<void>;
  export function describe(name: string, fn: () => void | Promise<void>): Promise<void>;
  export const it: typeof test;
  export const suite: typeof describe;
  export function before(fn: Hook): void;
  export function after(fn: Hook): void;
  export function beforeEach(fn: Hook): void;
  export function afterEach(fn: Hook): void;
  export default test;
}
interface NodeAssert {
  (value: unknown, message?: string): asserts value;
  ok(value: unknown, message?: string): asserts value;
  equal(actual: unknown, expected: unknown, message?: string): void;
  notEqual(actual: unknown, expected: unknown, message?: string): void;
  strictEqual(actual: unknown, expected: unknown, message?: string): void;
  notStrictEqual(actual: unknown, expected: unknown, message?: string): void;
  deepEqual(actual: unknown, expected: unknown, message?: string): void;
  notDeepEqual(actual: unknown, expected: unknown, message?: string): void;
  deepStrictEqual(actual: unknown, expected: unknown, message?: string): void;
  notDeepStrictEqual(actual: unknown, expected: unknown, message?: string): void;
  throws(fn: () => unknown, expected?: unknown, message?: string): void;
  doesNotThrow(fn: () => unknown, message?: string): void;
  rejects(
    fn: Promise<unknown> | (() => Promise<unknown>), expected?: unknown, message?: string
  ): Promise<void>;
  match(value: string, pattern: RegExp, message?: string): void;
  fail(message?: string): never;
  strict: NodeAssert;
}
declare module 'node:assert' {
  const assert: NodeAssert;
  export default assert;
  export const ok: NodeAssert['ok'];
  export const equal: NodeAssert['equal'];
  export const notEqual: NodeAssert['notEqual'];
  export const strictEqual: NodeAssert['strictEqual'];
  export const notStrictEqual: NodeAssert['notStrictEqual'];
  export const deepEqual: NodeAssert['deepEqual'];
  export const notDeepEqual: NodeAssert['notDeepEqual'];
  export const deepStrictEqual: NodeAssert['deepStrictEqual'];
  export const notDeepStrictEqual: NodeAssert['notDeepStrictEqual'];
  export const throws: NodeAssert['throws'];
  export const doesNotThrow: NodeAssert['doesNotThrow'];
  export const rejects: NodeAssert['rejects'];
  export const match: NodeAssert['match'];
  export const fail: NodeAssert['fail'];
  export const strict: NodeAssert;
}
declare module 'node:assert/strict' {
  import assert from 'node:assert';
  export * from 'node:assert';
  export default assert;
}
"""


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


def test_files(workspace: BenchmarkWorkspace) -> list[str]:
    """The workspace's unit test files, such as src/board.test.ts."""
    return [path for path in typescript_sources(workspace) if _TEST_FILE.search(path)]


# Loaded before the tests: the permission model does not cover the network, so this
# turns the network APIs off in the test process.
_NO_NETWORK = """
import net from 'node:net';
import tls from 'node:tls';
import dgram from 'node:dgram';
const refuse = () => { throw new Error('the network is not available to tests'); };
for (const name of ['fetch', 'WebSocket', 'EventSource']) {
  Object.defineProperty(globalThis, name, { value: refuse, writable: false, configurable: false });
}
net.Socket.prototype.connect = refuse;
net.connect = net.createConnection = refuse;
tls.connect = refuse;
dgram.createSocket = refuse;
"""


def run_tests(
    linter: BenchmarkLinter, workspace: BenchmarkWorkspace
) -> tuple[str, dict[str, int]]:
    """Run the workspace's unit tests; the report for the model, and the counts."""
    if not linter.typescript_installed:
        raise LinterError("TypeScript is not installed")
    tests = test_files(workspace)
    if not tests:
        raise WorkspaceError(
            "there are no tests yet. Write them as src/<name>.test.ts with node:test and "
            "node:assert/strict, importing the modules they test"
        )
    folder = workspace.folder.resolve()
    with tempfile.TemporaryDirectory(prefix="tests-", dir=linter.root) as scratch:
        out = Path(scratch).resolve()
        built = linter.run_node(
            linter.esbuild(),
            [
                *tests,
                "--bundle",
                "--platform=node",
                "--format=esm",
                "--target=node22",
                "--sourcemap",
                "--charset=utf8",
                "--log-level=error",
                f"--outdir={out}",
                "--outbase=.",
                "--out-extension:.js=.mjs",
                "--tsconfig-raw={}",
            ],
            folder,
            BUNDLE_TIMEOUT_SECONDS,
        )
        if built.returncode != 0:
            raise WorkspaceError(
                f"the tests did not build:\n{_bundle_errors(built.stderr, folder)}"
            )
        preload = out / "no-network.mjs"
        preload.write_text(_NO_NETWORK, encoding="utf-8")
        # Relative and with forward slashes: node --test reads its file arguments as globs.
        compiled = [PurePosixPath(test).with_suffix(".mjs").as_posix() for test in tests]
        try:
            result = linter.run_node_with(
                [
                    "--permission",
                    f"--allow-fs-read={out}",
                    f"--max-old-space-size={TEST_HEAP_MB}",
                    "--enable-source-maps",
                    f"--import={preload.as_uri()}",
                    "--test",
                    # One process, so the time limit stops every test with it.
                    "--test-isolation=none",
                    "--test-reporter=tap",
                    *compiled,
                ],
                out,
                TEST_TIMEOUT_SECONDS,
            )
        except LinterError as error:
            if isinstance(error.__cause__, subprocess.TimeoutExpired):
                raise WorkspaceError(
                    f"the tests took longer than {TEST_TIMEOUT_SECONDS:.0f} s and were stopped; "
                    "look for a loop that never ends or a test that waits forever"
                ) from error
            raise
    return _test_report(result.stdout + "\n" + result.stderr, folder, out, files=len(tests))


_TAP_RESULT = re.compile(r"^(?P<indent>\s*)(?P<status>ok|not ok) \d+ - (?P<name>.*?)(?: # .*)?$")
_TAP_COUNT = re.compile(r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)$")
_TAP_NOISE = re.compile(r"^\s*(TAP version|# Subtest:|1\.\.\d+|# (suites|duration_ms) )")


def _test_report(
    output: str, folder: Path, out: Path, *, files: int
) -> tuple[str, dict[str, int]]:
    """The TAP report cut down to the counts, each failure and anything the tests printed."""
    lines = output.splitlines()
    counts: dict[str, int] = {}
    failures: list[str] = []
    printed: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        count = _TAP_COUNT.match(line)
        if count is not None:
            counts[count[1]] = int(count[2])
            continue
        result = _TAP_RESULT.match(line)
        if result is not None:
            # Each result is followed by its details between --- and ... lines.
            details: list[str] = []
            if index < len(lines) and lines[index].strip() == "---":
                index += 1
                while index < len(lines) and lines[index].strip() != "...":
                    details.append(lines[index])
                    index += 1
                index += 1
            if result["status"] == "not ok":
                failure = _failure(result["name"], result["indent"], details, folder, out)
                if failure is not None:
                    failures.append(failure)
            continue
        if line.strip() and not _TAP_NOISE.match(line):
            printed.append(line.rstrip())
    total = counts.get("tests", 0)
    failed = counts.get("fail", 0) + counts.get("cancelled", 0)
    passed = counts.get("pass", 0)
    if not total and not failures:
        # Nothing ran: an import failed or the file crashed before any test.
        reason = _clean_paths("\n".join(printed[-MAX_TEST_OUTPUT_LINES:]), folder, out)
        raise WorkspaceError(f"the tests could not run:\n{reason or 'Node.js gave no reason'}")
    checked = f"{files} test file{'' if files == 1 else 's'}"
    head = f"Ran {total} test{'' if total == 1 else 's'} in {checked}: {passed} passed"
    head += f", {failed} failed." if failed else "."
    report = [head]
    report.extend(failures[:MAX_FAILURES])
    if len(failures) > MAX_FAILURES:
        report.append(f"…and {len(failures) - MAX_FAILURES} more failures")
    if printed:
        shown = printed[:MAX_TEST_OUTPUT_LINES]
        more = len(printed) - len(shown)
        text = _clean_paths("\n".join(shown), folder, out)
        report.append(f"Output:\n{text}" + (f"\n…and {more} more lines" if more else ""))
    return "\n".join(report), {"passed": passed, "failed": failed}


def _failure(
    name: str, indent: str, details: list[str], folder: Path, out: Path
) -> str | None:
    """One failed test as the model reads it, or None for a suite that failed by its tests."""
    fields: dict[str, str] = {}
    key: str | None = None
    # Each detail field sits two spaces in from its result line.
    field = re.compile(rf"^{re.escape(indent)}  ([a-zA-Z_]+):(?: (.*))?$")
    for line in details:
        match = field.match(line)
        if match is not None:
            key = match[1]
            value = (match[2] or "").strip()
            fields[key] = "" if value in ("|-", "|", ">-", ">") else value.strip("'")
        elif key is not None:
            fields[key] = (fields[key] + "\n" + line.strip()).strip()
    if fields.get("failureType") == "subtestsFailed":
        return None
    location = fields.get("location", "").replace("\\\\", "\\")
    where = _clean_paths(location, folder, out).replace("\\", "/")
    error = _clean_paths(fields.get("error", "failed"), folder, out)
    error_lines = [line for line in error.splitlines() if line.strip()][:12]
    label = f"FAIL {name}" + (f" ({where})" if where else "")
    return "\n".join([label, *(f"  {line}" for line in error_lines)])


def _clean_paths(text: str, folder: Path, out: Path) -> str:
    """Workspace-relative paths in place of the absolute ones Node.js reports."""
    for base in (folder, out):
        for spelling in (str(base) + "\\", base.as_posix() + "/"):
            text = text.replace(spelling, "")
    return text
