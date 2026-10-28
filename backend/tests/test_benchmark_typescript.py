from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from test_benchmark_linter import FakeCommands, _install, _linter
from test_benchmark_tools import _round, _tool_call
from test_benchmarks import BENCHMARK, FakeBenchmarkClient, _database, _runner

from llamawebui.domain.benchmark import BenchmarkError, load_benchmarks
from llamawebui.services import benchmark_typescript
from llamawebui.services.benchmark_linter import BenchmarkLinter, CommandResult, LinterError
from llamawebui.services.benchmark_runner import (
    TOOL_NAMES,
    _call_tool,
    _offered,
    choose_tools,
    tool_system_prompt,
)
from llamawebui.services.benchmark_workspace import BenchmarkWorkspace, WorkspaceError

TEMPLATE = "<!doctype html><title>Game</title>\n<!-- typescript-bundle -->\n"


class FakeToolchain(FakeCommands):
    """npm installs everything; tsc and esbuild give set replies and record their calls."""

    def __init__(self) -> None:
        super().__init__()
        self.tsc = CommandResult(0, "", "")
        self.esbuild = CommandResult(0, 'console.log("</script>");\n', "")
        self.configs: list[dict[str, object]] = []

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
        stdin: str | None,
    ) -> CommandResult:
        if "install" in command:
            return super().__call__(command, cwd, env, timeout, stdin)
        self.calls.append((command, cwd, env, stdin))
        script = Path(command[1]).name
        if script == "tsc":
            self.configs.append(json.loads(Path(command[3]).read_text(encoding="utf-8")))
            return self.tsc
        if script == "esbuild":
            return self.esbuild
        return self.eslint


async def _toolchain(tmp_path: Path) -> tuple[BenchmarkLinter, FakeToolchain]:
    commands = FakeToolchain()
    linter = _linter(tmp_path, commands)
    await _install(linter)
    assert linter.typescript_installed
    return linter, commands


def _workspace(tmp_path: Path) -> BenchmarkWorkspace:
    workspace = BenchmarkWorkspace(tmp_path / "run")
    workspace.write("src/main.ts", "import { add } from './util'\nconsole.log(add(1, 2))\n")
    workspace.write("src/util.ts", "export const add = (a: number, b: number) => a + b\n")
    workspace.write("src/index.html", TEMPLATE)
    return workspace


def test_frontmatter_makes_a_typescript_benchmark(tmp_path: Path) -> None:
    for name, setting in (
        ("plain", ""),
        ("on", "typescript: true\n"),
        ("empty", "typescript: {}\n"),
        ("strict", "typescript:\n  strict: true\n"),
        ("off", "typescript: false\n"),
    ):
        (tmp_path / f"{name}.txt").write_text(f"---\n{setting}---\nMake it.", encoding="utf-8")
    loaded = {item.id: item for item in load_benchmarks(tmp_path)}
    names = ("plain", "on", "empty", "strict", "off")
    assert [loaded[name].typescript_strict for name in names] == [None, False, False, True, None]
    assert loaded["strict"].typescript
    assert loaded["strict"].to_payload()["typescript"] == {"strict": True, "tests": False}
    assert loaded["plain"].to_payload()["typescript"] is None


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ("typescript: yes please", "typescript must be true or a mapping"),
        ("typescript: {strict: true, target: es5}", "unknown typescript settings: target"),
        ("typescript: {strict: maybe}", "typescript strict must be true or false"),
    ],
)
def test_bad_typescript_settings_are_refused(tmp_path: Path, setting: str, message: str) -> None:
    (tmp_path / "bad.txt").write_text(f"---\n{setting}\n---\nx", encoding="utf-8")
    with pytest.raises(BenchmarkError, match=f"bad.txt: {message}"):
        load_benchmarks(tmp_path)


def test_an_install_from_before_typescript_is_outdated(tmp_path: Path) -> None:
    root = tmp_path / "tools" / "eslint"
    for file in ("node/node.exe", "node_modules/eslint/bin/eslint.js"):
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_text("x", encoding="utf-8")
    (root / "install.json").write_text(
        json.dumps({"versions": {}, "installed_at": "then"}), encoding="utf-8"
    )
    linter = _linter(tmp_path, FakeCommands())
    status = linter.status()
    assert (status["state"], status["typescript"], status["outdated"]) == (
        "installed",
        False,
        True,
    )
    # Installing again is how an outdated install gets the TypeScript tools.
    assert linter.outdated and not linter.typescript_installed


