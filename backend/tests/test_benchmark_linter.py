from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
import pytest
from conftest import Login
from fastapi.testclient import TestClient
from test_benchmarks import BENCHMARK

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.services import benchmark_linter
from llamawebui.services.authorization import privilege_for_request
from llamawebui.services.benchmark_linter import (
    MAX_PROBLEMS,
    NODE_VERSION,
    PACKAGES,
    BenchmarkLinter,
    CommandResult,
    LinterError,
    node_asset,
    report,
    run_command,
)

WIN_ZIP = f"node-v{NODE_VERSION}-win-x64.zip"
LINUX_TAR = f"node-v{NODE_VERSION}-linux-x64.tar.gz"


def _node_zip(top: str = f"node-v{NODE_VERSION}-win-x64") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{top}/", "")
        archive.writestr(f"{top}/node.exe", "node")
        archive.writestr(f"{top}/node_modules/npm/bin/npm-cli.js", "npm")
    return buffer.getvalue()


def _node_tar() -> bytes:
    buffer = io.BytesIO()
    top = f"node-v{NODE_VERSION}-linux-x64"
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, mode in (
            (f"{top}/bin/node", b"node", 0o755),
            (f"{top}/lib/node_modules/npm/bin/npm-cli.js", b"npm", 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            archive.addfile(info, io.BytesIO(data))
        folder = tarfile.TarInfo(f"{top}/include")
        folder.type = tarfile.DIRTYPE
        archive.addfile(folder)
    return buffer.getvalue()


class FakeDownloads:
    def __init__(self, files: dict[str, bytes], *, sums: str | None = None) -> None:
        self.files = files
        self.sums = sums
        self.urls: list[str] = []

    async def __call__(
        self, url: str, destination: Path, progress: Callable[[int, int | None], None]
    ) -> None:
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name == "SHASUMS256.txt":
            data = (self.sums if self.sums is not None else self._sums()).encode()
        else:
            data = self.files[name]
        destination.write_bytes(data)
        progress(len(data), len(data))

    def _sums(self) -> str:
        return "".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in self.files.items()
        )


class FakeCommands:
    """Stands in for Node.js: npm installs create ESLint; ESLint returns a set reply."""

    def __init__(self, *, npm_code: int = 0, eslint: CommandResult | None = None) -> None:
        self.npm_code = npm_code
        self.eslint = eslint or CommandResult(0, "[]", "")
        self.calls: list[tuple[list[str], Path, Mapping[str, str], str | None]] = []

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
        stdin: str | None,
    ) -> CommandResult:
        self.calls.append((command, cwd, env, stdin))
        if "install" in command:
            if self.npm_code:
                return CommandResult(self.npm_code, "", "npm ERR! network\nnpm ERR! offline")
            eslint = cwd / "node_modules" / "eslint" / "bin" / "eslint.js"
            eslint.parent.mkdir(parents=True)
            eslint.write_text("eslint", encoding="utf-8")
            return CommandResult(0, "", "")
        return self.eslint


def _linter(
    tmp_path: Path,
    commands: FakeCommands,
    downloads: FakeDownloads | None = None,
    *,
    platform: str = "win32",
) -> BenchmarkLinter:
    return BenchmarkLinter(
        tmp_path,
        downloader=downloads or FakeDownloads({WIN_ZIP: _node_zip()}),
        runner=commands,
        platform=platform,
        machine="AMD64" if platform == "win32" else "x86_64",
    )


async def _install(linter: BenchmarkLinter) -> None:
    linter.start_install()
    assert linter.status()["state"] == "installing"
    await linter.wait()


def test_the_node_archive_matches_the_machine() -> None:
    assert node_asset("win32", "AMD64").name == WIN_ZIP
    assert node_asset("linux", "aarch64").name == f"node-v{NODE_VERSION}-linux-arm64.tar.gz"
    assert node_asset("darwin", "arm64").url == (
        f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-darwin-arm64.tar.gz"
    )
    with pytest.raises(LinterError, match="no build for freebsd"):
        node_asset("freebsd", "x86_64")
    with pytest.raises(LinterError, match="no build"):
        node_asset("win32", "riscv")


