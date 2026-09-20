import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.huggingface_catalog import RepositoryManifest
from llamawebui.services.logical_model_registry import LogicalModelRegistry
from llamawebui.services.model_library import ModelLibrary
from llamawebui.services.runtime_probe import RuntimeProbeResult


def test_library_projects_only_complete_valid_downloads(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    manifest = RepositoryManifest(
        repo_id="owner/model-GGUF",
        revision="a" * 40,
        groups=(
            GgufGroup(
                key="Q4/model-Q4",
                quantization="Q4",
                files=(
                    HubFile("Q4/model-00001-of-00002.gguf", 3),
                    HubFile("Q4/model-00002-of-00002.gguf", 4),
                ),
                total_size=7,
                complete=True,
            ),
        ),
    )
    job = registry.create(manifest, "Q4/model-Q4")
    destination = Path(job.destination)
    destination.joinpath("Q4").mkdir(parents=True)
    destination.joinpath("Q4/model-00001-of-00002.gguf").write_bytes(b"one")
    destination.joinpath("Q4/model-00002-of-00002.gguf").write_bytes(b"four")
    registry.transition(job.id, DownloadState.DOWNLOADING)
    registry.update_progress(job.id, 7)
    registry.transition(job.id, DownloadState.COMPLETED)

    models = ModelLibrary(registry, model_root).list()

    assert len(models) == 1
    assert models[0].download_id == job.id
    assert models[0].primary_path == destination / "Q4/model-00001-of-00002.gguf"
    assert models[0].file_count == 2
    assert models[0].total_bytes == 7

    assert registry.clear_terminal() == 1
    assert registry.list() == []
    assert len(ModelLibrary(registry, model_root).list()) == 1

    destination.joinpath("Q4/model-00002-of-00002.gguf").unlink()
    assert ModelLibrary(registry, model_root).list() == ()


def test_library_rejects_checksum_modified_file(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    manifest = RepositoryManifest(
        repo_id="owner/model-GGUF",
        revision="d" * 40,
        groups=(
            GgufGroup(
                key="model-Q4",
                quantization="Q4",
                files=(
                    HubFile(
                        "model-Q4.gguf",
                        8,
                        hashlib.sha256(b"original").hexdigest(),
                    ),
                ),
                total_size=8,
                complete=True,
            ),
        ),
    )
    job = registry.create(manifest, "model-Q4")
    destination = Path(job.destination)
    destination.mkdir(parents=True)
    file_path = destination / "model-Q4.gguf"
    file_path.write_bytes(b"original")
    registry.transition(job.id, DownloadState.DOWNLOADING)
    registry.update_progress(job.id, 8)
    registry.transition(job.id, DownloadState.COMPLETED)

    assert len(ModelLibrary(registry, model_root).list()) == 1

    file_path.write_bytes(b"modified")

    assert ModelLibrary(registry, model_root).list() == ()


def test_library_deduplicates_completed_jobs_for_same_primary_path(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    manifest = RepositoryManifest(
        repo_id="owner/model-GGUF",
        revision="f" * 40,
        groups=(
            GgufGroup(
                key="model-Q4",
                quantization="Q4",
                files=(HubFile("model-Q4.gguf", 4),),
                total_size=4,
                complete=True,
            ),
        ),
    )
    first = registry.create(manifest, "model-Q4")
    second = registry.create(manifest, "model-Q4")
    destination = Path(first.destination)
    destination.mkdir(parents=True)
    (destination / "model-Q4.gguf").write_bytes(b"good")
    registry.transition(first.id, DownloadState.DOWNLOADING)
    registry.update_progress(first.id, 4)
    registry.transition(first.id, DownloadState.COMPLETED)
    registry.transition(second.id, DownloadState.DOWNLOADING)
    registry.update_progress(second.id, 4)
    registry.transition(second.id, DownloadState.COMPLETED)

    models = ModelLibrary(registry, model_root).list()

    assert len(models) == 1
    assert models[0].download_id == first.id


def test_library_endpoint_returns_primary_profile_path(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        registry = app.state.download_registry
        manifest = RepositoryManifest(
            repo_id="owner/model-GGUF",
            revision="b" * 40,
            groups=(
                GgufGroup(
                    key="model-Q8_0",
                    quantization="Q8_0",
                    files=(HubFile("model-Q8_0.gguf", 4),),
                    total_size=4,
                    complete=True,
                ),
            ),
        )
        job = registry.create(manifest, "model-Q8_0")
        destination = Path(job.destination)
        destination.mkdir(parents=True)
        destination.joinpath("model-Q8_0.gguf").write_bytes(b"gguf")
        registry.transition(job.id, DownloadState.DOWNLOADING)
        registry.update_progress(job.id, 4)
        registry.transition(job.id, DownloadState.COMPLETED)

        response = client.get("/api/library")

    assert response.status_code == 200
    assert response.json() == [
        {
            "download_id": job.id,
            "repo_id": "owner/model-GGUF",
            "revision": "b" * 40,
            "group_key": "model-Q8_0",
            "primary_path": str(destination / "model-Q8_0.gguf"),
            "file_count": 1,
            "total_bytes": 4,
        }
    ]


def test_library_reconcile_reports_invalid_and_stray_files(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    model_root = tmp_path / "models"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), model_root)
    manifest = RepositoryManifest(
        repo_id="owner/model-GGUF",
        revision="e" * 40,
        groups=(
            GgufGroup(
                key="model-Q4",
                quantization="Q4",
                files=(HubFile("model-Q4.gguf", 4),),
                total_size=4,
                complete=True,
            ),
        ),
    )
    job = registry.create(manifest, "model-Q4")
    destination = Path(job.destination)
    destination.mkdir(parents=True)
    destination.joinpath("model-Q4.gguf").write_bytes(b"good")
    registry.transition(job.id, DownloadState.DOWNLOADING)
    registry.update_progress(job.id, 4)
    registry.transition(job.id, DownloadState.COMPLETED)
    (model_root / "stray.gguf").parent.mkdir(parents=True, exist_ok=True)
    (model_root / "stray.gguf").write_bytes(b"stray")

    result = ModelLibrary(registry, model_root).reconcile()

    assert result.managed_jobs == 1
    assert result.valid_models == 1
    assert result.invalid_jobs == 0
    assert result.stray_gguf_files == 1


def test_library_discover_finds_complete_external_shards(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    model_root.mkdir()
    model_dir = model_root / "owner" / "model"
    model_dir.mkdir(parents=True)
    first = model_dir / "model-Q4-00001-of-00002.gguf"
    second = model_dir / "model-Q4-00002-of-00002.gguf"
    first.write_bytes(b"one")
    second.write_bytes(b"two-two")
    (model_dir / "incomplete-00001-of-00002.gguf").write_bytes(b"x")
    registry = DownloadRegistry(create_database_engine(tmp_path / "app.db"), model_root)

    discovered = ModelLibrary(registry, model_root).discover()

    assert len(discovered) == 1
    assert discovered[0].primary_path == first
    assert discovered[0].files == (first, second)
    assert discovered[0].total_bytes == 10
    assert discovered[0].model_name == "model"


def test_library_reads_scalar_gguf_metadata(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    metadata = [("general.name", 4, "Demo Model"), ("general.context_length", 10, 4096)]
    content = bytearray(
        b"GGUF"
        + (3).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
        + len(metadata).to_bytes(8, "little")
    )
    for key, value_type, value in metadata:
        encoded = key.encode()
        content.extend(len(encoded).to_bytes(8, "little"))
        content.extend(encoded)
        content.extend(value_type.to_bytes(4, "little"))
        if isinstance(value, str):
            encoded_value = value.encode()
            content.extend(len(encoded_value).to_bytes(8, "little"))
            content.extend(encoded_value)
        else:
            content.extend(value.to_bytes(8, "little"))
    model.write_bytes(content)
    registry = DownloadRegistry(create_database_engine(tmp_path / "app.db"), tmp_path)

    discovered = ModelLibrary(registry, tmp_path).discover()

    assert discovered[0].metadata == {"general.name": "Demo Model", "general.context_length": 4096}


def test_logical_model_registry_reconciles_discovered_models(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), tmp_path)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    discovered = ModelLibrary(registry, tmp_path).discover()

    logical = LogicalModelRegistry(create_database_engine(database))
    first = logical.reconcile_discovered(discovered)
    second = logical.reconcile_discovered(discovered)

    assert len(first) == 1
    assert second[0].id == first[0].id
    assert second[0].primary_path == model


def test_library_import_creates_disabled_profile_for_discovered_model(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    model_root = tmp_path / "data" / "models"
    model_dir = model_root / "external"
    model_dir.mkdir(parents=True)
    model = model_dir / "external-Q4.gguf"
    model.write_bytes(b"gguf")

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(options=frozenset({"model"}), raw_help="help"),
            devices_output=None,
            errors=(),
        )

    app = create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    with TestClient(app) as client:
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        discovered = client.get("/api/library/discover").json()[0]
        imported = client.post(
            "/api/library/import",
            json={
                "primary_path": discovered["primary_path"],
                "runtime_id": runtime_id,
                "alias": "external-model",
            },
        )

    assert imported.status_code == 201
    assert imported.json()["enabled"] is False
    assert imported.json()["model_path"] == str(model)


def test_library_delete_preserves_profile_and_exposes_redownload(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path,
            version=RuntimeVersion(build="1", commit=None, raw=""),
            capabilities=RuntimeCapabilities(options=frozenset({"model"}), raw_help=""),
            devices_output=None,
            errors=(),
        )

    app = create_app(Settings(data_dir=tmp_path / "data"), runtime_prober=fake_probe)
    with TestClient(app) as client:
        registry = app.state.download_registry
        manifest = RepositoryManifest(
            repo_id="owner/model-GGUF",
            revision="c" * 40,
            groups=(
                GgufGroup(
                    key="model-Q4",
                    quantization="Q4",
                    files=(HubFile("model-Q4.gguf", 4),),
                    total_size=4,
                    complete=True,
                ),
            ),
        )
        job = registry.create(manifest, "model-Q4")
        destination = Path(job.destination)
        destination.mkdir(parents=True)
        model = destination / "model-Q4.gguf"
        model.write_bytes(b"gguf")
        registry.transition(job.id, DownloadState.DOWNLOADING)
        registry.update_progress(job.id, 4)
        registry.transition(job.id, DownloadState.COMPLETED)
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        created = client.post(
            "/api/profiles",
            json={"alias": "model", "runtime_id": runtime_id, "model_path": str(model)},
        )
        deleted = client.delete(f"/api/library/{job.id}")
        profiles = client.get("/api/profiles")

    assert created.status_code == 201
    assert deleted.status_code == 200
    assert not destination.exists()
    assert profiles.json()[0]["validation_state"] == "broken"
    assert profiles.json()[0]["source_download"]["id"] == job.id