@pytest.mark.asyncio
async def test_check_typescript_reports_errors_against_workspace_paths(tmp_path: Path) -> None:
    linter, commands = await _toolchain(tmp_path)
    workspace = _workspace(tmp_path)
    folder = workspace.folder.resolve()
    commands.tsc = CommandResult(
        2,
        f"{folder / 'src' / 'main.ts'}(2,13): error TS2551: Property 'widht' does not exist.\n"
        "  Did you mean 'width'?\n"
        f"{tmp_path / 'elsewhere.ts'}(1,1): error TS1005: ';' expected.\n",
        "",
    )

    report = benchmark_typescript.check(linter, workspace, strict=True)

    assert report.splitlines() == [
        "Checked 2 TypeScript files, strict: 2 errors",
        "src/main.ts:2:13 error TS2551: Property 'widht' does not exist. Did you mean 'width'?",
        f"{tmp_path / 'elsewhere.ts'}:1:1 error TS1005: ';' expected.",
    ]
    config = commands.configs[-1]
    options = cast(dict[str, object], config["compilerOptions"])
    assert (options["strict"], options["noImplicitAny"], options["noEmit"]) == (True, True, True)
    files = cast(list[str], config["files"])
    # The workspace's files, and the types for node:test and node:assert test files use.
    assert sorted(files[:-1]) == [str(folder / "src/main.ts"), str(folder / "src/util.ts")]
    assert files[-1].endswith("node-test.d.ts")
    # tsc runs from the workspace with a config written outside it, then cleaned up.
    command, cwd, _, _ = commands.calls[-1]
    assert cwd == folder
    assert not Path(command[3]).exists()

    commands.tsc = CommandResult(0, "", "")
    assert benchmark_typescript.check(linter, workspace, strict=False) == (
        "Checked 2 TypeScript files, non-strict: no type errors."
    )
    commands.tsc = CommandResult(1, "", "error TS5023: Unknown compiler option.")
    with pytest.raises(LinterError, match="tsc failed: error TS5023"):
        benchmark_typescript.check(linter, workspace, strict=False)


@pytest.mark.asyncio
async def test_check_typescript_lists_at_most_the_problem_limit(tmp_path: Path) -> None:
    linter, commands = await _toolchain(tmp_path)
    workspace = _workspace(tmp_path)
    main = workspace.folder.resolve() / "src" / "main.ts"
    commands.tsc = CommandResult(
        2, "".join(f"{main}({line},1): error TS1: bad.\n" for line in range(1, 41)), ""
    )
    report = benchmark_typescript.check(linter, workspace, strict=False).splitlines()
    assert report[0] == "Checked 2 TypeScript files, non-strict: 40 errors"
    assert report[-1] == "…and 10 more"


@pytest.mark.asyncio
async def test_check_typescript_needs_sources_and_the_install(tmp_path: Path) -> None:
    linter, _ = await _toolchain(tmp_path)
    empty = BenchmarkWorkspace(tmp_path / "empty")
    with pytest.raises(WorkspaceError, match="no TypeScript files yet"):
        benchmark_typescript.check(linter, empty, strict=False)
    missing = _linter(tmp_path / "other", FakeCommands())
    with pytest.raises(LinterError, match="TypeScript is not installed"):
        benchmark_typescript.check(missing, empty, strict=False)
    with pytest.raises(LinterError, match="TypeScript is not installed"):
        benchmark_typescript.bundle(missing, empty)


