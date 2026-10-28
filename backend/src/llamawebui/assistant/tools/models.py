"""Tools for finding models on Hugging Face, downloading them and installing runtimes."""

from __future__ import annotations

import platform
import sys
from collections.abc import Mapping

from huggingface_hub.errors import HfHubHTTPError

from llamawebui.assistant.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    json_result,
    object_schema,
    text_argument,
)
from llamawebui.assistant.tools.system import GIB, gib
from llamawebui.domain.model_manifest import GgufGroup
from llamawebui.models import DownloadJobRecord
from llamawebui.services.download_registry import DownloadPlanError
from llamawebui.services.huggingface_catalog import CatalogUnavailableError, RepositoryManifest
from llamawebui.services.llama_release_installer import ReleaseInstallError, group_runtime_assets
from llamawebui.services.runtime_registry import RuntimeAlreadyRegisteredError

README_CHARACTERS = 4000
# How release file names name this machine's system and processor.
_SYSTEM = {"win32": "win", "darwin": "macos"}.get(sys.platform, "ubuntu")
_ARCH = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"


def for_this_machine(name: str) -> bool:
    """Whether a release file is built for this system and processor."""
    lowered = name.lower()
    return f"-{_SYSTEM}-" in lowered and f"-{_ARCH}" in lowered
BACKENDS = ("cpu", "cuda", "vulkan", "rocm", "sycl", "metal")


async def _repository(context: ToolContext, repo_id: str) -> RepositoryManifest:
    try:
        return await context.services.hub.repository(repo_id)
    except (CatalogUnavailableError, HfHubHTTPError, ValueError) as error:
        raise ToolError(f"could not read {repo_id} from Hugging Face: {error}") from error


