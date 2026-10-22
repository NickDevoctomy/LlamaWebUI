import asyncio
import json
import time
from pathlib import Path

import pytest
from conftest import Login
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.model_manifest import HubFile, group_gguf_files
from llamawebui.domain.model_profile import ModelProfile, ProfileValidationError
from llamawebui.domain.profile_arguments import ProfileArgument
from llamawebui.domain.recommendation import (
    RECOMMENDATION_FORMAT,
    RecommendationError,
    assess,
    load_recommendations,
    parse_recommendation,
    select_group,
)
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.models import ModelProfileRecord, RuntimeRecord
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.huggingface_catalog import ModelSearchResult, RepositoryManifest
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.recommendations import recommended_profile
from llamawebui.services.runtime_probe import RuntimeProbeResult

GIB = 1024**3
STRIX_CPU = "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S"
REPO = "unsloth/Qwen3.8-Flash-Next-GGUF"
SHARDS = tuple(f"Qwen3.8-Flash-Next-UD-IQ4_XS-{index:05d}-of-00002.gguf" for index in (1, 2))


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


def _app(tmp_path: Path, transfer: object, catalog: FakeCatalog | None = None) -> object:
    return create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=_fake_probe_factory(),  # type: ignore[arg-type]
        catalog=catalog or FakeCatalog(),
        file_transfer=transfer,  # type: ignore[arg-type]
        hardware_detector=lambda: HardwareProfile(
            cpu=STRIX_CPU, memory_bytes=95 * GIB, gpus=("AMD Radeon 8060S",), discrete_vram_bytes=0
        ),
        recommendations=(parse_recommendation(_document()),),
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
        assert all(not argument.is_raw for argument in profile.arguments)
    by_backend = {
        item.tested_on.backend: item for item in recommendations if item.model.repo_id == REPO
    }
    strix, nvidia = by_backend["vulkan"], by_backend["cuda"]
    assert strix.model.quantization == nvidia.model.quantization == "UD-IQ4_XS"
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
    assert item["profile_id"] is None
    assert item["profile"]["arguments"] == [{"id": "ctx-size", "value": 262144}]


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
    assert listed["recommendations"][0]["profile_id"] == profiles[0]["id"]
    assert again.status_code == 409
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