@pytest.mark.asyncio
async def test_installing_fetches_pinned_node_and_packages(tmp_path: Path) -> None:
    commands = FakeCommands()
    downloads = FakeDownloads({WIN_ZIP: _node_zip()})
    linter = _linter(tmp_path, commands, downloads)
    assert linter.status()["state"] == "missing"
    assert linter.status()["pinned"] == {"node": NODE_VERSION, **PACKAGES}

    await _install(linter)

    status = linter.status()
    assert status["state"] == "installed", status["error"]
    assert status["versions"] == {"node": NODE_VERSION, **PACKAGES}
    assert isinstance(status["size"], int) and status["size"] > 0
    assert downloads.urls == [
        f"https://nodejs.org/dist/v{NODE_VERSION}/{WIN_ZIP}",
        f"https://nodejs.org/dist/v{NODE_VERSION}/SHASUMS256.txt",
    ]
    root = tmp_path / "tools" / "eslint"
    assert (root / "node" / "node.exe").is_file()
    assert "eslint-plugin-html" in (root / "eslint.config.mjs").read_text(encoding="utf-8")
    command, cwd, env, _ = commands.calls[0]
    assert command[:3] == [
        str(cwd / "node" / "node.exe"),
        str(cwd / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js"),
        "install",
    ]
    assert "--ignore-scripts" in command
    assert [item for item in command if "@" in item[1:]] == [
        f"{name}@{version}" for name, version in PACKAGES.items()
    ]
    assert env["PATH"] == str(cwd / "node")
    # Only the finished install is left; staging and npm's cache are gone.
    assert [item.name for item in (tmp_path / "tools").iterdir()] == ["eslint"]
    assert not (root / ".npm-cache").exists()

    with pytest.raises(LinterError, match="already installed"):
        linter.start_install()
    linter.remove()
    assert linter.status()["state"] == "missing"
    assert not root.exists()
    linter.remove()  # Nothing there: nothing to do.


@pytest.mark.asyncio
async def test_installing_on_linux_unpacks_the_tarball(tmp_path: Path) -> None:
    linter = _linter(
        tmp_path, FakeCommands(), FakeDownloads({LINUX_TAR: _node_tar()}), platform="linux"
    )
    await _install(linter)

    assert linter.status()["state"] == "installed", linter.status()["error"]
    node = tmp_path / "tools" / "eslint" / "node" / "bin" / "node"
    assert node.is_file()
    assert (tmp_path / "tools" / "eslint" / "node" / "include").is_dir()


@pytest.mark.asyncio
async def test_only_one_install_runs_at_a_time(tmp_path: Path) -> None:
    linter = _linter(tmp_path, FakeCommands())
    linter.start_install()
    with pytest.raises(LinterError, match="already being installed"):
        linter.start_install()
    with pytest.raises(LinterError, match="wait for the install"):
        linter.remove()
    assert linter.status()["progress"] == {"stage": "downloading", "received": 0, "total": None}
    await linter.wait()


@pytest.mark.parametrize(
    ("downloads", "npm_code", "message"),
    [
        (
            FakeDownloads({WIN_ZIP: _node_zip()}, sums="0" * 64 + f"  {WIN_ZIP}\n"),
            0,
            f"{WIN_ZIP} does not match its published checksum",
        ),
        (
            FakeDownloads({WIN_ZIP: _node_zip()}, sums="abc  other.zip\n"),
            0,
            f"nodejs.org lists no checksum for {WIN_ZIP}",
        ),
        (
            FakeDownloads({WIN_ZIP: _node_zip()}),
            1,
            "npm could not install ESLint: npm ERR! offline",
        ),
        (
            FakeDownloads({WIN_ZIP: _node_zip(top="")}),
            0,
            "the Node.js archive contains an unsafe path",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_install_reports_why_and_leaves_nothing(
    tmp_path: Path, downloads: FakeDownloads, npm_code: int, message: str
) -> None:
    linter = _linter(tmp_path, FakeCommands(npm_code=npm_code), downloads)
    await _install(linter)

    status = linter.status()
    assert (status["state"], status["error"]) == ("failed", message)
    assert list((tmp_path / "tools").iterdir()) == []
    # Trying again clears the old failure.
    linter.start_install()
    assert linter.status()["error"] is None
    await linter.wait()


@pytest.mark.asyncio
async def test_npm_finishing_without_eslint_fails_the_install(tmp_path: Path) -> None:
    class NoEslint(FakeCommands):
        def __call__(self, command, cwd, env, timeout, stdin):  # type: ignore[no-untyped-def]
            return CommandResult(0, "", "")

    linter = _linter(tmp_path, NoEslint())
    await _install(linter)
    assert linter.status()["error"] == "npm finished but ESLint is missing"


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "https://nodejs.org/x"),
                response=httpx.Response(404),
            ),
            "download failed: HTTP 404 from https://nodejs.org/x",
        ),
        (httpx.ConnectError("offline"), "download failed: ConnectError"),
        (OSError("disk full"), "install failed: disk full"),
    ],
)
@pytest.mark.asyncio
async def test_download_failures_are_reported(
    tmp_path: Path, error: Exception, message: str
) -> None:
    async def failing(url: str, destination: Path, progress: object) -> None:
        raise error

    linter = BenchmarkLinter(tmp_path, downloader=failing, runner=FakeCommands())
    await _install(linter)
    assert linter.status()["error"] == message


