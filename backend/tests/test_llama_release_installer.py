from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.llama_release_installer import (
    GitHubReleaseClient,
    ReleaseAsset,
    ReleaseInfo,
    ReleaseInstallError,
    RuntimeInstaller,
    _digest_matches,
    _extract_archive,
    _parse_assets,
    _safe_target,
    group_runtime_assets,
)
from llamawebui.services.runtime_probe import RuntimeProbeResult


@pytest.mark.asyncio
async def test_release_client_resolves_stable_nightly_pointer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/tags/v1"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": "nightly-tag.txt",
                            "browser_download_url": "https://x/nightly",
                            "size": 4,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/nightly"):
            return httpx.Response(200, text="b123\n")
        return httpx.Response(200, json={"tag_name": "b123", "assets": []})

    client = GitHubReleaseClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await client.release("v1")
    assert result.tag == "b123"
    assert result.stable_tag == "v1"


@pytest.mark.asyncio
async def test_release_client_resolves_latest_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": "v1", "assets": []})
        return httpx.Response(404)

    client = GitHubReleaseClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await client.release("latest")

    assert result.tag == "v1"
    assert result.stable_tag == "v1"


@pytest.mark.asyncio
async def test_release_client_keeps_build_release_and_rejects_invalid_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/tags/b123"):
            return httpx.Response(200, json={"tag_name": "b123", "assets": []})
        return httpx.Response(200, json={"bad": True})

    client = GitHubReleaseClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert (await client.release("b123")).stable_tag is None

    invalid = GitHubReleaseClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ReleaseInstallError, match="invalid release"):
        await invalid.release("v1")


@pytest.mark.asyncio
async def test_installer_downloads_zip_verifies_and_promotes(tmp_path: Path) -> None:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("bin/llama-server.exe", "binary")
    data = content.getvalue()
    release = ReleaseInfo(
        "b1",
        "v1",
        (
            ReleaseAsset(
                "build.zip",
                "https://x/build.zip",
                len(data),
                f"sha256:{hashlib.sha256(data).hexdigest()}",
            ),
        ),
    )

    class Releases:
        async def release(self, tag: str) -> ReleaseInfo:
            assert tag == "v1"
            return release

    async def prober(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            path,
            RuntimeVersion("1", None, "1"),
            RuntimeCapabilities(frozenset({"models-preset"}), "help"),
            None,
            (),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data)

    # Replace the download client used by the module with a transport-backed client.
    import llamawebui.services.llama_release_installer as module
    original = module.httpx.AsyncClient
    module.httpx.AsyncClient = lambda **kwargs: original(
        transport=httpx.MockTransport(handler),
        **{key: value for key, value in kwargs.items() if key != "transport"},
    )  # type: ignore[assignment]
    try:
        destination = await RuntimeInstaller(
            tmp_path, prober=prober, releases=Releases()
        ).install(tag="v1", asset_name="build.zip")
    finally:
        module.httpx.AsyncClient = original
    assert (destination / "bin" / "llama-server.exe").read_text() == "binary"


def test_archive_helpers_reject_unsafe_paths_and_validate_digests(tmp_path: Path) -> None:
    assert _parse_assets(None) == ()
    assert _digest_matches(b"x", f"sha256:{hashlib.sha256(b'x').hexdigest()}")
    assert not _digest_matches(b"x", "md5:x")
    with pytest.raises(ReleaseInstallError):
        _safe_target(tmp_path, "../escape")
    with pytest.raises(FileNotFoundError):
        _extract_archive(tmp_path / "missing.tar", tmp_path / "out")


def test_archive_helpers_find_required_executable_and_parse_asset_rows(tmp_path: Path) -> None:
    archive = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("llama-server", "binary")
    destination = tmp_path / "out"
    destination.mkdir()
    _extract_archive(archive, destination)
    assert (destination / "llama-server").read_text() == "binary"
    assert _parse_assets([{"name": "x", "browser_download_url": "u", "size": "bad"}])[0].size == 0


def test_runtime_asset_grouping_selects_backend_companions() -> None:
    assets = (
        ReleaseAsset("llama-b1-cuda.zip", "cuda", 1, None),
        ReleaseAsset("llama-b1-cudart.zip", "cudart", 1, None),
        ReleaseAsset("llama-b1-cpu.zip", "cpu", 1, None),
        ReleaseAsset("llama-b1-vulkan.zip", "vulkan", 1, None),
        ReleaseAsset("notes.txt", "notes", 1, None),
    )

    cuda = group_runtime_assets(assets, backend="cuda")
    cpu = group_runtime_assets(assets, backend="cpu")
    auto = group_runtime_assets(assets)

    assert len(cuda) == 1
    assert cuda[0].primary.name == "llama-b1-cuda.zip"
    assert [asset.name for asset in cuda[0].companions] == ["llama-b1-cudart.zip"]
    assert [group.primary.name for group in cpu] == ["llama-b1-cpu.zip"]
    assert [group.primary.name for group in auto] == ["llama-b1-cpu.zip"]


def test_release_asset_parsing_skips_malformed_rows_and_digest_mismatch() -> None:
    assert _parse_assets(["bad", {"name": "missing-url"}]) == ()
    assert not _digest_matches(b"x", "sha256:not-the-digest")


def test_installer_reports_missing_asset(tmp_path: Path) -> None:
    class Releases:
        async def release(self, tag: str) -> ReleaseInfo:
            return ReleaseInfo(tag, None, ())

    async def prober(path: Path) -> RuntimeProbeResult:
        raise AssertionError(path)

    with pytest.raises(ReleaseInstallError, match="asset not found"):
        import asyncio
        asyncio.run(
            RuntimeInstaller(tmp_path, prober=prober, releases=Releases()).install(
                tag="v1", asset_name="missing.zip"
            )
        )
