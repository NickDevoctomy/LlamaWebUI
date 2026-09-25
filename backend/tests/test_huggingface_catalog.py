from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from huggingface_hub import HfApi

from llamawebui.services.huggingface_catalog import HuggingFaceCatalog

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


async def test_repository_pins_revision_and_groups_files() -> None:
    api = Mock(spec=HfApi)
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
    assert manifest.revision == "abc123"
    assert manifest.groups[0].total_size == 30
    assert manifest.groups[0].complete


async def test_repository_rejects_missing_commit_sha() -> None:
    api = Mock(spec=HfApi)
    api.model_info.return_value = SimpleNamespace(
        id="owner/model",
        sha=None,
        siblings=[],
    )

    with pytest.raises(ValueError, match="did not resolve to a commit SHA"):
        await HuggingFaceCatalog(api=api).repository("owner/model")