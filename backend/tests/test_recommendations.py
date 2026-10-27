import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Login
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.model_manifest import HubFile, group_gguf_files
from llamawebui.domain.model_profile import ModelProfile, ProfileValidationError, launch_hash
from llamawebui.domain.profile_arguments import ProfileArgument
from llamawebui.domain.recommendation import (
    RECOMMENDATION_FORMAT,
    RecommendationError,
    assess,
    load_recommendations,
    parse_recommendation,
    parse_recommendations,
    select_group,
    select_model_group,
)
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.models import ModelProfileRecord, RuntimeRecord
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.huggingface_catalog import ModelSearchResult, RepositoryManifest
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.recommendations import (
    find_download,
    is_modified,
    next_free_alias,
    recommended_profile,
)
from llamawebui.services.runtime_probe import RuntimeProbeResult

GIB = 1024**3
STRIX_CPU = "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S"
REPO = "unsloth/Qwen3.8-Flash-Next-GGUF"
SHARDS = tuple(f"Qwen3.8-Flash-Next-UD-IQ4_XS-{index:05d}-of-00002.gguf" for index in (1, 2))
DRAFT_FILE = "MTP/mtp-Qwen3.8-Flash-Next-BF16.gguf"


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "format": RECOMMENDATION_FORMAT,
        "id": "test-model-gpu",
        "title": "Test model",
        "summary": "Tuned for testing.",
        "model": {"repo_id": REPO, "quantization": "UD-IQ4_XS", "revision": None},
        "tested_on": {
            "label": "Strix Halo, 96 GB",
            "cpu_pattern": r"Ryzen AI Max\+? (PRO )?395",
            "memory_gb": 96,
            "unified_memory": True,
            "backend": "vulkan",
        },
        "requirements": {"memory_gb": 96, "backends": ["Vulkan", "rocm"]},
        "profile": {
            "alias": "qwen-recommended",
            "description": "Long context for a unified-memory APU.",
            "exclusive": True,
            "arguments": [{"id": "ctx-size", "value": 262144}],
        },
    }
    document.update(overrides)
    return document


def _files() -> tuple[HubFile, ...]:
    return (
        HubFile(SHARDS[0], 3),
        HubFile(SHARDS[1], 4),
        HubFile("Qwen3.8-Flash-Next-Q8_0.gguf", 8),
        HubFile("mmproj-UD-IQ4_XS.gguf", 2),
        HubFile(DRAFT_FILE, 5),
    )


class FakeCatalog:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str | None]] = []

    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 25
    ) -> tuple[ModelSearchResult, ...]:
        return ()

    async def repository(self, repo_id: str, *, revision: str | None = None) -> RepositoryManifest:
        self.requested.append((repo_id, revision))
        return RepositoryManifest(
            repo_id=repo_id, revision="b" * 40, groups=group_gguf_files(_files())
        )


class ImmediateTransfer:
    async def download(
        self, *, repo_id: str, filename: str, revision: str, destination: Path
    ) -> Path:
        size = next(file.size for file in _files() if file.path == filename) or 0
        target = destination / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
        return target


class BlockingTransfer:
    async def download(
        self, *, repo_id: str, filename: str, revision: str, destination: Path
    ) -> Path:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _fake_probe_factory() -> object:
    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="b11070", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "ctx-size", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    return fake_probe


def _app(
    tmp_path: Path,
    transfer: object,
    catalog: FakeCatalog | None = None,
    documents: tuple[dict[str, object], ...] | None = None,
) -> object:
    return create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=_fake_probe_factory(),  # type: ignore[arg-type]
        catalog=catalog or FakeCatalog(),
        file_transfer=transfer,  # type: ignore[arg-type]
        hardware_detector=lambda: HardwareProfile(
            cpu=STRIX_CPU, memory_bytes=95 * GIB, gpus=("AMD Radeon 8060S",), discrete_vram_bytes=0
        ),
        recommendations=tuple(
            parse_recommendation(document) for document in (documents or (_document(),))
        ),
    )


