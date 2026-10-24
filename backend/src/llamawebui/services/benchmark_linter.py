"""ESLint for benchmark runs: installed on request, and run on the files an agent writes.

Linting is ESLint's own work, with eslint-plugin-html for the scripts inside HTML pages.
This module only installs a pinned Node.js and pinned packages into the data folder,
then hands ESLint a file's text and reports what it found.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx

LOGGER = logging.getLogger(__name__)

NODE_VERSION = "24.22.1"
# Exact versions, so every machine lints with the same rules.
PACKAGES: dict[str, str] = {
    "eslint": "10.13.0",
    "@eslint/js": "10.0.1",
    "eslint-plugin-html": "8.2.1",
    "globals": "17.13.0",
}
NODE_DIST = "https://nodejs.org/dist"
# The extensions ESLint is set up for here; anything else is refused before it runs.
LINTED_EXTENSIONS = (".js", ".mjs", ".cjs", ".html", ".htm")
# Problems listed for the model; the rest are counted.
MAX_PROBLEMS = 30
CHECK_TIMEOUT_SECONDS = 60.0
INSTALL_TIMEOUT_SECONDS = 600.0

# ESLint's recommended rules catch real mistakes (undefined names, duplicate keys,
# unreachable code, assigning to constants) and leave style alone.
CONFIG = """\
import js from "@eslint/js";
import html from "eslint-plugin-html";
import globals from "globals";

