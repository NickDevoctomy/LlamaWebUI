import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.download_job import DownloadState
from llamawebui.domain.model_manifest import GgufGroup, HubFile
from llamawebui.models import DownloadJobRecord
from llamawebui.services import huggingface_transfer_process
from llamawebui.services.download_registry import DownloadRegistry
from llamawebui.services.download_worker import (
    DownloadWorker,
    HuggingFaceFileTransfer,
    _matches_etag,
    _safe_file_size,
)
from llamawebui.services.huggingface_catalog import RepositoryManifest
from llamawebui.services.huggingface_transfer_process import execute_transfer

pytestmark = pytest.mark.asyncio


def create_registry(tmp_path: Path, sizes: tuple[int, ...]) -> tuple[DownloadRegistry, str]:
    database = tmp_path / "app.db"
    upgrade_database(database)
    registry = DownloadRegistry(create_database_engine(database), tmp_path / "models")
    files = tuple(HubFile(f"model-{index}.gguf", size) for index, size in enumerate(sizes))
    manifest = RepositoryManifest(
        repo_id="owner/model",
        revision="a" * 40,
        groups=(GgufGroup("model", "Q4", files, sum(sizes), True),),
    )
    return registry, registry.create(manifest, "model").id


async def test_worker_downloads_verifies_and_completes(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2, 3))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            assert repo_id == "owner/model"
            assert revision == "a" * 40
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * (2 if filename == "model-0.gguf" else 3))
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.COMPLETED
    assert job.completed_bytes == 5
    assert job.error is None
    assert (Path(job.destination) / "model-0.gguf").read_bytes() == b"xx"
    assert not list(Path(job.destination).parent.glob("*.partial"))


async def test_worker_retries_transient_transfer_failure(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2,))
    attempts = 0

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OSError("temporary transfer failure")
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"xx")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert attempts == 3
    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_worker_verifies_published_file_checksum(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2,))
    job = registry.get(job_id)
    job.files[0]["sha256"] = hashlib.sha256(b"xx").hexdigest()

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"xx")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_worker_fails_on_checksum_mismatch(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2,))
    with Session(create_database_engine(tmp_path / "app.db")) as session:
        job = session.get(DownloadJobRecord, job_id)
        assert job is not None
        job.files = [{**job.files[0], "sha256": hashlib.sha256(b"nope").hexdigest()}]
        session.commit()

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"xx")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert registry.get(job_id).state == DownloadState.FAILED
    assert "checksum mismatch" in (registry.get(job_id).error or "")


async def test_worker_rejects_invalid_size_metadata(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2,))
    with Session(create_database_engine(tmp_path / "app.db")) as session:
        job = session.get(DownloadJobRecord, job_id)
        assert job is not None
        job.files = [{**job.files[0], "size": "2"}]
        session.commit()

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            raise AssertionError("transfer should not start")

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert "size is invalid" in (registry.get(job_id).error or "")


async def test_worker_rejects_existing_destination(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (1,))
    job = registry.get(job_id)
    Path(job.destination).mkdir(parents=True)

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert "already exists" in (registry.get(job_id).error or "")


async def test_worker_replaces_stale_cache_only_destination(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (1,))
    job = registry.get(job_id)
    destination = Path(job.destination)
    (destination / ".cache" / "huggingface").mkdir(parents=True)

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert registry.get(job_id).state == DownloadState.COMPLETED
    assert (Path(job.destination) / "model-0.gguf").read_bytes() == b"x"


async def test_worker_completes_when_valid_destination_was_published_before_retry(
    tmp_path: Path,
) -> None:
    registry, job_id = create_registry(tmp_path, (1,))
    job = registry.get(job_id)
    destination = Path(job.destination)
    destination.mkdir(parents=True)
    (destination / "model-0.gguf").write_bytes(b"x")

    class Transfer:
        async def download(self, **kwargs: object) -> Path:
            raise AssertionError("a valid published destination should not redownload")

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_worker_fails_when_disk_space_drops_during_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry, job_id = create_registry(tmp_path, (10,))
    finish_transfer = asyncio.Event()

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            await finish_transfer.wait()
            raise AssertionError("transfer should be cancelled by disk check")

    monkeypatch.setattr("llamawebui.services.download_worker._PROGRESS_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("llamawebui.services.download_worker._DISK_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(
        "llamawebui.services.download_worker.shutil.disk_usage",
        lambda path: type("Usage", (), {"free": 0})(),
    )

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert registry.get(job_id).state == DownloadState.FAILED
    assert "insufficient disk space" in (registry.get(job_id).error or "")


async def test_worker_tracks_progress_inside_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry, job_id = create_registry(tmp_path, (10,))
    partial_written = asyncio.Event()
    finish_transfer = asyncio.Event()

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            incomplete = destination / ".cache" / "huggingface" / "download" / (
                "mUS11PkUrQTVsen78h_qeKf5F7c=.etag.incomplete"
            )
            incomplete.parent.mkdir(parents=True, exist_ok=True)
            incomplete.write_bytes(b"xxxx")
            partial_written.set()
            await finish_transfer.wait()
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 10)
            incomplete.unlink()
            return target

    monkeypatch.setattr("llamawebui.services.download_worker._PROGRESS_INTERVAL_SECONDS", 0.01)
    worker_task = asyncio.create_task(DownloadWorker(registry, Transfer()).run(job_id))
    await partial_written.wait()
    try:
        await asyncio.sleep(0.03)
        assert registry.get(job_id).completed_bytes == 4
    finally:
        finish_transfer.set()
        await worker_task

    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_safe_file_size_tolerates_cache_file_finalization_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "file.incomplete"
    path.write_bytes(b"partial")
    original_stat = Path.stat

    def disappearing_stat(candidate: Path):
        if candidate == path:
            path.unlink()
        return original_stat(candidate)

    monkeypatch.setattr(Path, "stat", disappearing_stat)

    await asyncio.sleep(0)
    assert _safe_file_size(path) == 0


