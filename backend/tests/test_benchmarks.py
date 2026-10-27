from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import Login
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_server_api import FakeProcess, available_port

from llamawebui.app import create_app, slot_context
from llamawebui.config import Settings
from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.domain.argument_catalog import default_catalog
from llamawebui.domain.benchmark import Benchmark, BenchmarkError, load_benchmarks
from llamawebui.domain.model_profile import ModelProfile
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.models import ModelProfileRecord, RuntimeRecord
from llamawebui.services.benchmark_runner import (
    BenchmarkBusyError,
    BenchmarkRunner,
    BenchmarkRunNotFoundError,
    BenchmarkRunRegistry,
    _slowest_share,
)
from llamawebui.services.profile_registry import ProfileRegistry
from llamawebui.services.router_client import RouterModel, RouterUnavailableError
from llamawebui.services.router_supervisor import RouterProcess, RouterSupervisor
from llamawebui.services.runtime_probe import RuntimeProbeResult

BENCHMARK = Benchmark(id="tetris", title="Tetris", prompt="make tetris")


def engine_folder(engine: Engine) -> Path:
    return Path(str(engine.url.database)).parent


def _chunks() -> list[dict[str, object]]:
    """A stream the way llama.cpp sends it: running totals that grow chunk by chunk.

    Decoding runs 4 s at 20 tok/s, bursts for 3 s at 40 tok/s (as accepted drafts
    do), then slows back to 20 tok/s for its last 3 s: 260 tokens in 10 s.
    """
    prompt = {"prompt_n": 20, "prompt_ms": 50.0, "prompt_per_second": 400.0}
    chunks: list[dict[str, object]] = [
        {"prompt_progress": {"total": 20, "cache": 0, "processed": 10, "time_ms": 25.0}},
        {"prompt_progress": {"total": 20, "cache": 0, "processed": 20, "time_ms": 50.0}},
        {
            "choices": [{"delta": {"reasoning_content": "Thinking"}}],
            "timings": {**prompt, "predicted_n": 0, "predicted_ms": 0.0},
        },
    ]
    milliseconds = 0.0
    for token in range(1, 261):
        milliseconds += 25.0 if 80 < token <= 200 else 50.0
        content = "<html>" if token == 1 else "</html>" if token == 260 else "x"
        timings = {
            **prompt,
            "predicted_n": token,
            "predicted_ms": milliseconds,
            "predicted_per_second": token / milliseconds * 1000,
        }
        choice: dict[str, object] = {"delta": {"content": content}}
        if token == 260:
            choice["finish_reason"] = "stop"
        chunks.append({"choices": [choice], "timings": timings})
    return chunks