async def search_models(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    query = text_argument(arguments, "query")
    sort = arguments.get("sort")
    try:
        results = await context.services.hub.search(
            query, sort=sort if sort in {"downloads", "likes", "lastModified"} else "downloads"
        )
    except CatalogUnavailableError as error:
        raise ToolError(str(error)) from error
    models = [
        {
            "repo_id": result.repo_id,
            "downloads": result.downloads,
            "likes": result.likes,
            "updated": result.last_modified,
            "gated": result.gated,
        }
        for result in results
    ]
    return json_result(models, f"{len(models)} repositories")


def _group(group: GgufGroup) -> dict[str, object]:
    """A quantization group, leaving out what is true of almost every group."""
    row: dict[str, object] = {
        "group_key": group.key,
        "quantization": group.quantization,
        "size_gib": gib(group.total_size),
    }
    if len(group.files) != 1:
        row["files"] = len(group.files)
    if not group.complete:
        row["complete"] = False
    if group.projector_files:
        row["projector_files"] = len(group.projector_files)
    return row


async def get_repository(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    repo_id = text_argument(arguments, "repo_id")
    manifest = await _repository(context, repo_id)
    readme = manifest.readme or ""
    return json_result(
        {
            "repo_id": manifest.repo_id,
            "revision": manifest.revision,
            "quantization_groups": [_group(group) for group in manifest.groups],
            "readme": readme[:README_CHARACTERS]
            + ("… (cut)" if len(readme) > README_CHARACTERS else ""),
        },
        f"{len(manifest.groups)} quantization groups",
    )


async def describe_download(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    repo_id = text_argument(arguments, "repo_id")
    group_key = text_argument(arguments, "group_key")
    manifest = await _repository(context, repo_id)
    group = next((item for item in manifest.groups if item.key == group_key), None)
    if group is None:
        raise ToolError(f"{repo_id} has no quantization group {group_key!r}")
    size = f"{group.total_size / GIB:.1f} GiB" if group.total_size else "unknown size"
    return (
        f"Download {repo_id} · {group.quantization or group_key} ({size})",
        {"repo_id": repo_id, "group_key": group_key, "files": len(group.files), "size": size},
    )


async def queue_download(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    repo_id = text_argument(arguments, "repo_id")
    group_key = text_argument(arguments, "group_key")
    try:
        job = await context.services.control_plane.queue_download(
            repo_id, group_key, include_projector=arguments.get("include_projector") is True
        )
    except (CatalogUnavailableError, HfHubHTTPError) as error:
        raise ToolError(f"could not read {repo_id} from Hugging Face: {error}") from error
    except DownloadPlanError as error:
        raise ToolError(str(error)) from error
    context.watch("download", job.id)
    return json_result(
        {
            "download_id": job.id,
            "destination": job.destination,
            "total_gib": gib(job.total_bytes),
            "note": "Downloading in the background. You will be notified when it finishes; "
            "do not poll for it.",
        },
        f"queued {repo_id}",
    )


async def list_runtime_releases(
    context: ToolContext, arguments: Mapping[str, object]
) -> ToolResult:
    tag = text_argument(arguments, "tag", required=False) or "latest"
    every_platform = arguments.get("all_platforms") is True
    try:
        release = await context.services.releases.release(tag)
    except ReleaseInstallError as error:
        raise ToolError(str(error)) from error
    builds = {
        backend: [
            group.primary.name
            for group in group_runtime_assets(release.assets, backend=backend)
            if every_platform or for_this_machine(group.primary.name)
        ]
        for backend in BACKENDS
    }
    return json_result(
        {
            "tag": release.tag,
            "platform": "all" if every_platform else f"{_SYSTEM} {_ARCH}",
            "builds": {backend: names for backend, names in builds.items() if names},
        },
        release.tag,
    )


async def describe_install(
    context: ToolContext, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    tag = text_argument(arguments, "tag")
    asset = text_argument(arguments, "asset_name")
    backend = text_argument(arguments, "backend", required=False) or None
    return f"Install llama.cpp {tag} ({asset})", {
        "tag": tag,
        "asset_name": asset,
        "backend": backend,
    }


async def install_runtime(context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
    tag = text_argument(arguments, "tag")
    asset = text_argument(arguments, "asset_name")
    backend = text_argument(arguments, "backend", required=False) or None
    try:
        runtime = await context.services.control_plane.install_runtime(tag, asset, backend=backend)
    except (ReleaseInstallError, FileNotFoundError, RuntimeAlreadyRegisteredError) as error:
        raise ToolError(str(error)) from error
    return json_result(
        {"runtime_id": runtime.id, "name": runtime.name, "build": runtime.build},
        f"installed {runtime.name}",
    )


MODEL_TOOLS = (
    Tool(
        "search_models",
        "Search Hugging Face for GGUF model repositories.",
        object_schema(
            {
                "query": {"type": "string", "description": "Words in the repository name."},
                "sort": {"type": "string", "enum": ["downloads", "likes", "lastModified"]},
            },
            ["query"],
        ),
        "huggingface.read",
        search_models,
    ),
    Tool(
        "get_repository",
        "A Hugging Face repository's GGUF quantization groups (size, files) and the start of "
        "its model card.",
        object_schema({"repo_id": {"type": "string"}}, ["repo_id"]),
        "huggingface.read",
        get_repository,
    ),
    Tool(
        "queue_download",
        "Download one quantization group of a repository into the model library. Runs in the "
        "background; you are notified when it completes or fails.",
        object_schema(
            {
                "repo_id": {"type": "string"},
                "group_key": {"type": "string", "description": "From get_repository."},
                "include_projector": {
                    "type": "boolean",
                    "description": "Also download the vision projector (mmproj), if any.",
                },
            },
            ["repo_id", "group_key"],
        ),
        "downloads.write",
        queue_download,
        approval="downloads",
        describe=describe_download,
    ),
    Tool(
        "list_runtime_releases",
        "llama.cpp release builds on GitHub for this machine's system, by backend, for "
        "install_runtime.",
        object_schema(
            {
                "tag": {"type": "string", "description": "A release tag, or latest."},
                "all_platforms": {
                    "type": "boolean",
                    "description": "List builds for every system, not only this machine's.",
                },
            }
        ),
        "runtimes.read",
        list_runtime_releases,
    ),
    Tool(
        "install_runtime",
        "Download a llama.cpp release build and register it as a runtime. Waits until done.",
        object_schema(
            {
                "tag": {"type": "string"},
                "asset_name": {"type": "string", "description": "From list_runtime_releases."},
                "backend": {"type": "string", "enum": list(BACKENDS)},
            },
            ["tag", "asset_name"],
        ),
        "runtimes.write",
        install_runtime,
        approval="runtimes",
        describe=describe_install,
    ),
)


def download_notice(job: DownloadJobRecord) -> str:
    """How a finished download went, in one line for the conversation that started it."""
    head = f"Download {job.id} ({job.repo_id}, {job.group_key}) {job.state}"
    if job.error:
        return f"{head}: {job.error}."
    return f"{head}: {gib(job.total_bytes) or 0} GiB in {job.destination}."