async def test_worker_records_size_failure(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2,))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.FAILED
    assert "size mismatch" in (job.error or "")
    assert not Path(job.destination).exists()


async def test_worker_does_not_override_cancelled_state(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (1, 1))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = destination / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            registry.transition(job_id, DownloadState.CANCELLED)
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.CANCELLED
    assert job.completed_bytes == 0
    assert not Path(job.destination).exists()


async def test_worker_resumes_verified_staged_files(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (2, 3))
    job = registry.get(job_id)
    staging = Path(job.destination).parent / f".{Path(job.destination).name}.{job.id}.partial"
    staging.mkdir(parents=True)
    (staging / "model-0.gguf").write_bytes(b"xx")
    calls: list[str] = []

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            calls.append(filename)
            target = destination / filename
            target.write_bytes(b"xxx")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert calls == ["model-1.gguf"]
    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_worker_redownloads_staged_file_when_etag_does_not_match(
    tmp_path: Path,
) -> None:
    registry, job_id = create_registry(tmp_path, (2,))
    job = registry.get(job_id)
    job.files[0]["etag"] = hashlib.md5(b"xx").hexdigest()
    staging = Path(job.destination).parent / f".{Path(job.destination).name}.{job.id}.partial"
    staging.mkdir(parents=True)
    (staging / "model-0.gguf").write_bytes(b"stale")
    calls: list[str] = []

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            calls.append(filename)
            target = destination / filename
            target.write_bytes(b"xx")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    assert calls == ["model-0.gguf"]
    assert registry.get(job_id).state == DownloadState.COMPLETED


async def test_worker_etag_validation_ignores_unsupported_values(tmp_path: Path) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(b"model")

    assert _matches_etag(path, None)
    assert _matches_etag(path, "opaque-etag")
    assert _matches_etag(path, '"opaque-etag"')


async def test_worker_rejects_transfer_path_outside_staging(tmp_path: Path) -> None:
    registry, job_id = create_registry(tmp_path, (1,))

    class Transfer:
        async def download(
            self, *, repo_id: str, filename: str, revision: str, destination: Path
        ) -> Path:
            target = tmp_path / "outside.gguf"
            target.write_bytes(b"x")
            return target

    await DownloadWorker(registry, Transfer()).run(job_id)

    job = registry.get(job_id)
    assert job.state == DownloadState.FAILED
    assert "outside the staging directory" in (job.error or "")
    assert not Path(job.destination).exists()


async def test_huggingface_transfer_process_forwards_pinned_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_download(**kwargs: object) -> str:
        captured.update(kwargs)
        target = tmp_path / "model.gguf"
        target.touch()
        return str(target)

    monkeypatch.setattr(
        "llamawebui.services.huggingface_transfer_process.hf_hub_download", fake_download
    )

    result = execute_transfer(
        {
            "repo_id": "owner/model",
            "filename": "model.gguf",
            "revision": "a" * 40,
            "destination": str(tmp_path),
            "token": "hf_secret",
        }
    )

    assert result == {"path": str(tmp_path / "model.gguf")}
    assert captured == {
        "repo_id": "owner/model",
        "filename": "model.gguf",
        "revision": "a" * 40,
        "local_dir": tmp_path,
        "token": "hf_secret",
    }


async def test_huggingface_transfer_process_redacts_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_download(**kwargs: object) -> str:
        raise RuntimeError(f"request rejected for {kwargs['token']}")

    monkeypatch.setattr(
        "llamawebui.services.huggingface_transfer_process.hf_hub_download", fake_download
    )

    result = execute_transfer(
        {
            "repo_id": "owner/model",
            "filename": "model.gguf",
            "revision": "a" * 40,
            "destination": str(tmp_path),
            "token": "hf_secret",
        }
    )

    assert result == {"error": "request rejected for [redacted]"}


async def test_huggingface_transfer_main_reports_invalid_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr(huggingface_transfer_process.sys.stdin, "read", lambda: "not-json")

    assert huggingface_transfer_process.main() == 1
    assert '"error"' in capsys.readouterr().out


async def test_huggingface_transfer_main_returns_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        huggingface_transfer_process.sys.stdin,
        "read",
        lambda: (
            '{"repo_id":"owner/model","filename":"model.gguf",'
            '"revision":"r","destination":".","token":null}'
        ),
    )
    monkeypatch.setattr(
        huggingface_transfer_process,
        "execute_transfer",
        lambda request: {"path": "model.gguf"},
    )

    assert huggingface_transfer_process.main() == 0
    assert capsys.readouterr().out == '{"path": "model.gguf"}'


async def test_huggingface_transfer_kills_child_when_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    communicate_started = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, input: bytes) -> tuple[bytes, bytes]:
            assert json.loads(input)["revision"] == "a" * 40
            communicate_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            self.returncode = -9
            return self.returncode

    process = Process()

    async def fake_subprocess(*args: object, **kwargs: object) -> Process:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    task = asyncio.create_task(
        HuggingFaceFileTransfer().download(
            repo_id="owner/model",
            filename="model.gguf",
            revision="a" * 40,
            destination=tmp_path,
        )
    )
    await communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert process.waited