export default [
  js.configs.recommended,
  { files: ["**/*.html", "**/*.htm"], plugins: { html } },
  {
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: { ...globals.browser },
    },
  },
  { files: ["**/*.mjs"], languageOptions: { sourceType: "module" } },
];
"""


class LinterError(RuntimeError):
    """The linter is missing, busy, or could not install or run."""


@dataclass(frozen=True, slots=True)
class NodeAsset:
    name: str
    url: str


def node_asset(platform: str = sys.platform, machine: str | None = None) -> NodeAsset:
    """The official Node.js archive for this machine."""
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        (machine or _machine()).lower()
    )
    system = {"win32": "win", "linux": "linux", "darwin": "darwin"}.get(platform)
    if arch is None or system is None:
        raise LinterError(f"Node.js has no build for {platform} on {machine or _machine()}")
    name = f"node-v{NODE_VERSION}-{system}-{arch}.{'zip' if system == 'win' else 'tar.gz'}"
    return NodeAsset(name=name, url=f"{NODE_DIST}/v{NODE_VERSION}/{name}")


def _machine() -> str:
    import platform

    return platform.machine()


# Runs a command with optional stdin text; swapped out in tests.
CommandRunner = Callable[[list[str], Path, Mapping[str, str], float, str | None], "CommandResult"]


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(
    command: list[str], cwd: Path, env: Mapping[str, str], timeout: float, stdin: str | None
) -> CommandResult:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed programs from the install folder
            command,
            cwd=cwd,
            env=dict(env),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise LinterError(f"{Path(command[1]).stem} took longer than {timeout:.0f} s") from error
    except OSError as error:
        raise LinterError(f"could not start Node.js: {error}") from error
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


Downloader = Callable[[str, Path, Callable[[int, int | None], None]], Awaitable[None]]


async def download(
    url: str, destination: Path, progress: Callable[[int, int | None], None]
) -> None:
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client,
        client.stream("GET", url, follow_redirects=True) as response,
    ):
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else None
        received = 0
        with destination.open("wb") as output:
            async for chunk in response.aiter_bytes(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                progress(received, total)


class BenchmarkLinter:
    """Installs ESLint into ``<data>/tools/eslint`` and lints text with it."""

    def __init__(
        self,
        data_dir: Path,
        *,
        downloader: Downloader = download,
        runner: CommandRunner = run_command,
        platform: str = sys.platform,
        machine: str | None = None,
    ) -> None:
        self.tools_dir = data_dir / "tools"
        self.root = self.tools_dir / "eslint"
        self._download = downloader
        self._run = runner
        self._platform = platform
        self._machine = machine
        self._task: asyncio.Task[None] | None = None
        self._progress: dict[str, object] = {}
        self._error: str | None = None

    # -- state ------------------------------------------------------------------------

    @property
    def installing(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def installed(self) -> bool:
        return self._manifest() is not None

    def status(self) -> dict[str, object]:
        manifest = self._manifest()
        state = (
            "installing"
            if self.installing
            else "installed"
            if manifest
            else "failed"
            if self._error
            else "missing"
        )
        return {
            "state": state,
            "versions": manifest["versions"] if manifest else None,
            "installed_at": manifest["installed_at"] if manifest else None,
            "size": _folder_size(self.root) if manifest else None,
            "progress": dict(self._progress) if self.installing else None,
            "error": self._error if state == "failed" else None,
            # What an install fetches, so the page can say before it starts.
            "pinned": {"node": NODE_VERSION, **PACKAGES},
        }

    def _manifest(self) -> dict[str, object] | None:
        try:
            manifest = json.loads((self.root / "install.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(manifest, dict) or not self._node().is_file():
            return None
        if not self._eslint().is_file():
            return None
        return manifest

    def _node(self, root: Path | None = None) -> Path:
        base = (root or self.root) / "node"
        return base / "node.exe" if self._platform == "win32" else base / "bin" / "node"

    def _eslint(self, root: Path | None = None) -> Path:
        return (root or self.root) / "node_modules" / "eslint" / "bin" / "eslint.js"

    def _npm(self, root: Path) -> Path:
        base = root / "node"
        if self._platform == "win32":
            return base / "node_modules" / "npm" / "bin" / "npm-cli.js"
        return base / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"

    # -- install and remove -------------------------------------------------------------

    def start_install(self) -> None:
        if self.installing:
            raise LinterError("the linter is already being installed")
        if self.installed:
            raise LinterError("the linter is already installed")
        self._error = None
        self._progress = {"stage": "downloading", "received": 0, "total": None}
        self._task = asyncio.create_task(self._install())

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    def remove(self) -> None:
        if self.installing:
            raise LinterError("wait for the install to finish before removing it")
        self._error = None
        if self.root.exists():
            try:
                shutil.rmtree(self.root)
            except OSError as error:
                raise LinterError(
                    f"could not remove every ESLint file ({error.strerror}); try again shortly"
                ) from error

    async def _install(self) -> None:
        # Everything is built in its final folder, and install.json is written last to mark
        # it finished. Renaming a finished folder into place fails on Windows while a virus
        # scanner still holds the new files open.
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.root, ignore_errors=True)
        root = self.root
        staging = Path(tempfile.mkdtemp(prefix=".download-eslint-", dir=self.tools_dir))
        try:
            asset = node_asset(self._platform, self._machine)
            archive = staging / asset.name
            sums = staging / "SHASUMS256.txt"

            def progress(received: int, total: int | None) -> None:
                self._progress.update(received=received, total=total)

            await self._download(asset.url, archive, progress)
            await self._download(
                f"{NODE_DIST}/v{NODE_VERSION}/SHASUMS256.txt", sums, lambda _r, _t: None
            )
            self._progress.update(stage="verifying")
            _verify(archive, asset.name, sums.read_text(encoding="utf-8"))
            self._progress.update(stage="extracting")
            root.mkdir()
            await asyncio.to_thread(_extract_node, archive, root / "node")
            shutil.rmtree(staging, ignore_errors=True)
            if self._platform != "win32":
                self._node(root).chmod(0o755)
            self._progress.update(stage="packages")
            await asyncio.to_thread(self._install_packages, root)
            (root / "eslint.config.mjs").write_text(CONFIG, encoding="utf-8")
            (root / "install.json").write_text(
                json.dumps(
                    {
                        "versions": {"node": NODE_VERSION, **PACKAGES},
                        "installed_at": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except (
            LinterError,
            httpx.HTTPError,
            OSError,
            tarfile.TarError,
            zipfile.BadZipFile,
        ) as error:
            self._error = _install_error(error)
            shutil.rmtree(root, ignore_errors=True)
            LOGGER.warning(
                "linter_install_failed",
                extra={"component": "benchmarks", "event": "linter_failed", "error": self._error},
            )
        finally:
            self._progress = {}
            shutil.rmtree(staging, ignore_errors=True)

    def _install_packages(self, root: Path) -> None:
        (root / "package.json").write_text(
            json.dumps({"name": "llamawebui-eslint", "private": True}), encoding="utf-8"
        )
        command = [
            str(self._node(root)),
            str(self._npm(root)),
            "install",
            "--prefix",
            str(root),
            "--save-exact",
            # Package install scripts never run: nothing but the packages' files is needed.
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--loglevel=error",
            *(f"{name}@{version}" for name, version in PACKAGES.items()),
        ]
        cache = root / ".npm-cache"
        env = self._env(root, npm_config_cache=str(cache))
        result = self._run(command, root, env, INSTALL_TIMEOUT_SECONDS, None)
        shutil.rmtree(cache, ignore_errors=True)
        if result.returncode != 0:
            raise LinterError(f"npm could not install ESLint: {_last_line(result.stderr)}")
        if not self._eslint(root).is_file():
            raise LinterError("npm finished but ESLint is missing")

    def _env(self, root: Path, **extra: str) -> dict[str, str]:
        keep = (
            *("SYSTEMROOT", "WINDIR", "TEMP", "TMP"),
            *("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"),
        )
        env = {key: value for key, value in os.environ.items() if key.upper() in keep}
        env["PATH"] = str(self._node(root).parent)
        env["NO_UPDATE_NOTIFIER"] = "1"
        env.update(extra)
        return env

    # -- linting ------------------------------------------------------------------------

    def check(self, text: str, path: str) -> str:
        """Lint one workspace file's text; the report is written for the model."""
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in LINTED_EXTENSIONS:
            raise LinterError(
                f"check_file lints {', '.join(LINTED_EXTENSIONS)} files, not '{suffix or path}'"
            )
        if not self.installed:
            raise LinterError("the linter is not installed")
        # ESLint reads the text from stdin under a neutral name inside its own folder, so
        # nothing in the workspace (such as a planted eslint.config.js) can change how it runs.
        command = [
            str(self._node()),
            str(self._eslint()),
            "--config",
            str(self.root / "eslint.config.mjs"),
            "--no-inline-config",
            "--format",
            "json",
            "--stdin",
            "--stdin-filename",
            f"workspace-file{suffix}",
        ]
        result = self._run(command, self.root, self._env(self.root), CHECK_TIMEOUT_SECONDS, text)
        if result.returncode not in (0, 1):
            raise LinterError(f"ESLint failed: {_last_line(result.stderr or result.stdout)}")
        try:
            files = json.loads(result.stdout)
            messages = files[0]["messages"] if files else []
        except (ValueError, LookupError, TypeError) as error:
            raise LinterError("ESLint returned output that could not be read") from error
        return report(path, messages)