def _register_runtime(client: TestClient, tmp_path: Path, backend: str = "vulkan") -> str:
    executable = tmp_path / f"llama-server-{backend}.exe"
    executable.touch()
    response = client.post(
        "/api/runtimes",
        json={"name": backend.upper(), "executable_path": str(executable), "backend": backend},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _wait_for(
    client: TestClient, predicate: object, timeout: float = 5.0
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout
    while True:
        profiles = client.get("/api/profiles").json()
        if predicate(profiles) or time.monotonic() > deadline:  # type: ignore[operator]
            return profiles  # type: ignore[no-any-return]
        time.sleep(0.05)


def test_bundled_recommendations_load_and_use_valid_catalog_arguments() -> None:
    recommendations = load_recommendations()
    catalog = default_catalog()

    assert recommendations, "at least one recommendation is bundled"
    for recommendation in recommendations:
        profile = recommended_profile(recommendation, catalog, Path("model.gguf"))
        assert len(profile.arguments) == len(recommendation.arguments)
        # Only options a newer runtime advertises may go beyond the catalog.
        assert all(
            not argument.is_raw or argument.origin == "runtime" for argument in profile.arguments
        )
        # Bundled profiles are tuned to fill the machine, so each one runs alone.
        assert recommendation.exclusive is True
        assert recommendation.description
    by_id = {item.id: item for item in recommendations}
    strix = by_id["qwen3.8-flash-next-ud-iq4-xs-strix-halo"]
    nvidia = by_id["qwen3.8-flash-next-ud-iq4-xs-nvidia-24gb"]
    mtp = by_id["qwen3.8-flash-next-ud-iq4-xs-mtp-nvidia-24gb"]
    assert strix.model.quantization == nvidia.model.quantization == "UD-IQ4_XS"
    assert mtp.model.draft_group == "MTP/mtp-Qwen3.8-Flash-Next-BF16"
    assert strix.model.draft_group == "MTP/mtp-Qwen3.8-Flash-Next-Q8_0"
    assert mtp.alias == "qwen3.8-flash-next-mtp-24gb"
    dense = by_id["qwen3.8-27b-ud-q4-k-m-nvidia-24gb"]
    assert dense.model.quantization == "UD-Q4_K_M"
    assert dense.model.draft_group is None
    assert dense.vram_gb == 24
    small = by_id["qwen3.8-27b-gsq-rco-iq3-s-nvidia-16gb"]
    assert small.family == dense.id
    assert small.version == dense.version
    assert (small.label, small.alias, small.vram_gb) == ("16 GB VRAM", "qwen3.8-27b-16gb", 16)
    assert small.model.group == "Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp"
    # The context is smaller, and the 24 GB tuning of batches, threads and drafting is not
    # carried over to a card it was not measured on.
    assert [item for item in small.arguments if item not in dense.arguments] == [
        {"id": "ctx-size", "value": 98304},
        {"id": "batch-size", "value": 512},
        {"id": "ubatch-size", "value": 256},
        {"id": "threads", "value": 8},
        {"id": "threads-batch", "value": 8},
        {"id": "spec-draft-n-max", "value": 8},
        {"id": "spec-draft-p-min", "value": 0},
    ]
    nvidia_host = {
        "cpu": "Intel(R) Core(TM) i7-14700F",
        "memory_bytes": 128 * GIB,
        "discrete_vram_bytes": 24564 * 1024**2,
        "gpus": ("NVIDIA GeForce RTX 4090",),
    }
    strix_host = {
        "cpu": STRIX_CPU,
        "memory_bytes": 95 * GIB,
        "gpus": ("AMD Radeon(TM) 8060S Graphics",),
    }
    # Each bundled profile matches its own kind of machine and not the other.
    assert assess(strix, **strix_host).hardware_match  # type: ignore[arg-type]
    assert not assess(strix, **nvidia_host).hardware_match  # type: ignore[arg-type]
    assert assess(nvidia, **nvidia_host).hardware_match  # type: ignore[arg-type]
    assert assess(nvidia, **nvidia_host).fit == "fits"  # type: ignore[arg-type]
    assert not assess(nvidia, **strix_host).hardware_match  # type: ignore[arg-type]
    assert assess(mtp, **nvidia_host).hardware_match  # type: ignore[arg-type]
    assert assess(dense, **nvidia_host).fit == "fits"  # type: ignore[arg-type]
    assert assess(small, **nvidia_host).hardware_match  # type: ignore[arg-type]
    sixteen = {**nvidia_host, "discrete_vram_bytes": 16303 * 1024**2, "memory_bytes": 16 * GIB}
    assert assess(small, **sixteen).hardware_match  # type: ignore[arg-type]
    assert not assess(dense, **sixteen).hardware_match  # type: ignore[arg-type]
    assert not assess(dense, **strix_host).hardware_match  # type: ignore[arg-type]
    unified = by_id["qwen3.8-27b-ud-q8-k-l-strix-halo"]
    assert unified.family == dense.id
    assert (unified.label, unified.alias, unified.vram_gb) == (
        "96 GB Strix Halo",
        "qwen3.8-27b-96gb",
        None,
    )
    assert unified.model.quantization == "UD-Q8_K_L"
    # Only what was measured on the APU: the 24 GB card's batches, threads and sampling go.
    assert {item["id"] for item in unified.arguments} == {
        "ctx-size",
        "flash-attn",
        "cache-type-k",
        "cache-type-v",
        "spec-type",
        "spec-draft-n-max",
        "spec-draft-n-min",
        "spec-draft-p-min",
    }
    assert assess(unified, **strix_host).hardware_match  # type: ignore[arg-type]
    assert not assess(unified, **nvidia_host).hardware_match  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"format": "other"}, "unsupported recommendation format"),
        ({"id": "Bad ID"}, "invalid recommendation id"),
        ({"title": ""}, "'title' must be a non-empty string"),
        ({"model": "unsloth"}, "'model' must be an object"),
        ({"requirements": {"memory_gb": 96, "backends": []}}, "requirements.backends"),
        ({"requirements": {"memory_gb": 0, "backends": ["cpu"]}}, "'memory_gb' must be a positive"),
        ({"profile": {"alias": "x", "arguments": ["ctx-size"]}}, "profile.arguments"),
        ({"profile": {"alias": "x", "arguments": [], "exclusive": "yes"}}, "profile.exclusive"),
        ({"profile": {"alias": "x", "arguments": [], "description": ""}}, "'description'"),
        (
            {
                "model": {"repo_id": REPO, "quantization": "Q8_0", "draft_group": "MTP/x"},
                "profile": {"alias": "x", "arguments": [{"id": "spec-draft-model", "value": "d"}]},
            },
            "must not set spec-draft-model",
        ),
    ],
)
def test_parse_recommendation_rejects_malformed_documents(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(RecommendationError, match=message):
        parse_recommendation(_document(**overrides))


@pytest.mark.parametrize("key", ["cpu_pattern", "gpu_pattern"])
def test_parse_recommendation_rejects_invalid_hardware_patterns(key: str) -> None:
    tested = dict(_document()["tested_on"])  # type: ignore[call-overload]
    tested[key] = "Ryzen ("
    with pytest.raises(RecommendationError, match=f"{key} is invalid"):
        parse_recommendation(_document(tested_on=tested))


def test_parse_recommendation_rejects_invalid_vram() -> None:
    with pytest.raises(RecommendationError, match="'vram_gb' must be a positive"):
        parse_recommendation(
            _document(requirements={"memory_gb": 96, "vram_gb": 0, "backends": ["cuda"]})
        )


def test_load_recommendations_reports_file_and_duplicate_errors(tmp_path: Path) -> None:
    assert load_recommendations(tmp_path / "missing") == ()
    (tmp_path / "a.json").write_text(json.dumps(_document()), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(_document()), encoding="utf-8")
    with pytest.raises(RecommendationError, match="duplicate recommendation ids: test-model-gpu"):
        load_recommendations(tmp_path)

    (tmp_path / "b.json").write_text(json.dumps(_document(id="other")), encoding="utf-8")
    with pytest.raises(
        RecommendationError, match="duplicate recommendation profile aliases: qwen-recommended"
    ):
        load_recommendations(tmp_path)

    (tmp_path / "b.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RecommendationError, match="b.json: recommendation must be a JSON object"):
        load_recommendations(tmp_path)

    (tmp_path / "b.json").write_text("{", encoding="utf-8")
    with pytest.raises(RecommendationError, match="b.json"):
        load_recommendations(tmp_path)

    (tmp_path / "b.json").write_text(json.dumps(_document(id="x", title=None)), encoding="utf-8")
    with pytest.raises(RecommendationError, match="b.json: recommendation field 'title'"):
        load_recommendations(tmp_path)


def test_assess_grades_memory_and_matches_the_tested_cpu() -> None:
    recommendation = parse_recommendation(_document())

    fits = assess(recommendation, cpu=STRIX_CPU, memory_bytes=95 * GIB)
    tight = assess(
        recommendation,
        cpu="Intel Core i9-14900K",
        memory_bytes=64 * GIB,
        discrete_vram_bytes=16 * GIB,
    )
    short = assess(recommendation, cpu=None, memory_bytes=32 * GIB)

    assert (fits.fit, fits.hardware_match) == ("fits", True)
    assert (tight.fit, tight.hardware_match, tight.available_memory_gb) == ("tight", False, 80)
    assert (short.fit, short.hardware_match) == ("insufficient", False)
    assert recommendation.backends == ("vulkan", "rocm")


def test_assess_matches_a_gpu_family_with_enough_vram() -> None:
    tested = {"label": "24 GB NVIDIA", "gpu_pattern": "NVIDIA", "memory_gb": 128, "backend": "cuda"}
    recommendation = parse_recommendation(
        _document(
            tested_on=tested,
            requirements={"memory_gb": 152, "vram_gb": 24, "backends": ["cuda"]},
        )
    )

    def matches(gpus: tuple[str, ...], vram_gib: float) -> bool:
        return assess(
            recommendation,
            cpu="Any CPU",
            memory_bytes=128 * GIB,
            discrete_vram_bytes=int(vram_gib * GIB),
            gpus=gpus,
        ).hardware_match

    assert recommendation.vram_gb == 24
    assert recommendation.to_payload()["requirements"] == {
        "memory_gb": 152,
        "vram_gb": 24,
        "backends": ["cuda"],
    }
    assert matches(("NVIDIA GeForce RTX 4090",), 23.99)
    assert matches(("NVIDIA RTX A5000",), 24)
    assert matches(("NVIDIA GeForce RTX 5090",), 32)
    assert not matches(("NVIDIA GeForce RTX 4080",), 16)
    assert not matches(("AMD Radeon RX 7900 XTX",), 24)


def test_a_recommendation_without_hardware_patterns_never_claims_a_match() -> None:
    tested = {"label": "Any machine", "memory_gb": 16, "backend": "cpu"}
    recommendation = parse_recommendation(_document(tested_on=tested))

    assert not assess(recommendation, cpu=STRIX_CPU, memory_bytes=95 * GIB).hardware_match


def test_select_group_picks_the_complete_quantization_and_skips_projectors() -> None:
    groups = group_gguf_files(_files())

    assert select_group(groups, "ud-iq4_xs").files[0].path == SHARDS[0]
    with pytest.raises(RecommendationError, match="has no Q2_K files"):
        select_group(groups, "Q2_K")
    with pytest.raises(RecommendationError, match="missing shards"):
        select_group(group_gguf_files((HubFile(SHARDS[0], 3),)), "UD-IQ4_XS")
    duplicated = group_gguf_files(
        (HubFile("a/Model-UD-IQ4_XS.gguf", 1), HubFile("b/Model-UD-IQ4_XS.gguf", 1))
    )
    with pytest.raises(RecommendationError, match="more than one UD-IQ4_XS group"):
        select_group(duplicated, "UD-IQ4_XS")


def test_recommendations_list_reports_fit_runtimes_and_existing_profiles(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, BlockingTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        vulkan = _register_runtime(client, tmp_path, "vulkan")
        _register_runtime(client, tmp_path, "cuda")
        listed = client.get("/api/profiles/recommendations")

    payload = listed.json()
    assert listed.status_code == 200
    assert payload["host"]["cpu"] == STRIX_CPU
    item = payload["recommendations"][0]
    assert item["assessment"] == {
        "fit": "fits",
        "hardware_match": True,
        "available_memory_gb": 95.0,
    }
    assert item["compatible_runtime_ids"] == [vulkan]
    assert item["download"] is None
    assert item["matches"] == {}
    assert item["profile_ids"] == []
    assert item["suggested_alias"] == "qwen-recommended"
    assert item["profile"]["arguments"] == [{"id": "ctx-size", "value": 262144}]
    assert item["profile"]["exclusive"] is True
    assert item["profile"]["description"] == "Long context for a unified-memory APU."


def test_apply_queues_download_and_enables_profile_when_it_completes(
    tmp_path: Path, login: Login
) -> None:
    catalog = FakeCatalog()
    with TestClient(_app(tmp_path, ImmediateTransfer(), catalog)) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        profiles = _wait_for(client, lambda items: items and items[0]["enabled"])
        listed = client.get("/api/profiles/recommendations").json()
        again = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        unknown = client.post(
            "/api/profiles/recommendations/missing/apply", json={"runtime_id": runtime_id}
        )

    assert applied.status_code == 201, applied.text
    body = applied.json()
    assert body["ready"] is False
    assert body["profile"]["enabled"] is False
    assert body["profile"]["auto_enable"] is True
    assert body["profile"]["model_path"].endswith(SHARDS[0])
    assert body["download"]["group_key"] == "Qwen3.8-Flash-Next-UD-IQ4_XS"
    assert catalog.requested[0] == (REPO, None)
    assert profiles[0]["enabled"] is True
    assert profiles[0]["auto_enable"] is False
    assert profiles[0]["validation_state"] == "unverified"
    assert "ctx-size = 262144" in profiles[0]["preset"]
    item = listed["recommendations"][0]
    assert item["matches"] == {runtime_id: [profiles[0]["id"]]}
    assert item["profile_ids"] == [profiles[0]["id"]]
    assert item["download"]["state"] == "completed"
    assert item["suggested_alias"] == "qwen-recommended-2"
    assert again.status_code == 409
    assert again.json()["detail"] == "this profile is already added as qwen-recommended"
    assert unknown.status_code == 404


def test_apply_reports_pending_profile_while_downloading(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, BlockingTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        profiles = client.get("/api/profiles").json()

    assert applied.status_code == 201, applied.text
    assert profiles[0]["validation_state"] == "pending"
    assert profiles[0]["enabled"] is False
    assert profiles[0]["exclusive"] is True
    assert profiles[0]["description"] == "Long context for a unified-memory APU."
    assert profiles[0]["download"]["total_bytes"] == 7
    assert profiles[0]["download"]["state"] in {"queued", "downloading"}


def test_apply_reuses_a_finished_download_and_enables_immediately(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        queued = client.post(
            "/api/downloads",
            json={"repo_id": REPO, "revision": "main", "group_key": "Qwen3.8-Flash-Next-UD-IQ4_XS"},
        )
        deadline = time.monotonic() + 5
        while client.get("/api/downloads").json()[0]["state"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        downloads = client.get("/api/downloads").json()

    assert queued.status_code == 201
    assert applied.status_code == 201, applied.text
    assert applied.json()["ready"] is True
    assert applied.json()["profile"]["enabled"] is True
    assert applied.json()["profile"]["exclusive"] is True
    assert applied.json()["profile"]["description"] == "Long context for a unified-memory APU."
    assert len(downloads) == 1


def test_apply_rejects_unknown_runtime_without_queuing(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path, BlockingTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": "missing"}
        )
        downloads = client.get("/api/downloads").json()

    assert applied.status_code == 404
    assert downloads == []


def test_apply_reports_a_missing_quantization(tmp_path: Path, login: Login) -> None:
    class EmptyCatalog(FakeCatalog):
        async def repository(
            self, repo_id: str, *, revision: str | None = None
        ) -> RepositoryManifest:
            return RepositoryManifest(repo_id=repo_id, revision="b" * 40, groups=())

    with TestClient(_app(tmp_path, BlockingTransfer(), EmptyCatalog())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )

    assert applied.status_code == 422
    assert "has no UD-IQ4_XS files" in applied.json()["detail"]


def _registry(tmp_path: Path) -> tuple[ProfileRegistry, str]:
    database = tmp_path / "app.db"
    upgrade_database(database)
    engine = create_database_engine(database)
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(
            RuntimeRecord(
                id="runtime-1",
                name="Vulkan",
                executable_path=str(tmp_path / "llama-server.exe"),
                options=["model", "ctx-size"],
                devices=[],
                help_sha256="0" * 64,
            )
        )
        session.commit()
    return ProfileRegistry(engine, default_catalog()), "runtime-1"


def test_pending_profile_stays_disabled_when_its_files_are_still_missing(tmp_path: Path) -> None:
    registry, runtime_id = _registry(tmp_path)
    profile = ModelProfile(
        alias="pending-model",
        model_path=tmp_path / "model.gguf",
        arguments=(ProfileArgument("--ctx-size", 4096, "ctx-size"),),
    )
    record = registry.create_pending(profile=profile, runtime_id=runtime_id, download_id="job-1")

    assert record.enabled is False
    assert registry.awaiting_downloads() == ("job-1",)
    assert registry.enable_ready("job-1") == ()
    assert registry.awaiting_downloads() == ()
    assert registry.get(record.id).enabled is False

    with pytest.raises(ProfileValidationError, match="ctx-size"):
        registry.create_pending(
            profile=ModelProfile(
                alias="bad-model",
                model_path=tmp_path / "model.gguf",
                arguments=(ProfileArgument("--ctx-size", "huge", "ctx-size"),),
            ),
            runtime_id=runtime_id,
            download_id="job-2",
        )


def test_refresh_presets_rebuilds_stale_presets_only_when_valid(tmp_path: Path) -> None:
    registry, runtime_id = _registry(tmp_path)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    current = registry.create(
        profile=ModelProfile(
            alias="current",
            model_path=model,
            arguments=(ProfileArgument("--ctx-size", 4096, "ctx-size"),),
        ),
        runtime_id=runtime_id,
    )
    missing = registry.create_pending(
        profile=ModelProfile(alias="missing", model_path=tmp_path / "gone.gguf"),
        runtime_id=runtime_id,
        download_id="job-1",
    )
    engine = create_database_engine(tmp_path / "app.db")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        for record_id in (current.id, missing.id):
            record = session.get(ModelProfileRecord, record_id)
            assert record is not None
            record.preset = "version = 1\n\n[stale]\nspec-draft-cache-type-k = q4_0\n"
        session.commit()

    assert registry.refresh_presets() == ("current",)
    assert "ctx-size = 4096" in registry.get(current.id).preset
    # Model files are missing, so it cannot be validated and is left as it was.
    assert "spec-draft-cache-type-k" in registry.get(missing.id).preset
    assert registry.refresh_presets() == ()


def test_editing_a_pending_profile_cancels_auto_enable(tmp_path: Path) -> None:
    registry, runtime_id = _registry(tmp_path)
    model = tmp_path / "model.gguf"
    record = registry.create_pending(
        profile=ModelProfile(alias="pending-model", model_path=model),
        runtime_id=runtime_id,
        download_id="job-1",
    )
    model.write_bytes(b"gguf")
    registry.update(
        record.id,
        profile=ModelProfile(alias="pending-model", model_path=model),
        runtime_id=runtime_id,
        enabled=False,
    )

    assert registry.awaiting_downloads() == ()
    assert registry.enable_ready("job-1") == ()


def _profile_body(runtime_id: str, model: str, context: int) -> dict[str, object]:
    return {
        "alias": "qwen-recommended",
        "runtime_id": runtime_id,
        "model_path": model,
        "enabled": True,
        "exclusive": True,
        "arguments": [{"id": "ctx-size", "value": context}],
    }


def _upgraded_document() -> dict[str, object]:
    return _document(
        version=2,
        profile={
            "alias": "qwen-recommended",
            "description": "Shorter context, faster prompts.",
            "exclusive": True,
            "arguments": [{"id": "ctx-size", "value": 131072}],
        },
    )


def _apply_and_wait(client: TestClient, tmp_path: Path) -> tuple[str, dict[str, object]]:
    runtime_id = _register_runtime(client, tmp_path)
    applied = client.post(
        "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
    )
    assert applied.status_code == 201, applied.text
    profiles = _wait_for(client, lambda items: items and items[0]["enabled"])
    return runtime_id, profiles[0]


def test_an_unchanged_recommended_profile_upgrades_and_keeps_its_place(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        _, added = _apply_and_wait(client, tmp_path)
        too_early = client.post(f"/api/profiles/{added['id']}/upgrade")

    assert added["version"] == 1
    assert added["recommendation"] == {
        "id": "test-model-gpu",
        "title": "Test model",
        "latest_version": 1,
        "modified": False,
        "upgradable": False,
        "baseline": {"version": 1, "arguments": [{"id": "ctx-size", "value": 262144}]},
        "latest": {
            "version": 1,
            "arguments": [{"id": "ctx-size", "value": 262144}],
            "exclusive": True,
            "description": "Long context for a unified-memory APU.",
        },
    }
    assert too_early.status_code == 409
    assert too_early.json()["detail"] == "qwen-recommended is already at version 1"

    newer = _app(tmp_path, ImmediateTransfer(), documents=(_upgraded_document(),))
    with TestClient(newer) as client:  # type: ignore[arg-type]
        login(client)
        listed = client.get("/api/profiles").json()[0]
        upgraded = client.post(f"/api/profiles/{added['id']}/upgrade")
        missing = client.post("/api/profiles/missing/upgrade")

    assert listed["recommendation"]["upgradable"] is True
    assert listed["recommendation"]["latest_version"] == 2
    assert upgraded.status_code == 200, upgraded.text
    body = upgraded.json()
    assert body["version"] == 2
    assert body["recommendation"]["upgradable"] is False
    assert body["recommendation"]["modified"] is False
    assert body["description"] == "Shorter context, faster prompts."
    assert body["enabled"] is True
    assert body["alias"] == "qwen-recommended"
    assert body["model_path"] == added["model_path"]
    assert "ctx-size = 131072" in body["preset"]
    assert missing.status_code == 404


def test_a_changed_recommended_profile_keeps_its_version_and_cannot_upgrade(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id, added = _apply_and_wait(client, tmp_path)
        edited = client.put(
            f"/api/profiles/{added['id']}",
            json={**_profile_body(runtime_id, str(added["model_path"]), 4096), "version": 9},
        )

    assert edited.status_code == 200, edited.text
    # The version belongs to the recommendation, so neither an edit nor a typed one moves it.
    assert edited.json()["version"] == 1
    assert edited.json()["recommendation"]["modified"] is True

    newer = _app(tmp_path, ImmediateTransfer(), documents=(_upgraded_document(),))
    with TestClient(newer) as client:  # type: ignore[arg-type]
        login(client)
        listed = client.get("/api/profiles").json()[0]
        refused = client.post(f"/api/profiles/{added['id']}/upgrade")

    assert listed["recommendation"]["upgradable"] is False
    # Both what it was added with and the newer version are there to compare against.
    assert listed["recommendation"]["baseline"] == {
        "version": 1,
        "arguments": [{"id": "ctx-size", "value": 262144}],
    }
    assert listed["recommendation"]["latest"]["version"] == 2
    assert listed["recommendation"]["latest"]["arguments"] == [{"id": "ctx-size", "value": 131072}]
    assert listed["recommendation"]["latest"]["description"] == "Shorter context, faster prompts."
    assert refused.status_code == 409
    assert "was changed after it was added" in refused.json()["detail"]


def test_startup_links_only_profiles_that_match_a_recommendation_exactly(
    tmp_path: Path, login: Login
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    other = _document(
        id="other-model",
        profile={"alias": "other-model", "arguments": [{"id": "ctx-size", "value": 1000}]},
    )
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        same = client.post("/api/profiles", json=_profile_body(runtime_id, str(model), 262144))
        changed = client.post(
            "/api/profiles",
            json={**_profile_body(runtime_id, str(model), 2000), "alias": "other-model"},
        )
    assert same.json()["recommendation"] is None

    restarted = _app(tmp_path, ImmediateTransfer(), documents=(_document(), other))
    with TestClient(restarted) as client:  # type: ignore[arg-type]
        login(client)
        listed = {item["alias"]: item for item in client.get("/api/profiles").json()}

    assert changed.status_code == 201
    assert listed["qwen-recommended"]["recommendation"]["id"] == "test-model-gpu"
    assert listed["qwen-recommended"]["recommendation"]["modified"] is False
    assert listed["other-model"]["recommendation"] is None


def test_launch_hash_ignores_alias_and_option_order_only() -> None:
    catalog = default_catalog()
    context = ProfileArgument("--ctx-size", 4096, catalog_id="ctx-size")
    threads = ProfileArgument("--threads", 8, catalog_id="threads")
    model = Path("model.gguf")
    runtime = Path("llama-server.exe")
    both = (context, threads)
    base = launch_hash(ModelProfile("one", model, both), catalog, runtime)

    assert launch_hash(ModelProfile("two", model, (threads, context)), catalog, runtime) == base
    assert launch_hash(ModelProfile("one", model, (context,)), catalog, runtime) != base
    assert launch_hash(ModelProfile("one", Path("other.gguf"), both), catalog, runtime) != base
    assert launch_hash(ModelProfile("one", model, both), catalog, Path("cuda.exe")) != base


def test_profiles_with_the_same_command_share_a_hash(tmp_path: Path, login: Login) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        body = _profile_body(runtime_id, str(model), 4096)
        first = client.post("/api/profiles", json=body).json()
        clone = client.post(f"/api/profiles/{first['id']}/clone", json={"alias": "copy"}).json()
        other = client.post(
            "/api/profiles", json={**_profile_body(runtime_id, str(model), 8192), "alias": "other"}
        ).json()

    assert len(first["hash"]) == 64
    assert clone["hash"] == first["hash"]
    assert other["hash"] != first["hash"]


def test_a_changed_profile_lets_the_original_be_added_again_under_a_new_alias(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id, added = _apply_and_wait(client, tmp_path)
        client.put(
            f"/api/profiles/{added['id']}",
            json=_profile_body(runtime_id, str(added["model_path"]), 4096),
        )
        changed = client.get("/api/profiles/recommendations").json()["recommendations"][0]
        taken = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        copy = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply",
            json={"runtime_id": runtime_id, "alias": changed["suggested_alias"]},
        )
        listed = client.get("/api/profiles/recommendations").json()["recommendations"][0]
        downloads = client.get("/api/downloads").json()

    assert changed["matches"] == {}
    assert changed["profile_ids"] == [added["id"]]
    assert changed["suggested_alias"] == "qwen-recommended-2"
    assert taken.status_code == 409
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body["ready"] is True
    assert body["profile"]["alias"] == "qwen-recommended-2"
    assert body["profile"]["enabled"] is True
    assert body["profile"]["recommendation"]["modified"] is False
    assert listed["matches"] == {runtime_id: [body["profile"]["id"]]}
    assert sorted(listed["profile_ids"]) == sorted([added["id"], body["profile"]["id"]])
    assert len(downloads) == 1


def test_apply_can_download_the_model_without_adding_a_profile(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply",
            json={"runtime_id": runtime_id, "add_profile": False},
        )
        deadline = time.monotonic() + 5
        while client.get("/api/downloads").json()[0]["state"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        listed = client.get("/api/profiles/recommendations").json()["recommendations"][0]
        profiles = client.get("/api/profiles").json()

    assert applied.status_code == 201, applied.text
    assert applied.json()["profile"] is None
    assert profiles == []
    assert listed["download"]["state"] == "completed"
    assert listed["matches"] == {}


def test_find_download_matches_repo_revision_and_quantization() -> None:
    def job(state: str, group_key: str = "Model-UD-IQ4_XS", revision: str = "a" * 40) -> object:
        return SimpleNamespace(repo_id=REPO, revision=revision, group_key=group_key, state=state)

    recommendation = parse_recommendation(_document())
    pinned = parse_recommendation(
        _document(model={"repo_id": REPO, "quantization": "UD-IQ4_XS", "revision": "c" * 40})
    )
    paused, done = job("paused"), job("completed")

    assert find_download(recommendation, [paused, done]) is done  # type: ignore[list-item]
    assert find_download(recommendation, [paused]) is paused  # type: ignore[list-item]
    assert find_download(recommendation, [job("failed"), job("completed", "Model-Q8_0")]) is None  # type: ignore[list-item]
    assert find_download(recommendation, [job("completed", "mmproj-UD-IQ4_XS")]) is None  # type: ignore[list-item]
    assert find_download(pinned, [done]) is None  # type: ignore[list-item]


class GatedDraftTransfer(ImmediateTransfer):
    """Finishes the main model at once and holds the draft model until the gate opens."""

    def __init__(self) -> None:
        self.gate = threading.Event()

    async def download(
        self, *, repo_id: str, filename: str, revision: str, destination: Path
    ) -> Path:
        if filename == DRAFT_FILE:
            while not self.gate.is_set():
                await asyncio.sleep(0.02)
        return await super().download(
            repo_id=repo_id, filename=filename, revision=revision, destination=destination
        )


def _draft_document() -> dict[str, object]:
    return _document(
        model={
            "repo_id": REPO,
            "quantization": "UD-IQ4_XS",
            "revision": None,
            "draft_group": "MTP/mtp-Qwen3.8-Flash-Next-BF16",
        }
    )


def test_a_draft_model_is_downloaded_and_the_profile_waits_for_both_files(
    tmp_path: Path, login: Login
) -> None:
    transfer = GatedDraftTransfer()
    app = _app(tmp_path, transfer, documents=(_draft_document(),))
    with TestClient(app) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        deadline = time.monotonic() + 5
        while not any(
            job["state"] == "completed" and "mtp" not in job["group_key"].lower()
            for job in client.get("/api/downloads").json()
        ):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        waiting = client.get("/api/profiles").json()[0]
        transfer.gate.set()
        profiles = _wait_for(client, lambda items: items and items[0]["enabled"])
        listed = client.get("/api/profiles/recommendations").json()["recommendations"][0]
        downloads = client.get("/api/downloads").json()

    assert applied.status_code == 201, applied.text
    assert sorted(job["group_key"] for job in downloads) == [
        "MTP/mtp-Qwen3.8-Flash-Next-BF16",
        "Qwen3.8-Flash-Next-UD-IQ4_XS",
    ]
    # The model finished first; the profile kept waiting for its draft model.
    assert waiting["enabled"] is False
    assert waiting["auto_enable"] is True
    assert profiles[0]["enabled"] is True
    assert "spec-draft-model" in profiles[0]["preset"]
    assert profiles[0]["preset"].rstrip().endswith("mtp-Qwen3.8-Flash-Next-BF16.gguf")
    assert listed["draft_download"]["state"] == "completed"
    assert listed["matches"] == {runtime_id: [profiles[0]["id"]]}
    assert profiles[0]["recommendation"]["modified"] is False
    # The recommended settings carry this machine's draft path, so it never shows as a change.
    draft = next(
        item for item in profiles[0]["configuration"]["arguments"]
        if item.get("id") == "spec-draft-model"
    )
    assert draft in profiles[0]["recommendation"]["baseline"]["arguments"]
    assert draft in profiles[0]["recommendation"]["latest"]["arguments"]


def test_a_missing_draft_model_group_is_reported(tmp_path: Path, login: Login) -> None:
    document = _draft_document()
    document["model"] = {**document["model"], "draft_group": "MTP/missing"}  # type: ignore[dict-item]
    with TestClient(_app(tmp_path, BlockingTransfer(), documents=(document,))) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        applied = client.post(
            "/api/profiles/recommendations/test-model-gpu/apply", json={"runtime_id": runtime_id}
        )
        downloads = client.get("/api/downloads").json()

    assert applied.status_code == 422
    assert applied.json()["detail"] == "the repository has no draft model MTP/missing"
    assert downloads == []


def test_next_free_alias_adds_the_first_free_number() -> None:
    assert next_free_alias("qwen", []) == "qwen"
    assert next_free_alias("qwen", ["qwen"]) == "qwen-2"
    assert next_free_alias("qwen", ["qwen", "qwen-2", "qwen-4"]) == "qwen-3"


def test_file_locations_never_count_as_a_change() -> None:
    catalog = default_catalog()
    baseline = [{"id": "ctx-size", "value": 4096}]
    record = SimpleNamespace(
        recommendation_baseline=baseline,
        configuration={
            "schema_version": 2,
            "arguments": [
                {"id": "spec-draft-model", "value": "E:\models\draft.gguf"},
                {"id": "ctx-size", "value": 4096},
            ],
        },
    )

    assert is_modified(record, catalog) is False  # type: ignore[arg-type]
    record.configuration["arguments"][1]["value"] = 8192  # type: ignore[index]
    assert is_modified(record, catalog) is True  # type: ignore[arg-type]
    unlinked = SimpleNamespace(recommendation_baseline=None)
    assert is_modified(unlinked, catalog) is True  # type: ignore[arg-type]


def _family_document() -> dict[str, object]:
    return _document(
        label="96 GB",
        profile={
            "alias": "qwen-recommended",
            "description": "Long context for a unified-memory APU.",
            "exclusive": True,
            "arguments": [
                {"id": "ctx-size", "value": 262144},
                {"id": "parallel", "value": 1},
                {"id": "temperature", "value": 0.6},
            ],
        },
        variants=[
            {
                "id": "test-model-small",
                "label": "Q8 build",
                "title": "Test model, smaller",
                "model": {"quantization": "Q8_0", "group": "Qwen3.8-Flash-Next-Q8_0"},
                "requirements": {"memory_gb": 48},
                "profile": {
                    "alias": "qwen-small",
                    "arguments": [
                        {"id": "ctx-size", "value": 65536},
                        {"id": "threads", "value": 8},
                    ],
                    "remove_arguments": ["parallel"],
                },
            }
        ],
    )


def test_a_variant_is_its_base_with_its_changes_on_top() -> None:
    base, small = parse_recommendations(_family_document())

    assert (base.family, base.label) == ("test-model-gpu", "96 GB")
    assert (small.id, small.family, small.label, small.version) == (
        "test-model-small",
        "test-model-gpu",
        "Q8 build",
        1,
    )
    assert small.title == "Test model, smaller"
    assert small.summary == base.summary
    assert small.model.repo_id == REPO
    assert (small.model.quantization, small.model.group) == ("Q8_0", "Qwen3.8-Flash-Next-Q8_0")
    assert small.memory_gb == 48
    assert small.backends == base.backends
    assert small.tested_on == base.tested_on
    assert (small.alias, small.description, small.exclusive) == (
        "qwen-small",
        base.description,
        True,
    )
    assert list(small.arguments) == [
        {"id": "ctx-size", "value": 65536},
        {"id": "temperature", "value": 0.6},
        {"id": "threads", "value": 8},
    ]
    assert small.to_payload()["family"] == "test-model-gpu"
    # A file without variants is one recommendation of its own family.
    (single,) = parse_recommendations(_document())
    assert single.family == single.id
    assert single.label is None


def _variant(**fields: object) -> dict[str, object]:
    return {"variants": [{"id": "other", "label": "x", **fields}]}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"variants": "small"}, "variants must be a list"),
        ({"label": None}, "needs a 'label'"),
        ({"variants": [{"id": "test-model-gpu", "label": "x"}]}, "its own 'id' and 'label'"),
        ({"variants": [{"id": "other"}]}, "its own 'id' and 'label'"),
        (_variant(version=3), "cannot set 'version'"),
        (_variant(variants=[]), "cannot set 'variants'"),
        (_variant(model="Q8"), "'model' must be"),
        (_variant(profile={"remove_arguments": ["fit"]}), "names no base argument: fit"),
        (_variant(profile={"remove_arguments": "fit"}), "remove_arguments must list"),
        (_variant(profile={"arguments": ["ctx-size"]}), r"variants\[0\]: profile.arguments"),
    ],
)
def test_malformed_variants_are_rejected(change: dict[str, object], message: str) -> None:
    with pytest.raises(RecommendationError, match=message):
        parse_recommendations({**_family_document(), **change})


def test_a_variant_alias_must_not_repeat_its_base(tmp_path: Path) -> None:
    document = _family_document()
    document["variants"][0]["profile"]["alias"] = "qwen-recommended"  # type: ignore[index]
    (tmp_path / "a.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RecommendationError, match="duplicate recommendation profile aliases"):
        load_recommendations(tmp_path)


def test_a_named_model_group_is_picked_exactly() -> None:
    groups = group_gguf_files((*_files(), HubFile("Qwen3.8-Flash-Next-Q8_0-mtp.gguf", 9)))
    _, small = parse_recommendations(_family_document())
    plain = parse_recommendation(_document())

    assert select_model_group(groups, small.model).key == "Qwen3.8-Flash-Next-Q8_0"
    assert select_model_group(groups, plain.model).files[0].path == SHARDS[0]
    with pytest.raises(RecommendationError, match="more than one Q8_0 group"):
        select_group(groups, "Q8_0")
    missing = parse_recommendation(
        _document(model={"repo_id": REPO, "quantization": "Q8_0", "group": "Nope-Q8_0"})
    )
    with pytest.raises(RecommendationError, match="has no model Nope-Q8_0"):
        select_model_group(groups, missing.model)
    sharded = parse_recommendation(
        _document(
            model={
                "repo_id": REPO,
                "quantization": "UD-IQ4_XS",
                "group": "Qwen3.8-Flash-Next-UD-IQ4_XS",
            }
        )
    )
    with pytest.raises(RecommendationError, match="missing shards"):
        select_model_group(group_gguf_files((HubFile(SHARDS[0], 3),)), sharded.model)


def test_find_download_matches_a_named_group_only() -> None:
    def job(group_key: str) -> object:
        return SimpleNamespace(
            repo_id=REPO, revision="a" * 40, group_key=group_key, state="completed"
        )

    _, small = parse_recommendations(_family_document())
    plain, mtp = job("Qwen3.8-Flash-Next-Q8_0"), job("Qwen3.8-Flash-Next-Q8_0-mtp")

    assert find_download(small, [mtp, plain]) is plain  # type: ignore[list-item]
    assert find_download(small, [mtp]) is None  # type: ignore[list-item]


def test_variants_are_listed_and_applied_on_their_own(tmp_path: Path, login: Login) -> None:
    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=_fake_probe_factory(),  # type: ignore[arg-type]
        catalog=FakeCatalog(),
        file_transfer=ImmediateTransfer(),  # type: ignore[arg-type]
        hardware_detector=lambda: HardwareProfile(
            cpu=STRIX_CPU, memory_bytes=95 * GIB, gpus=("AMD Radeon 8060S",), discrete_vram_bytes=0
        ),
        recommendations=parse_recommendations(_family_document()),
    )
    with TestClient(app) as client:
        login(client)
        runtime_id = _register_runtime(client, tmp_path)
        listed = client.get("/api/profiles/recommendations").json()["recommendations"]
        applied = client.post(
            "/api/profiles/recommendations/test-model-small/apply", json={"runtime_id": runtime_id}
        )
        assert applied.status_code == 201, applied.text
        profiles = _wait_for(client, lambda items: items and items[0]["enabled"])
        again = client.get("/api/profiles/recommendations").json()["recommendations"]

    assert [(item["id"], item["family"], item["label"]) for item in listed] == [
        ("test-model-gpu", "test-model-gpu", "96 GB"),
        ("test-model-small", "test-model-gpu", "Q8 build"),
    ]
    assert listed[1]["model"]["group"] == "Qwen3.8-Flash-Next-Q8_0"
    (profile,) = profiles
    assert profile["alias"] == "qwen-small"
    assert profile["model_path"].endswith("Qwen3.8-Flash-Next-Q8_0.gguf")
    assert profile["recommendation"]["id"] == "test-model-small"
    assert "ctx-size = 65536" in profile["preset"]
    assert "parallel" not in profile["preset"]
    assert again[0]["profile_ids"] == []
    assert again[1]["profile_ids"] == [profile["id"]]


def test_a_changed_profile_adopted_by_a_newer_version_moves_to_it(
    tmp_path: Path, login: Login
) -> None:
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        runtime_id, added = _apply_and_wait(client, tmp_path)
        edited = client.put(
            f"/api/profiles/{added['id']}",
            json=_profile_body(runtime_id, str(added["model_path"]), 131072),
        )
    assert edited.json()["recommendation"]["modified"] is True

    newer = _app(tmp_path, ImmediateTransfer(), documents=(_upgraded_document(),))
    with TestClient(newer) as client:  # type: ignore[arg-type]
        login(client)
        listed = client.get("/api/profiles").json()[0]

    assert listed["version"] == 2
    assert listed["recommendation"]["modified"] is False
    assert listed["recommendation"]["upgradable"] is False

    # A newer version that did not adopt a later change leaves the profile modified.
    newest = _document(
        version=3,
        profile={"alias": "qwen-recommended", "arguments": [{"id": "ctx-size", "value": 8192}]},
    )
    with TestClient(_app(tmp_path, ImmediateTransfer())) as client:  # type: ignore[arg-type]
        login(client)
        client.put(
            f"/api/profiles/{added['id']}",
            json=_profile_body(runtime_id, str(added["model_path"]), 4096),
        )
    with TestClient(_app(tmp_path, ImmediateTransfer(), documents=(newest,))) as client:  # type: ignore[arg-type]
        login(client)
        listed = client.get("/api/profiles").json()[0]

    assert listed["version"] == 2
    assert listed["recommendation"]["modified"] is True