@pytest.mark.asyncio
async def test_bundle_typescript_writes_the_page_from_the_template(tmp_path: Path) -> None:
    linter, commands = await _toolchain(tmp_path)
    workspace = _workspace(tmp_path)

    result = benchmark_typescript.bundle(linter, workspace)

    page = workspace.read("index.html")
    assert result == (
        f"Bundled src/main.ts into index.html from src/index.html ({len(page):,} bytes)."
    )
    assert page == (
        "<!doctype html><title>Game</title>\n"
        '<script>\nconsole.log("<\\/script>");\n</script>\n'
    )
    command, cwd, _, _ = commands.calls[-1]
    assert cwd == workspace.folder.resolve()
    assert command[2:] == [
        "src/main.ts",
        "--bundle",
        "--format=iife",
        "--platform=browser",
        "--target=es2022",
        "--charset=utf8",
        "--log-level=error",
        "--tsconfig-raw={}",
    ]

    folder = workspace.folder.resolve()
    stderr = f"✘ [ERROR] Could not resolve './nope'\n\n    {folder}\\src\\main.ts:1:20:"
    commands.esbuild = CommandResult(1, "", stderr)
    failure = r"the bundle failed:\n✘ \[ERROR\] Could not resolve"
    with pytest.raises(WorkspaceError, match=failure) as failed:
        benchmark_typescript.bundle(linter, workspace)
    assert str(folder) not in str(failed.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "template", "message"),
    [
        ("src/main.js", None, "the entry must be a TypeScript file"),
        ("src/missing.ts", None, "'src/missing.ts' does not exist"),
        (None, "src/plain.html", "must contain <!-- typescript-bundle --> exactly once"),
    ],
)
async def test_bundle_typescript_refuses_bad_inputs(
    tmp_path: Path, entry: str | None, template: str | None, message: str
) -> None:
    linter, _ = await _toolchain(tmp_path)
    workspace = _workspace(tmp_path)
    workspace.write("src/main.js", "x")
    workspace.write("src/plain.html", "<p>no marker</p>")
    with pytest.raises(WorkspaceError, match=message):
        benchmark_typescript.bundle(linter, workspace, entry, template)


def test_the_typescript_tools_are_only_for_typescript_benchmarks() -> None:
    every = list(TOOL_NAMES)
    with pytest.raises(ValueError, match="check_typescript only work on a TypeScript benchmark"):
        choose_tools(["write_file", "check_typescript"], every)
    without = [name for name in every if not name.endswith("_typescript")]
    with pytest.raises(ValueError, match="TypeScript tools are not installed"):
        choose_tools(None, without, typescript=True)
    with pytest.raises(ValueError, match="TypeScript tools are not installed"):
        choose_tools(["bundle_typescript"], without, typescript=True)
    # Left out on purpose, a TypeScript benchmark can still run with plain tools.
    assert choose_tools(["write_file"], without, typescript=True) == ["write_file"]

    note = tool_system_prompt(["write_file", "check_typescript", "bundle_typescript", "finish_job"])
    assert "This is a TypeScript job." in note
    assert "Run check_typescript after changing the TypeScript." in note
    assert "refused until index.html is built from the latest TypeScript" in note
    assert "TypeScript job" not in tool_system_prompt(["write_file", "finish_job"])

    def description(strict: bool) -> str:
        (tool,) = _offered(["check_typescript"], strict=strict)
        return str(cast(dict[str, object], tool["function"])["description"])

    assert "in strict mode" in description(True)
    assert "in non-strict mode" in description(False)


@pytest.mark.asyncio
async def test_finish_job_waits_for_a_current_typescript_build(tmp_path: Path) -> None:
    linter, _ = await _toolchain(tmp_path)
    workspace = BenchmarkWorkspace(tmp_path / "run")
    given = ["write_file", "edit_file", "bundle_typescript", "finish_job"]
    build: dict[str, object] = {}

    def call(name: str, arguments: dict[str, object]) -> tuple[str, dict[str, object]]:
        return _call_tool(workspace, name, json.dumps(arguments), given, linter, [], build)

    finish = {"final_message": "Done."}
    result, entry = call("finish_job", finish)
    assert entry["ok"] is False
    assert "there is no TypeScript yet" in result

    _workspace(tmp_path)
    result, _ = call("finish_job", finish)
    assert "index.html has not been built" in result

    result, entry = call("bundle_typescript", {})
    assert entry["ok"] is True, result
    assert call("finish_job", finish)[1]["ok"] is True

    call("edit_file", {"path": "src/util.ts", "old_string": "a + b", "new_string": "b + a"})
    result, entry = call("finish_job", finish)
    assert entry["ok"] is False
    assert "changed after the last build" in result
    # Writing the page by hand counts as a change too.
    call("bundle_typescript", {})
    call("write_file", {"path": "index.html", "content": "<p>by hand</p>"})
    assert "changed after the last build" in call("finish_job", finish)[0]


