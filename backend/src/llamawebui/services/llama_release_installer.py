"""Discover and install official llama.cpp GitHub releases."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx

from llamawebui.services.runtime_probe import RuntimeProber


class ReleaseInstallError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    tag: str
    stable_tag: str | None
    assets: tuple[ReleaseAsset, ...]


class GitHubReleaseClient:
    def __init__(
        self,
        *,
        repo: str = "ggml-org/llama.cpp",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.repo = repo
        self._client = client

    async def release(self, tag: str) -> ReleaseInfo:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=30.0, headers={"Accept": "application/vnd.github+json"}
        )
        try:
            response = await client.get(f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
                raise ReleaseInstallError("GitHub returned an invalid release")
            assets = _parse_assets(payload.get("assets"))
            stable_tag = tag if not tag.startswith("b") else None
            nightly = next(
                (asset for asset in assets if asset.name.lower() == "nightly-tag.txt"), None
            )
            if stable_tag and nightly:
                build = (await client.get(nightly.url)).text.strip()
                if build and build != tag:
                    return await self._release_for_build(client, build, tag)
            return ReleaseInfo(tag=tag, stable_tag=stable_tag, assets=assets)
        except httpx.HTTPError as error:
            raise ReleaseInstallError("GitHub release lookup failed") from error
        finally:
            if owns_client:
                await client.aclose()

    async def _release_for_build(
        self, client: httpx.AsyncClient, tag: str, stable_tag: str
    ) -> ReleaseInfo:
        response = await client.get(f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ReleaseInstallError("GitHub returned an invalid build release")
        return ReleaseInfo(
            tag=tag, stable_tag=stable_tag, assets=_parse_assets(payload.get("assets"))
        )


class RuntimeInstaller:
    def __init__(
        self,
        data_dir: Path,
        *,
        prober: RuntimeProber,
        releases: GitHubReleaseClient | None = None,
    ) -> None:
        self.runtime_dir = data_dir / "runtimes"
        self.prober = prober
        self.releases = releases or GitHubReleaseClient()

    async def install(self, *, tag: str, asset_name: str, backend: str | None = None) -> Path:
        release = await self.releases.release(tag)
        asset = next((item for item in release.assets if item.name == asset_name), None)
        if asset is None:
            raise ReleaseInstallError(f"release asset not found: {asset_name}")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=self.runtime_dir))
        try:
            archive = staging / asset.name
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.get(asset.url, follow_redirects=True)
                response.raise_for_status()
                content = response.content
            if asset.digest and not _digest_matches(content, asset.digest):
                raise ReleaseInstallError("release asset digest verification failed")
            archive.write_bytes(content)
            extracted = staging / "payload"
            extracted.mkdir()
            _extract_archive(archive, extracted)
            executable = _find_executable(extracted)
            probe = await self.prober(executable)
            if probe.errors or not probe.capabilities.options:
                raise ReleaseInstallError("downloaded runtime failed capability probing")
            destination = self.runtime_dir / f"{release.tag}-{backend or 'auto'}-{uuid4().hex[:8]}"
            extracted.replace(destination)
            return destination
        except (httpx.HTTPError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
            raise ReleaseInstallError("runtime download or extraction failed") from error
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _parse_assets(raw: Any) -> tuple[ReleaseAsset, ...]:
    if not isinstance(raw, list):
        return ()
    result: list[ReleaseAsset] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("browser_download_url"), str)
        ):
            continue
        raw_size = item.get("size")
        size = raw_size if isinstance(raw_size, int) else 0
        digest = item.get("digest") if isinstance(item.get("digest"), str) else None
        result.append(ReleaseAsset(item["name"], item["browser_download_url"], size, digest))
    return tuple(result)


def _digest_matches(content: bytes, digest: str) -> bool:
    algorithm, _, expected = digest.partition(":")
    if algorithm.lower() != "sha256" or not expected:
        return False
    return hashlib.sha256(content).hexdigest().lower() == expected.lower()


def _extract_archive(archive: Path, destination: Path) -> None:
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as source:
            for zip_member in source.infolist():
                target = _safe_target(destination, zip_member.filename)
                if zip_member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read(zip_member))
        return
    with tarfile.open(archive) as source:
        for tar_member in source.getmembers():
            target = _safe_target(destination, tar_member.name)
            if tar_member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif tar_member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = source.extractfile(tar_member)
                if extracted is not None:
                    target.write_bytes(extracted.read())


def _safe_target(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseInstallError("release archive contains an unsafe path")
    target = (root / Path(*relative.parts)).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ReleaseInstallError("release archive contains an unsafe path")
    return target


def _find_executable(root: Path) -> Path:
    matches = list(root.rglob("llama-server.exe")) + list(root.rglob("llama-server"))
    if not matches:
        raise ReleaseInstallError("release archive does not contain llama-server")
    return matches[0]
