from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from llamawebui.services.model_artifact_registry import ModelArtifactError
from llamawebui.services.model_library import DiscoveredModel
from llamawebui.services.model_locator import LocalModel, local_models, locate_model

LMSTUDIO_PATH = (
    r"C:\Users\someone\.lmstudio\models\unsloth\Qwen3.8-Flash-Next-GGUF"
    r"\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path.resolve()


def test_existing_path_is_used_as_is_and_linked_to_its_download(tmp_path: Path) -> None:
    model = _touch(tmp_path / "model.gguf")
    candidates = (LocalModel(model, "job-1", "org/repo", "model"),)

    location = locate_model(str(model), candidates)

    assert location.status == "found"
    assert location.path == str(model)
    assert location.download_id == "job-1"
    assert location.repo_id == "org/repo"
    assert location.search == "model"


def test_foreign_path_is_relocated_by_primary_shard_filename(tmp_path: Path) -> None:
    local = _touch(
        tmp_path / "models" / "Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"
    )
    other = _touch(tmp_path / "models" / "other.gguf")
    candidates = (LocalModel(other), LocalModel(local))
    second_shard = LMSTUDIO_PATH.replace("00001-of", "00002-of")

    location = locate_model(second_shard, candidates)

    assert location.status == "relocated"
    assert location.requested_path == second_shard
    assert location.path == str(local)
    assert location.download_id is None
    assert location.search == "Qwen3.8-Flash-Next"


def test_hugging_face_repository_and_quant_select_a_matching_download(tmp_path: Path) -> None:
    q8 = _touch(tmp_path / "q8" / "Qwen3-Q8_0.gguf")
    q4 = _touch(tmp_path / "q4" / "Qwen3-Q4_K_M.gguf")
    q4_xl = _touch(tmp_path / "q4xl" / "Qwen3-Q4_K_XL.gguf")
    candidates = (
        LocalModel(q8, "q8", "unsloth/Qwen3-GGUF", "Qwen3-Q8_0"),
        LocalModel(q4_xl, "q4xl", "unsloth/Qwen3-GGUF", "Qwen3-Q4_K_XL"),
        LocalModel(q4, "q4", "Unsloth/qwen3-gguf", "Qwen3-Q4_K_M"),
    )

    by_quant = locate_model(None, candidates, hf_repo="unsloth/Qwen3-GGUF:q4_k_m")
    by_repo = locate_model(None, candidates, hf_repo="unsloth/Qwen3-GGUF")

    assert (by_quant.status, by_quant.path, by_quant.download_id) == ("relocated", str(q4), "q4")
    assert by_quant.repo_id == "Unsloth/qwen3-gguf"
    assert by_repo.download_id == "q8"


def test_missing_model_guesses_the_repository_from_lm_studio_layout() -> None:
    location = locate_model(LMSTUDIO_PATH, ())

    assert location.status == "missing"
    assert location.path == LMSTUDIO_PATH
    assert location.repo_id == "unsloth/Qwen3.8-Flash-Next-GGUF"
    assert location.search == "Qwen3.8-Flash-Next"


def test_missing_model_guesses_the_repository_from_hugging_face_cache_layout() -> None:
    path = (
        "/home/someone/.cache/huggingface/hub/models--bartowski--Llama-3-GGUF"
        "/snapshots/abc123/Llama-3-Q5_K_M.gguf"
    )

    location = locate_model(path, ())

    assert location.repo_id == "bartowski/Llama-3-GGUF"
    assert location.search == "Llama-3"


def test_missing_model_without_layout_hints_offers_only_search_text() -> None:
    plain = locate_model("/srv/llm/weights/Mistral-7B-Instruct-v0.3.BF16.gguf", ())
    hub = locate_model(
        None, (), hf_repo="unsloth/Qwen3-GGUF:Q4_K_M", hf_file="Qwen3-Q4_K_M.gguf"
    )
    repo_only = locate_model(None, (), hf_repo="unsloth/Qwen3-GGUF")

    assert (plain.repo_id, plain.search) == (None, "Mistral-7B-Instruct-v0.3")
    assert (hub.status, hub.path, hub.repo_id) == ("missing", None, "unsloth/Qwen3-GGUF")
    assert hub.search == "Qwen3"
    assert (repo_only.search, repo_only.path) == ("unsloth/Qwen3-GGUF", None)


def test_local_models_lists_present_downloads_before_other_discovered_files(
    tmp_path: Path,
) -> None:
    downloaded = _touch(tmp_path / "owner--repo" / "repo-Q4.gguf")
    stray = _touch(tmp_path / "loose" / "loose.gguf")
    jobs = {
        "done": SimpleNamespace(id="done", state="completed", repo_id="owner/repo", group_key="Q4"),
        "gone": SimpleNamespace(id="gone", state="completed", repo_id="owner/gone", group_key="Q4"),
        "unsafe": SimpleNamespace(id="unsafe", state="completed", repo_id="x/y", group_key="Q4"),
        "queued": SimpleNamespace(id="queued", state="queued", repo_id="x/z", group_key="Q4"),
    }

    def primary_path(job: Any) -> Path | None:
        if job.id == "unsafe":
            raise ModelArtifactError("unsafe")
        return downloaded if job.id == "done" else tmp_path / "missing.gguf"

    downloads = SimpleNamespace(list=lambda include_hidden: tuple(jobs.values()))
    artifacts = SimpleNamespace(primary_path=primary_path)
    library = SimpleNamespace(
        discover=lambda: (
            DiscoveredModel(downloaded, (downloaded,), 0, "repo", {}),
            DiscoveredModel(stray, (stray,), 0, "loose", {}),
        )
    )

    models = local_models(cast(Any, downloads), cast(Any, artifacts), cast(Any, library))

    assert models == (
        LocalModel(downloaded, "done", "owner/repo", "Q4"),
        LocalModel(stray),
    )