async def _installed(tmp_path: Path, commands: FakeCommands) -> BenchmarkLinter:
    linter = _linter(tmp_path, commands)
    await _install(linter)
    return linter


@pytest.mark.asyncio
async def test_checking_hands_eslint_the_text_never_the_workspace(tmp_path: Path) -> None:
    messages = [
        {
            "line": 14,
            "column": 3,
            "severity": 1,
            "message": "'x' is unused.",
            "ruleId": "no-unused-vars",
        },
        {
            "line": 8,
            "column": 1,
            "severity": 2,
            "message": "'grid' is constant.",
            "ruleId": "no-const-assign",
        },
        {
            "line": 15,
            "column": 9,
            "severity": 2,
            "message": "Parsing error: Unexpected token )",
            "ruleId": None,
        },
    ]
    commands = FakeCommands(eslint=CommandResult(1, json.dumps([{"messages": messages}]), ""))
    linter = await _installed(tmp_path, commands)

    result = linter.check("<script>grid = 5</script>", "tetris/index.html")

    assert result == (
        "tetris/index.html: 2 errors, 1 warning\n"
        "8:1 error 'grid' is constant. (no-const-assign)\n"
        "14:3 warning 'x' is unused. (no-unused-vars)\n"
        "15:9 error Parsing error: Unexpected token )"
    )
    command, cwd, _, stdin = commands.calls[-1]
    root = tmp_path / "tools" / "eslint"
    assert cwd == root
    assert stdin == "<script>grid = 5</script>"
    assert command == [
        str(root / "node" / "node.exe"),
        str(root / "node_modules" / "eslint" / "bin" / "eslint.js"),
        "--config",
        str(root / "eslint.config.mjs"),
        "--no-inline-config",
        "--format",
        "json",
        "--stdin",
        "--stdin-filename",
        "workspace-file.html",
    ]


@pytest.mark.asyncio
async def test_checking_refuses_what_eslint_cannot_lint(tmp_path: Path) -> None:
    missing = _linter(tmp_path, FakeCommands())
    with pytest.raises(LinterError, match="not installed"):
        missing.check("x", "a.js")
    with pytest.raises(LinterError, match=r"check_file lints \.js, .*not '\.css'"):
        missing.check("x", "style.css")
    with pytest.raises(LinterError, match="not 'Makefile'"):
        missing.check("x", "Makefile")


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (
            CommandResult(2, "", "Oops\nESLint couldn't find the config"),
            "ESLint failed: ESLint couldn't find the config",
        ),
        (CommandResult(2, "", ""), "ESLint failed: no output"),
        (CommandResult(0, "not json", ""), "ESLint returned output that could not be read"),
    ],
)
@pytest.mark.asyncio
async def test_eslint_failing_is_reported(
    tmp_path: Path, reply: CommandResult, message: str
) -> None:
    linter = await _installed(tmp_path, FakeCommands(eslint=reply))
    with pytest.raises(LinterError, match=f"^{message}$"):
        linter.check("x", "a.mjs")


@pytest.mark.asyncio
async def test_a_clean_file_and_an_empty_reply_both_report_no_problems(tmp_path: Path) -> None:
    linter = await _installed(tmp_path, FakeCommands(eslint=CommandResult(0, "[]", "")))
    assert linter.check("const a = 1;", "a.js") == "a.js: no problems found."


def test_long_reports_list_the_first_problems_and_count_the_rest() -> None:
    messages: list[dict[str, object]] = [
        {"line": index, "column": 1, "severity": 2, "message": "bad", "ruleId": "no-undef"}
        for index in range(1, MAX_PROBLEMS + 6)
    ]
    lines = report("a.js", messages).splitlines()
    assert lines[0] == f"a.js: {MAX_PROBLEMS + 5} errors"
    assert len(lines) == MAX_PROBLEMS + 2
    assert lines[-1] == "…and 5 more"
    assert report("a.js", [{"severity": 1, "message": "odd"}]) == "a.js: 1 warning\n0:0 warning odd"