@pytest.mark.asyncio
async def test_a_typescript_run_keeps_its_strict_mode(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    linter, _ = await _toolchain(tmp_path / "tools-home")
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [_tool_call(0, "check_typescript", {})],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)
    runner._linter = linter
    strict = replace(BENCHMARK, typescript_strict=True)

    record = runner.start(profile, strict, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert finished.typescript_strict is True
    assert "check_typescript" in (finished.tool_names or [])
    # No TypeScript written, so the check itself says what to do.
    assert "no TypeScript files yet" in str(finished.tool_calls[0]["result"])
    _, offered = client.requests[0]
    assert "bundle_typescript" in offered

    plain = runner.start(profile, BENCHMARK, None, tools=True)
    await runner.wait()
    assert runs.get(plain.id).typescript_strict is None
    assert "check_typescript" not in (runs.get(plain.id).tool_names or [])


TAP = """TAP version 13
# Subtest: board
    # Subtest: clears full rows
    ok 1 - clears full rows
      ---
      duration_ms: 0.6
      type: 'test'
      ...
    # Subtest: scores a tetris
    not ok 2 - scores a tetris
      ---
      duration_ms: 3.1
      type: 'test'
      location: '{folder}\\src\\board.test.ts:6:3'
      failureType: 'testCodeFailure'
      error: |-
        Expected values to be strictly equal:

        800 !== 400

      code: 'ERR_ASSERTION'
      stack: |-
        TestContext.<anonymous> ({folder}\\src\\board.test.ts:6:31)
      ...
    1..2
not ok 1 - board
  ---
  duration_ms: 4.0
  type: 'suite'
  location: '{folder}\\src\\board.test.ts:4:1'
  failureType: 'subtestsFailed'
  error: '1 subtest failed'
  ...
hello from a test
1..1
# tests 2
# suites 1
# pass 1
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 12.3
"""


class FakeTestRunner(FakeToolchain):
    """Node's test runner gives a set TAP reply; the run's options are recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.tests = CommandResult(0, "", "")
        self.timeout = False

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
        stdin: str | None,
    ) -> CommandResult:
        if "--test" not in command:
            return super().__call__(command, cwd, env, timeout, stdin)
        self.calls.append((command, cwd, env, stdin))
        # The preload that turns the network off is written beside the compiled tests.
        assert (cwd / "no-network.mjs").read_text(encoding="utf-8").count("refuse") > 1
        if self.timeout:
            import subprocess

            raise LinterError("node took too long") from subprocess.TimeoutExpired(command, 60)
        return self.tests


async def _test_runner(tmp_path: Path) -> tuple[BenchmarkLinter, FakeTestRunner]:
    commands = FakeTestRunner()
    linter = _linter(tmp_path, commands)
    await _install(linter)
    return linter, commands


@pytest.mark.asyncio
async def test_run_tests_reports_counts_failures_and_output(tmp_path: Path) -> None:
    linter, commands = await _test_runner(tmp_path / "home")
    workspace = _workspace(tmp_path)
    workspace.write("src/board.test.ts", "import { test } from 'node:test'\n")
    folder = workspace.folder.resolve()
    commands.tests = CommandResult(1, TAP.format(folder=str(folder).replace("\\", "\\\\")), "")

    report, counts = benchmark_typescript.run_tests(linter, workspace)

    assert counts == {"passed": 1, "failed": 1}
    assert report.splitlines() == [
        "Ran 2 tests in 1 test file: 1 passed, 1 failed.",
        "FAIL scores a tetris (src/board.test.ts:6:3)",
        "  Expected values to be strictly equal:",
        "  800 !== 400",
        "Output:",
        "hello from a test",
    ]
    build, _, _, _ = commands.calls[-2]
    assert Path(build[1]).name == "esbuild"
    assert "src/board.test.ts" in build and "--platform=node" in build
    run, cwd, _, _ = commands.calls[-1]
    # One locked-down process that may only read the compiled tests.
    assert "--permission" in run and f"--allow-fs-read={cwd}" in run
    assert "--test-isolation=none" in run
    assert run[-1] == "src/board.test.mjs"
    assert any(option.startswith("--max-old-space-size=") for option in run)
    # The compiled tests are cleaned up afterwards.
    assert not cwd.exists()


@pytest.mark.asyncio
async def test_run_tests_says_why_nothing_ran(tmp_path: Path) -> None:
    linter, commands = await _test_runner(tmp_path / "home")
    workspace = _workspace(tmp_path)

    with pytest.raises(WorkspaceError, match="there are no tests yet"):
        benchmark_typescript.run_tests(linter, workspace)

    workspace.write("src/board.spec.ts", "nope")
    commands.esbuild = CommandResult(1, "", "✘ [ERROR] Expected ';'\n")
    with pytest.raises(WorkspaceError, match="the tests did not build"):
        benchmark_typescript.run_tests(linter, workspace)

    commands.esbuild = CommandResult(0, "", "")
    commands.tests = CommandResult(1, "", "SyntaxError: Unexpected token\n")
    with pytest.raises(WorkspaceError, match="could not run:\nSyntaxError: Unexpected token"):
        benchmark_typescript.run_tests(linter, workspace)

    commands.timeout = True
    with pytest.raises(WorkspaceError, match="took longer than 60 s"):
        benchmark_typescript.run_tests(linter, workspace)


@pytest.mark.asyncio
async def test_a_benchmark_with_tests_offers_run_tests_and_counts_the_runs(
    tmp_path: Path,
) -> None:
    engine, profile = _database(tmp_path)
    linter, commands = await _test_runner(tmp_path / "tools-home")
    commands.tests = CommandResult(0, "# tests 3\n# pass 3\n# fail 0\n", "")
    client = FakeBenchmarkClient()
    client.replies = [
        _round(
            [
                _tool_call(0, "write_file", {"path": "src/a.test.ts", "content": "x"}),
                _tool_call(1, "run_tests", {}),
            ],
            finish="tool_calls",
            generated=5,
            milliseconds=10.0,
        ),
        _round([], finish="stop", generated=1, milliseconds=1.0),
    ]
    runner, runs, _ = _runner(engine, client)
    runner._linter = linter
    tested = replace(BENCHMARK, typescript_strict=False, typescript_tests=True)

    record = runner.start(profile, tested, None, tools=True)
    await runner.wait()

    finished = runs.get(record.id)
    assert "run_tests" in (finished.tool_names or [])
    assert finished.tool_calls[1]["result"] == "Ran 3 tests in 1 test file: 3 passed."
    assert finished.tool_calls[1]["tests"] == {"passed": 3, "failed": 0}
    assert (
        finished.stats["test_runs"],
        finished.stats["tests_passed"],
        finished.stats["tests_failed"],
    ) == (1, 3, 0)
    messages, offered = client.requests[0]
    assert "run_tests" in offered
    assert "node:test" in str(messages[0]["content"])

    # A TypeScript benchmark without tests turned on does not offer them.
    plain = runner.start(profile, replace(BENCHMARK, typescript_strict=False), None, tools=True)
    await runner.wait()
    assert "run_tests" not in (runs.get(plain.id).tool_names or [])


def test_frontmatter_turns_tests_on(tmp_path: Path) -> None:
    (tmp_path / "tested.txt").write_text(
        "---\ntypescript:\n  tests: true\n---\nMake it.", encoding="utf-8"
    )
    (tmp_path / "bad.txt").write_text(
        "---\ntypescript:\n  tests: maybe\n---\nMake it.", encoding="utf-8"
    )
    with pytest.raises(BenchmarkError, match="typescript tests must be true or false"):
        load_benchmarks(tmp_path)
    (tmp_path / "bad.txt").unlink()
    tested = load_benchmarks(tmp_path)[0]
    assert (tested.typescript, tested.typescript_tests) == (True, True)
    assert tested.to_payload()["typescript"] == {"strict": False, "tests": True}