def report(path: str, messages: list[dict[str, object]]) -> str:
    """ESLint's findings as short lines, in file order; line numbers match read_file."""
    if not messages:
        return f"{path}: no problems found."
    errors = sum(1 for item in messages if item.get("severity") == 2)
    warnings = len(messages) - errors
    counts = [
        f"{count} {label}{'' if count == 1 else 's'}"
        for count, label in ((errors, "error"), (warnings, "warning"))
        if count
    ]
    lines = [f"{path}: {', '.join(counts)}"]
    ordered = sorted(messages, key=lambda item: (_int(item.get("line")), _int(item.get("column"))))
    for item in ordered[:MAX_PROBLEMS]:
        severity = "error" if item.get("severity") == 2 else "warning"
        rule = item.get("ruleId")
        lines.append(
            f"{_int(item.get('line'))}:{_int(item.get('column'))} {severity} "
            f"{item.get('message')}{f' ({rule})' if rule else ''}"
        )
    if len(ordered) > MAX_PROBLEMS:
        lines.append(f"…and {len(ordered) - MAX_PROBLEMS} more")
    return "\n".join(lines)


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _verify(archive: Path, name: str, sums: str) -> None:
    expected = next(
        (line.split()[0] for line in sums.splitlines() if line.strip().endswith(f"  {name}")),
        None,
    )
    if expected is None:
        raise LinterError(f"nodejs.org lists no checksum for {name}")
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != expected.lower():
        raise LinterError(f"{name} does not match its published checksum")


def _extract_node(archive: Path, destination: Path) -> None:
    """Unpack Node.js, dropping the archive's top folder (node-v24.x.y-win-x64/)."""
    destination.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                target = _target(destination, member.filename)
                if target is None:
                    continue
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as data, target.open("wb") as output:
                        shutil.copyfileobj(data, output)
        return
    with tarfile.open(archive) as source:
        for entry in source.getmembers():
            target = _target(destination, entry.name)
            if target is None:
                continue
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = source.extractfile(entry)
                if extracted is not None:
                    with extracted, target.open("wb") as output:
                        shutil.copyfileobj(extracted, output)
                if entry.mode & 0o111:
                    target.chmod(0o755)


def _target(root: Path, name: str) -> Path | None:
    parts = PurePosixPath(name).parts[1:]
    if not parts:
        return None
    if PurePosixPath(name).is_absolute() or ".." in parts:
        raise LinterError("the Node.js archive contains an unsafe path")
    return root.joinpath(*parts)


def _folder_size(root: Path) -> int:
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no output"


def _install_error(error: Exception) -> str:
    if isinstance(error, LinterError):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return f"download failed: HTTP {error.response.status_code} from {error.request.url}"
    if isinstance(error, httpx.HTTPError):
        return f"download failed: {type(error).__name__}"
    return f"install failed: {error}"