def test_a_broken_install_counts_as_missing(tmp_path: Path) -> None:
    root = tmp_path / "tools" / "eslint"
    root.mkdir(parents=True)
    (root / "install.json").write_text("{}", encoding="utf-8")
    linter = _linter(tmp_path, FakeCommands())
    assert linter.status()["state"] == "missing"  # node.exe is not there
    (root / "node").mkdir()
    (root / "node" / "node.exe").write_text("", encoding="utf-8")
    assert linter.status()["state"] == "missing"  # nor is ESLint
    (root / "install.json").write_text("[]", encoding="utf-8")
    assert linter.status()["state"] == "missing"


def test_commands_run_with_their_input_and_time_out(tmp_path: Path) -> None:
    echo = run_command(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        tmp_path,
        {"SYSTEMROOT": "C:\\Windows"} if sys.platform == "win32" else {},
        30,
        "lint me",
    )
    assert (echo.returncode, echo.stdout.strip()) == (0, "LINT ME")
    with pytest.raises(LinterError, match="took longer than 0 s"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, {}, 0.1, None)
    with pytest.raises(LinterError, match="could not start Node.js"):
        run_command([str(tmp_path / "missing.exe"), "x"], tmp_path, {}, 5, None)


@pytest.mark.asyncio
async def test_downloads_stream_to_disk_with_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 10, headers={"Content-Length": "10"})
    )
    real = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real(transport=transport)

    monkeypatch.setattr(benchmark_linter.httpx, "AsyncClient", client)
    seen: list[tuple[int, int | None]] = []
    target = tmp_path / "file"
    await benchmark_linter.download(
        "https://nodejs.org/x", target, lambda r, t: seen.append((r, t))
    )
    assert target.read_bytes() == b"x" * 10
    assert seen[-1] == (10, 10)


def test_settings_install_and_remove_eslint_and_the_catalog_follows(
    tmp_path: Path, login: Login
) -> None:
    linter = _linter(tmp_path / "data", FakeCommands())
    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        benchmarks=(BENCHMARK,),
        benchmark_linter=linter,
    )
    with TestClient(app) as client:
        login(client)
        before = client.get("/api/settings/benchmark-tools").json()["eslint"]
        offered = {
            item["name"]: item["available"]
            for item in client.get("/api/benchmarks").json()["tools"]
        }
        started = client.post("/api/settings/benchmark-tools/eslint")
        client.portal.call(linter.wait)  # type: ignore[union-attr]
        again = client.post("/api/settings/benchmark-tools/eslint")
        installed = client.get("/api/settings/benchmark-tools").json()["eslint"]
        offered_after = {
            item["name"]: item["available"]
            for item in client.get("/api/benchmarks").json()["tools"]
        }
        app.state.benchmark_runner._active = "run-1"
        busy = client.delete("/api/settings/benchmark-tools/eslint")
        app.state.benchmark_runner._active = None
        removed = client.delete("/api/settings/benchmark-tools/eslint")

    assert before["state"] == "missing"
    assert offered["check_file"] is False and offered["write_file"] is True
    assert started.status_code == 202
    assert again.status_code == 409
    assert again.json()["detail"] == "the linter is already installed"
    assert installed["state"] == "installed"
    assert offered_after["check_file"] is True
    assert busy.status_code == 409
    assert (
        busy.json()["detail"] == "wait for the running benchmark to finish before removing ESLint"
    )
    assert removed.json()["eslint"]["state"] == "missing"


def test_installing_eslint_needs_the_settings_write_privilege() -> None:
    assert privilege_for_request("/api/settings/benchmark-tools", "GET") == "settings.read"
    assert privilege_for_request("/api/settings/benchmark-tools/eslint", "POST") == "settings.write"
    assert (
        privilege_for_request("/api/settings/benchmark-tools/eslint", "DELETE") == "settings.write"
    )


@pytest.mark.asyncio
async def test_removing_reports_files_still_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linter = await _installed(tmp_path, FakeCommands())

    def locked(path: object, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(benchmark_linter.shutil, "rmtree", locked)
    with pytest.raises(
        LinterError, match=r"could not remove every ESLint file \(Access is denied\)"
    ):
        linter.remove()


@pytest.mark.asyncio
async def test_an_install_builds_in_place_and_is_only_finished_once_marked(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    class Watching(FakeCommands):
        def __call__(self, command, cwd, env, timeout, stdin):  # type: ignore[no-untyped-def]
            # npm runs in the final folder, before install.json marks it finished.
            seen.append(str(cwd))
            assert not (cwd / "install.json").exists()
            assert linter.status()["state"] == "installing"
            return super().__call__(command, cwd, env, timeout, stdin)

    linter = _linter(tmp_path, Watching())
    await _install(linter)
    assert seen == [str(tmp_path / "tools" / "eslint")]
    assert linter.status()["state"] == "installed"
