from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from huggingface_hub import HfApi

from llamawebui.services.huggingface_catalog import CatalogUnavailableError, HuggingFaceCatalog

pytestmark = pytest.mark.asyncio


async def test_search_requests_gguf_models_and_maps_results() -> None:
    api = Mock(spec=HfApi)
    modified = datetime(2026, 9, 19, tzinfo=UTC)
    api.list_models.return_value = [
        SimpleNamespace(
            id="owner/model-GGUF",
            downloads=10,
            likes=None,
            last_modified=modified,
            gated="manual",
            private=False,
            tags=["gguf", "text-generation"],
        )
    ]
    catalog = HuggingFaceCatalog(api=api)

    results = await catalog.search("model", sort="likes", limit=5)

    api.list_models.assert_called_once_with(
        filter="gguf", search="model", sort="likes", direction=-1, limit=5, full=True
    )
    assert results[0].repo_id == "owner/model-GGUF"
    assert results[0].downloads == 10
    assert results[0].likes == 0
    assert results[0].last_modified == modified
    assert results[0].gated


async def test_search_preserves_provider_relevance_order_by_default() -> None:
    api = Mock(spec=HfApi)
    api.list_models.return_value = []

    await HuggingFaceCatalog(api=api).search("model")

    api.list_models.assert_called_once_with(filter="gguf", search="model", limit=25, full=True)


async def test_search_uses_recent_cache_when_hub_is_unavailable() -> None:
    api = Mock(spec=HfApi)
    api.list_models.return_value = []
    catalog = HuggingFaceCatalog(api=api)

    await catalog.search("model")
    api.list_models.side_effect = OSError("offline")

    assert await catalog.search("model") == ()


async def test_search_reports_unavailable_without_cached_metadata() -> None:
    api = Mock(spec=HfApi)
    api.list_models.side_effect = OSError("offline")

    with pytest.raises(CatalogUnavailableError, match="search is unavailable"):
        await HuggingFaceCatalog(api=api).search("model")


async def test_repository_pins_revision_and_groups_files(tmp_path: Path) -> None:
    api = Mock(spec=HfApi)
    readme = tmp_path / "README.md"
    readme.write_text("# Model card", encoding="utf-8")
    api.hf_hub_download.return_value = str(readme)
    api.model_info.return_value = SimpleNamespace(
        id="unsloth/Qwen3.8-Flash-Next-GGUF",
        sha="abc123",
        siblings=[
            SimpleNamespace(rfilename="UD-IQ4_XS/model-UD-IQ4_XS-00001-of-00002.gguf", size=10),
            SimpleNamespace(rfilename="UD-IQ4_XS/model-UD-IQ4_XS-00002-of-00002.gguf", size=20),
        ],
    )
    catalog = HuggingFaceCatalog(api=api)

    manifest = await catalog.repository("unsloth/Qwen3.8-Flash-Next-GGUF", revision="main")

    api.model_info.assert_called_once_with(
        "unsloth/Qwen3.8-Flash-Next-GGUF", revision="main", files_metadata=True
    )
    api.hf_hub_download.assert_called_once_with(
        "unsloth/Qwen3.8-Flash-Next-GGUF",
        "README.md",
        revision="abc123",
        repo_type="model",
    )
    assert manifest.revision == "abc123"
    assert manifest.groups[0].total_size == 30
    assert manifest.groups[0].complete
    assert manifest.readme == "# Model card"


async def test_repository_rejects_missing_commit_sha() -> None:
    api = Mock(spec=HfApi)
    api.model_info.return_value = SimpleNamespace(
        id="owner/model",
        sha=None,
        siblings=[],
    )

    with pytest.raises(ValueError, match="did not resolve to a commit SHA"):
        await HuggingFaceCatalog(api=api).repository("owner/model")


async def test_repository_works_when_readme_is_missing(tmp_path) -> None:
    from huggingface_hub.errors import EntryNotFoundError
    from requests import Response

    api = Mock(spec=HfApi)
    api.model_info.return_value = SimpleNamespace(id="owner/model", sha="abc123", siblings=[])
    response = Response()
    response.status_code = 404
    api.hf_hub_download.side_effect = EntryNotFoundError("README not found", response=response)

    manifest = await HuggingFaceCatalog(api=api).repository("owner/model")

    assert manifest.readme is None