class FakeBenchmarkClient:
    def __init__(
        self,
        *,
        load_states: Sequence[dict[str, object]] = (),
        unanswered_polls: int = 0,
        load_unanswered: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self._load_states = list(load_states) or [{"value": "loading"}, {"value": "loaded"}]
        self._unanswered_polls = unanswered_polls
        self._load_unanswered = load_unanswered
        self.hold = asyncio.Event()
        self.block_after_first_chunk = False
        # Each request's messages and tools, and scripted replies, one list per request.
        self.requests: list[tuple[list[dict[str, object]], list[str]]] = []
        # A scripted reply may instead be an error the request raises.
        self.replies: list[list[dict[str, object]] | Exception] = []
        self.options: list[dict[str, object]] = []
        # Called after each chunk is handed over, to look at the run mid-stream.
        self.on_chunk: Callable[[], None] | None = None

    async def list_models(self, *, reload: bool = False) -> tuple[RouterModel, ...]:
        if self._unanswered_polls:
            self._unanswered_polls -= 1
            raise RouterUnavailableError("llama.cpp router request failed", TimeoutError())
        state = self._load_states.pop(0) if len(self._load_states) > 1 else self._load_states[0]
        return (RouterModel(id="qwen", path=None, status=state, metadata={}),)

    async def load_model(self, model: str) -> None:
        self.calls.append(f"load:{model}")
        if self._load_unanswered:
            raise RouterUnavailableError("llama.cpp router request failed", TimeoutError())

    async def chat_stream(
        self,
        model: str,
        messages: Sequence[Mapping[str, object]],
        *,
        max_tokens: int | None,
        tools: Sequence[Mapping[str, object]] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        prompt = next(item["content"] for item in messages if item["role"] == "user")
        self.calls.append(f"prompt:{model}:{prompt}:{max_tokens}")
        self.options.append(dict(options or {}))
        names = [cast(dict[str, str], tool["function"])["name"] for tool in tools]
        self.requests.append(([dict(item) for item in messages], names))
        chunks = self.replies.pop(0) if self.replies else _chunks()
        if isinstance(chunks, Exception):
            raise chunks
        for index, chunk in enumerate(chunks):
            yield chunk
            if self.on_chunk is not None:
                self.on_chunk()
            if self.block_after_first_chunk and index == 2:
                await self.hold.wait()


def _database(tmp_path: Path) -> tuple[Engine, ModelProfileRecord]:
    database = tmp_path / "app.db"
    upgrade_database(database)
    engine = create_database_engine(database)
    with Session(engine) as session:
        session.add(
            RuntimeRecord(
                id="runtime-1",
                name="CUDA",
                executable_path=str(tmp_path / "llama-server.exe"),
                options=["model", "models-preset"],
                devices=[],
                help_sha256="0" * 64,
            )
        )
        session.commit()
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    profile = ProfileRegistry(engine, default_catalog()).create(
        profile=ModelProfile(alias="qwen", model_path=model), runtime_id="runtime-1", version=4
    )
    return engine, profile


def _runner(
    engine: Engine, client: FakeBenchmarkClient, *, router_stopped: bool = True, **options: Any
) -> tuple[BenchmarkRunner, BenchmarkRunRegistry, list[str]]:
    events: list[str] = []

    async def start_router(runtime_id: str) -> None:
        events.append(f"start:{runtime_id}")

    async def stop_router() -> None:
        events.append("stop")

    runs = BenchmarkRunRegistry(engine, engine_folder(engine) / "benchmark-runs")
    runner = BenchmarkRunner(
        runs,
        client,
        start_router=start_router,
        stop_router=stop_router,
        router_stopped=lambda: router_stopped,
        sample_seconds=0,
        **options,
    )
    return runner, runs, events


def test_load_benchmarks_reads_prompts_and_rejects_bad_files(tmp_path: Path) -> None:
    bundled = {item.id: item for item in load_benchmarks()}
    assert bundled["tetris-no-details"].prompt == (
        "make a single page html/js tetris clone, make it look cool."
    )
    assert bundled["tetris-no-details"].title == "Tetris no details"
    assert bundled["tetris-no-details"].tasks == ()
    assert len(bundled["tetris-ultra-high-detailed"].tasks) == 11
    assert bundled["tetris-ultra-high-detailed"].prompt.startswith("# Neon Tetris")

    assert load_benchmarks(tmp_path / "missing") == ()
    (tmp_path / "long-context.txt").write_text("  summarise this  \n", encoding="utf-8")
    assert load_benchmarks(tmp_path) == (
        Benchmark(id="long-context", title="Long context", prompt="summarise this"),
    )
    (tmp_path / "empty.txt").write_text("\n", encoding="utf-8")
    with pytest.raises(BenchmarkError, match="empty.txt: benchmark prompt is empty"):
        load_benchmarks(tmp_path)
    (tmp_path / "empty.txt").unlink()
    (tmp_path / "Bad Name!.txt").write_text("x", encoding="utf-8")
    with pytest.raises(BenchmarkError, match="invalid benchmark prompt file name"):
        load_benchmarks(tmp_path)


@pytest.mark.asyncio
async def test_a_benchmark_times_each_step_and_writes_its_result_to_the_profile(
    tmp_path: Path,
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, events = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256)
    assert runner.active_run_id == record.id
    await runner.wait()

    finished = runs.get(record.id)
    assert runner.active_run_id is None
    assert events == ["start:runtime-1", "stop"]
    assert client.calls == ["load:qwen", "prompt:qwen:make tetris:256"]
    assert (finished.state, finished.stage, finished.error) == ("completed", "done", None)
    assert finished.output == "<html>" + "x" * 258 + "</html>"
    assert finished.reasoning == "Thinking"
    assert finished.profile_version == 4
    reading, thinking, answer = finished.timeline
    assert (reading["kind"], reading["prompt_tokens"]) == ("reading", 20)
    assert thinking["kind"] == "thinking"
    assert finished.reasoning[thinking["from"] : thinking["to"]] == "Thinking"  # type: ignore[misc]
    assert (answer["kind"], answer["tokens"]) == ("answer", 260)
    assert finished.output[answer["from"] : answer["to"]] == finished.output  # type: ignore[misc]
    stats = finished.stats
    assert stats["prompt_tokens"] == 20
    assert stats["prompt_tokens_per_second"] == 400.0
    assert stats["prompt_seconds"] == 0.05
    assert stats["generated_tokens"] == 260
    assert stats["finish_reason"] == "stop"
    assert {"server_start_seconds", "load_seconds", "first_token_seconds", "total_seconds"} <= set(
        stats
    )
    # The average spans the whole run; the peak and the end come from 3 s windows.
    assert stats["decode_tokens_per_second"] == 26.0
    assert stats["decode_high_tokens_per_second"] == pytest.approx(40.0, abs=0.5)
    assert stats["decode_low_tokens_per_second"] == 20.0
    assert stats["decode_final_tokens_per_second"] == 20.0
    # Prefill is measured batch by batch: both of its 10-token steps took 25 ms.
    assert stats["prompt_high_tokens_per_second"] == 400.0
    assert stats["prompt_low_tokens_per_second"] == 400.0

    prefill = [sample for sample in finished.samples if sample["phase"] == "prefill"]
    decode = [sample for sample in finished.samples if sample["phase"] == "decode"]
    assert finished.samples[0]["phase"] == "prefill"
    assert [sample["rate"] for sample in prefill] == [400.0]
    assert len(decode) == 260
    # Samples follow the current speed, not the running average.
    assert decode[0]["rate"] == 20.0
    assert max(sample["rate"] for sample in decode) == 40.0
    assert decode[-1]["rate"] == 20.0

    result = ProfileRegistry(engine, default_catalog()).get(profile.id).benchmark_results
    assert result is not None
    assert result["tetris"]["decode_tokens_per_second"] == 26.0  # type: ignore[index]
    assert result["tetris"]["decode_high_tokens_per_second"] == pytest.approx(40.0, abs=0.5)  # type: ignore[index]
    assert result["tetris"]["decode_low_tokens_per_second"] == 20.0  # type: ignore[index]
    assert result["tetris"]["prompt_low_tokens_per_second"] == 400.0  # type: ignore[index]
    assert result["tetris"]["prompt_high_tokens_per_second"] == 400.0  # type: ignore[index]
    assert result["tetris"]["decode_final_tokens_per_second"] == 20.0  # type: ignore[index]
    assert result["tetris"]["run_id"] == record.id  # type: ignore[index]

    # Deleting the run keeps the result written to the profile.
    runs.delete(record.id)
    with pytest.raises(BenchmarkRunNotFoundError):
        runs.get(record.id)
    with pytest.raises(BenchmarkRunNotFoundError):
        runs.delete(record.id)
    kept = ProfileRegistry(engine, default_catalog()).get(profile.id).benchmark_results
    assert kept == result


@pytest.mark.asyncio
async def test_a_run_keeps_the_most_context_it_used(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    runner, runs, _ = _runner(engine, client)
    readings = iter([(1000, 65536), (52000, 65536), (30000, 65536)])
    seen: list[str] = []

    def context(alias: str) -> tuple[int, int] | None:
        seen.append(alias)
        return next(readings, (30000, 65536))

    runner._context = context
    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    stats = runs.get(record.id).stats
    assert (stats["context_peak"], stats["context_size"]) == (52000, 65536)
    assert set(seen) == {profile.alias}
    result = ProfileRegistry(engine, default_catalog()).get(profile.id).benchmark_results
    assert cast(dict[str, dict[str, object]], result)["tetris"]["context_peak"] == 52000


@pytest.mark.asyncio
async def test_a_run_without_context_readings_keeps_none(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    runner, runs, _ = _runner(engine, FakeBenchmarkClient())
    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()
    assert "context_peak" not in runs.get(record.id).stats


def test_slot_context_reads_the_busiest_slot_against_its_size() -> None:
    slots = [{"id": 0, "used": 900, "total": 16384}, {"id": 1, "used": 4000, "total": 16384}]
    assert slot_context([{"model": "qwen", "shared_capacity": None, "slots": slots}], "qwen") == (
        4000,
        16384,
    )
    # A unified cache shares its whole size between the slots.
    assert slot_context(
        [{"model": "qwen", "shared_capacity": 65536, "slots": slots}], "qwen"
    ) == (4000, 65536)
    # A router serving one model itself logs without a model name.
    assert slot_context([{"model": None, "slots": slots}], "qwen") == (4000, 16384)
    assert slot_context([{"model": "other", "slots": slots}], "qwen") is None
    assert slot_context([{"model": "qwen", "slots": [{"id": 0, "used": 5}]}], "qwen") is None
    assert slot_context([], "qwen") is None


@pytest.mark.asyncio
async def test_a_router_too_busy_to_answer_while_loading_does_not_fail_the_run(
    tmp_path: Path,
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient(load_unanswered=True, unanswered_polls=2)
    runner, runs, events = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    finished = runs.get(record.id)
    assert (finished.state, finished.error) == ("completed", None)
    assert "load_seconds" in finished.stats
    assert events == ["start:runtime-1", "stop"]


@pytest.mark.asyncio
async def test_a_load_that_never_answers_times_out_with_the_reason(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    runner, runs, _ = _runner(engine, FakeBenchmarkClient(unanswered_polls=1_000))
    runner._load_timeout = 0  # noqa: SLF001

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    assert runs.get(record.id).error == (
        "qwen did not load within 0s; the router last failed with: "
        "llama.cpp router request failed (TimeoutError)"
    )


@pytest.mark.asyncio
async def test_a_model_that_fails_to_load_fails_the_run_and_stops_the_router(
    tmp_path: Path,
) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient(
        load_states=[{"value": "unloaded", "failed": True, "exit_code": 1}]
    )
    marks: list[int] = []

    def logs_since(mark: int) -> list[str]:
        marks.append(mark)
        return [f"line {number}" for number in range(mark, mark + 250)]

    runner, runs, events = _runner(engine, client, log_mark=lambda: 7, logs_since=logs_since)

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    finished = runs.get(record.id)
    assert (finished.state, finished.stage) == ("failed", "loading")
    # Only the run's own lines are kept, and only the newest of them.
    assert marks == [7]
    assert finished.server_log is not None
    assert (len(finished.server_log), finished.server_log[-1]) == (200, "line 256")
    assert finished.error == "qwen failed to load (exit code 1)"
    assert events == ["start:runtime-1", "stop"]
    assert ProfileRegistry(engine, default_catalog()).get(profile.id).benchmark_results is None


@pytest.mark.asyncio
async def test_a_slow_load_times_out(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    runner, runs, _ = _runner(engine, FakeBenchmarkClient(load_states=[{"value": "loading"}]))
    runner._load_timeout = 0  # noqa: SLF001

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    assert runs.get(record.id).error == "qwen did not load within 0s"


@pytest.mark.asyncio
async def test_a_run_keeps_its_context_and_memory_use_over_time(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.block_after_first_chunk = True
    readings: list[dict[str, object] | Exception | None] = [
        {"ram_bytes": 100, "mapped_bytes": 50, "vram_bytes": 900},
        RuntimeError("process gone"),
        None,
        {"ram_bytes": 120, "mapped_bytes": None, "vram_bytes": None},
    ]
    asked: list[str] = []

    def memory(alias: str) -> dict[str, object] | None:
        asked.append(alias)
        reading = readings.pop(0) if len(readings) > 1 else readings[0]
        if isinstance(reading, Exception):
            raise reading
        return reading

    totals: list[dict[str, object] | Exception | None] = [
        {"vram_bytes": 2000, "ram_bytes": 5000},
        None,
        RuntimeError("counters gone"),
        {"vram_bytes": None, "ram_bytes": 6000},
    ]

    def system_memory() -> dict[str, object] | None:
        reading = totals.pop(0) if len(totals) > 1 else totals[0]
        if isinstance(reading, Exception):
            raise reading
        return reading

    runs = BenchmarkRunRegistry(engine, engine_folder(engine) / "benchmark-runs")
    runner = BenchmarkRunner(
        runs,
        client,
        start_router=lambda _runtime: asyncio.sleep(0),
        stop_router=lambda: asyncio.sleep(0),
        router_stopped=lambda: True,
        sample_seconds=0,
        context=lambda _alias: (2048, 8192),
        memory=memory,
        system_memory=system_memory,
        resource_seconds=0.001,
    )

    record = runner.start(profile, BENCHMARK, 256)
    for _ in range(500):
        await asyncio.sleep(0.002)
        progress = runner.progress(record.id)
        if progress and len(cast(list[object], progress["resources"])) >= 4:
            break
    live = cast(list[dict[str, object]], runner.progress(record.id)["resources"])  # type: ignore[index]
    client.hold.set()
    await runner.wait()

    assert asked[0] == "qwen"
    # RAM counts the model file pages the process holds; a failed or missing reading
    # still keeps the context.
    assert {key: value for key, value in live[0].items() if key != "t"} == {
        "context_used": 2048,
        "context_size": 8192,
        "ram_bytes": 150,
        "vram_bytes": 900,
        "vram_total_bytes": 2000,
        "ram_total_bytes": 5000,
    }
    assert "ram_bytes" not in live[1] and "ram_bytes" not in live[2]
    assert "ram_total_bytes" not in live[1] and "ram_total_bytes" not in live[2]
    assert live[3]["ram_bytes"] == 120 and "vram_bytes" not in live[3]
    assert live[3]["ram_total_bytes"] == 6000 and "vram_total_bytes" not in live[3]
    assert all(isinstance(entry["t"], float) for entry in live)
    stored = runs.get(record.id).resources
    assert len(stored) >= 4 and stored[:4] == live[:4]


@pytest.mark.asyncio
async def test_a_run_with_nothing_to_measure_keeps_no_resources(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    runner, runs, _ = _runner(engine, FakeBenchmarkClient())
    runner._resource_seconds = 0.001

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    assert runs.get(record.id).resources == []


@pytest.mark.asyncio
async def test_a_cancelled_benchmark_keeps_its_partial_output(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.block_after_first_chunk = True
    runner, runs, events = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256)
    for _ in range(50):
        await asyncio.sleep(0)
        progress = runner.progress(record.id)
        if progress and progress["reasoning"]:
            break
    assert runner.progress(record.id)["stage"] == "decoding"  # type: ignore[index]
    elapsed = runner.generation_elapsed(record.id)
    assert elapsed is not None and elapsed >= 0
    assert runner.generation_elapsed("another-run") is None
    with pytest.raises(BenchmarkBusyError, match="already running"):
        runner.start(profile, BENCHMARK, 256)
    with pytest.raises(BenchmarkBusyError, match="cancel the benchmark"):
        runs.delete(record.id)

    assert await runner.cancel(record.id) is True
    assert await runner.cancel(record.id) is False
    assert runner.generation_elapsed(record.id) is None

    finished = runs.get(record.id)
    assert (finished.state, finished.stage) == ("cancelled", "decoding")
    assert finished.error == "cancelled"
    assert finished.reasoning == "Thinking"
    assert events == ["start:runtime-1", "stop"]


@pytest.mark.asyncio
async def test_benchmarks_need_a_stopped_router_and_an_enabled_profile(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    running, _, _ = _runner(engine, FakeBenchmarkClient(), router_stopped=False)
    with pytest.raises(BenchmarkBusyError, match="stop the router"):
        running.start(profile, BENCHMARK, 256)

    stopped, runs, _ = _runner(engine, FakeBenchmarkClient())
    disabled = ProfileRegistry(engine, default_catalog()).set_enabled(profile.id, False)
    with pytest.raises(BenchmarkBusyError, match="enable the profile"):
        stopped.start(disabled, BENCHMARK, 256)
    assert runs.list() == []


@pytest.mark.asyncio
async def test_runs_left_running_by_a_crash_are_marked_failed(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    runs = BenchmarkRunRegistry(engine, engine_folder(engine) / "benchmark-runs")
    record = runs.create(profile, BENCHMARK, 256)

    assert runs.fail_interrupted() == 1
    assert runs.get(record.id).state == "failed"
    assert runs.list(profile.id)[0].error == "the app stopped while this benchmark was running"
    assert runs.list("other-profile") == []


class BenchmarkRouterClient(FakeBenchmarkClient):
    async def unload_model(self, model: str) -> None:
        pass

    async def model_events(self) -> AsyncIterator[object]:
        await asyncio.Event().wait()
        yield None


def test_benchmark_api_runs_a_profile_and_keeps_results_after_delete(
    tmp_path: Path, login: Login
) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return FakeProcess()

    async def healthy(host: str, port: int) -> bool:
        return True

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, healthy),
        router_port_probe=available_port,
        router_client=BenchmarkRouterClient(),  # type: ignore[arg-type]
        benchmarks=(BENCHMARK,),
    )
    with TestClient(app) as client:
        login(client)
        listed = client.get("/api/benchmarks").json()
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        profile_id = client.post(
            "/api/profiles",
            json={"alias": "qwen", "runtime_id": runtime_id, "model_path": str(model)},
        ).json()["id"]
        unknown_benchmark = client.post(
            "/api/benchmarks/runs", json={"profile_id": profile_id, "benchmark_id": "nope"}
        )
        unknown_profile = client.post(
            "/api/benchmarks/runs", json={"profile_id": "nope", "benchmark_id": "tetris"}
        )
        unknown_system_prompt = client.post(
            "/api/benchmarks/runs",
            json={"profile_id": profile_id, "benchmark_id": "tetris", "system_prompt_id": "nope"},
        )
        unknown_level = client.post(
            "/api/benchmarks/runs",
            json={"profile_id": profile_id, "benchmark_id": "tetris", "reasoning_level": "huge"},
        )
        unknown_tool = client.post(
            "/api/benchmarks/runs",
            json={"profile_id": profile_id, "benchmark_id": "tetris", "tool_names": ["nope"]},
        )
        started = client.post(
            "/api/benchmarks/runs",
            json={
                "profile_id": profile_id,
                "benchmark_id": "tetris",
                "max_tokens": 128,
                "system_prompt_id": "coding-agent",
                "tool_names": ["write_file", "task_complete"],
                "verbose_log": True,
            },
        )
        run_id = started.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = client.get(f"/api/benchmarks/runs/{run_id}").json()
            if run["state"] != "running":
                break
            time.sleep(0.05)
        history = client.get("/api/benchmarks/runs", params={"profile_id": profile_id}).json()
        server = client.get("/api/server/status").json()
        not_running = client.post(f"/api/benchmarks/runs/{run_id}/cancel")
        runs = app.state.benchmark_runs
        runs.workspace(run_id).write("game/index.html", "<script>alert(1)</script>")
        detail = client.get(f"/api/benchmarks/runs/{run_id}").json()
        artifact = client.get(f"/api/benchmarks/runs/{run_id}/artifacts/game/index.html")
        escape = client.get(f"/api/benchmarks/runs/{run_id}/artifacts/..%2F..%2Fllamawebui.db")
        missing_artifact = client.get(f"/api/benchmarks/runs/{run_id}/artifacts/nope.txt")
        unknown_run = client.get("/api/benchmarks/runs/nope/artifacts/game/index.html")
        folder = runs.workspace_root / run_id
        deleted = client.delete(f"/api/benchmarks/runs/{run_id}")
        missing = client.get(f"/api/benchmarks/runs/{run_id}")
        missing_delete = client.delete(f"/api/benchmarks/runs/{run_id}")
        profile = next(
            item for item in client.get("/api/profiles").json() if item["id"] == profile_id
        )

    assert listed["benchmarks"] == [BENCHMARK.to_payload()]
    # No default limit: a run may use the profile's whole context.
    assert listed["default_max_tokens"] is None
    assert listed["default_system_prompt_id"] == "coding-agent"
    # The bundled default is not among this test's benchmarks.
    assert listed["default_benchmark_id"] is None
    assert "coding-agent" in [item["id"] for item in listed["system_prompts"]]
    assert unknown_system_prompt.status_code == 404
    assert unknown_level.status_code == 422
    assert listed["reasoning_levels"] == ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert run["reasoning_level"] is None
    assert run["system_prompt_id"] == "coding-agent"
    assert "finish_job" in str(run["system_prompt"])
    assert run["tools"] is True
    assert run["verbose_log"] is True
    # The old name for finish_job still works, so an old run can be run again.
    assert run["tool_names"] == ["write_file", "finish_job"]
    assert unknown_tool.status_code == 422
    assert unknown_tool.json()["detail"] == "unknown tools: nope"
    assert [item["name"] for item in listed["tools"]] == [
        "write_file",
        "edit_file",
        "read_file",
        "delete_file",
        "list_files",
        "check_file",
        "check_typescript",
        "bundle_typescript",
        "run_tests",
        "list_tasks",
        "create_task",
        "complete_task",
        "delete_task",
        "finish_job",
    ]
    assert listed["tools"][0] == {
        "name": "write_file",
        "summary": "Create or replace a whole file.",
        "available": True,
    }
    assert detail["artifacts"] == [{"path": "game/index.html", "size": 25}]
    # Nothing in this test measures context or memory, so there is nothing over time.
    assert detail["resources"] == []
    # Files come back as text inside JSON, never as a page the browser would run.
    assert artifact.headers["content-type"].startswith("application/json")
    assert artifact.json() == {"path": "game/index.html", "content": "<script>alert(1)</script>"}
    assert escape.status_code == 404
    assert missing_artifact.status_code == 404
    assert unknown_run.status_code == 404
    assert not folder.exists()
    assert unknown_benchmark.status_code == 404
    assert unknown_profile.status_code == 404
    assert started.status_code == 201, started.text
    assert run["state"] == "completed", run
    assert run["output"] == "<html>" + "x" * 258 + "</html>"
    assert run["prompt"] == "make tetris"
    assert run["stats"]["decode_tokens_per_second"] == 26.0
    assert [item["id"] for item in history] == [run_id]
    assert "output" not in history[0]
    assert server["state"] == "stopped"
    assert not_running.status_code == 409
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert missing_delete.status_code == 404
    assert profile["benchmark_results"]["tetris"]["decode_tokens_per_second"] == 26.0


def test_a_benchmark_cannot_start_while_the_router_runs(tmp_path: Path, login: Login) -> None:
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()

    async def fake_probe(path: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=path.resolve(),
            version=RuntimeVersion(build="1", commit=None, raw="version"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"model", "models-preset"}), raw_help="help"
            ),
            devices_output=None,
            errors=(),
        )

    async def launcher(arguments: Sequence[str]) -> RouterProcess:
        return FakeProcess()

    async def healthy(host: str, port: int) -> bool:
        return True

    app = create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        router_supervisor=RouterSupervisor(launcher, healthy),
        router_port_probe=available_port,
        router_client=BenchmarkRouterClient(),  # type: ignore[arg-type]
        benchmarks=(BENCHMARK,),
    )
    with TestClient(app) as client:
        login(client)
        runtime_id = client.post(
            "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
        ).json()["id"]
        profile_id = client.post(
            "/api/profiles",
            json={"alias": "qwen", "runtime_id": runtime_id, "model_path": str(model)},
        ).json()["id"]
        client.post("/api/server/start", json={"runtime_id": runtime_id})
        refused = client.post(
            "/api/benchmarks/runs", json={"profile_id": profile_id, "benchmark_id": "tetris"}
        )
        client.post("/api/server/stop")

    assert refused.status_code == 409
    assert refused.json()["detail"] == "stop the router before running a benchmark"


def test_the_one_percent_low_weighs_each_speed_by_how_long_it_lasted() -> None:
    # 99 s at 50 tok/s and 1 s at 10 tok/s: the slowest 1% of the time is that second.
    assert _slowest_share([(50.0, 99.0), (10.0, 1.0)]) == 10.0
    # Many short slow readings are still only their share of the time.
    assert _slowest_share([(10.0, 0.5), (50.0, 99.5)]) == 30.0
    assert _slowest_share([(42.0, 3.0)]) == 42.0
    assert _slowest_share([]) is None
    assert _slowest_share([(42.0, 0.0)]) is None


@pytest.mark.asyncio
async def test_prefill_without_streamed_progress_is_measured_per_request(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    client.replies = [[chunk for chunk in _chunks() if "prompt_progress" not in chunk]]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    stats = runs.get(record.id).stats
    assert stats["prompt_tokens_per_second"] == 400.0
    assert stats["prompt_low_tokens_per_second"] == 400.0
    assert stats["prompt_high_tokens_per_second"] == 400.0


@pytest.mark.asyncio
async def test_cached_tokens_and_tiny_batches_do_not_set_prefill_speeds(tmp_path: Path) -> None:
    engine, profile = _database(tmp_path)
    client = FakeBenchmarkClient()
    reply = [chunk for chunk in _chunks() if "prompt_progress" not in chunk]
    prompt = {"prompt_n": 2000, "prompt_ms": 1001.0, "cache_n": 9000}
    for chunk in reply:
        if isinstance(chunk.get("timings"), dict):
            chunk["timings"] = {**cast(dict[str, object], chunk["timings"]), **prompt}

    def progress(processed: int, milliseconds: float) -> dict[str, object]:
        return {
            "prompt_progress": {
                "total": 11000,
                "cache": 9000,
                "processed": processed,
                "time_ms": milliseconds,
            }
        }

    client.replies = [
        [
            # The cached 9,000 tokens arrive as processed within a millisecond.
            progress(9000, 1.0),
            # Then 2,000 read at 2,000 tok/s in 0.5 s steps, and a 1 ms tail of 2 tokens.
            progress(9998, 500.0),
            progress(10998, 1000.0),
            progress(11000, 1001.0),
            *reply,
        ]
    ]
    runner, runs, _ = _runner(engine, client)

    record = runner.start(profile, BENCHMARK, 256)
    await runner.wait()

    finished = runs.get(record.id)
    stats = finished.stats
    assert stats["prompt_cached_tokens"] == 9000
    assert stats["prompt_tokens"] == 2000
    # No figure reads millions of tokens a second from the cache or the 1 ms tail.
    assert 1990 < cast(float, stats["prompt_high_tokens_per_second"]) < 2010
    assert 1990 < cast(float, stats["prompt_low_tokens_per_second"]) < 2010
    reading = next(entry for entry in finished.timeline if entry["kind"] == "reading")
    assert (reading["prompt_tokens"], reading["cached_tokens"]) == (2000, 9000)
