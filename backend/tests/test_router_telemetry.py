from __future__ import annotations

from llamawebui.services.router_telemetry import RouterTelemetry


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def spawn(telemetry: RouterTelemetry, name: str, port: int) -> None:
    telemetry.observe(
        f"srv  load_models: spawning server instance with name={name} on port {port}"
    )


def slots_line(slots: int, context: int) -> str:
    return f"srv    load_model: initializing, n_slots = {slots}, n_ctx_slot = {context}"


def test_ignores_the_timestamp_and_level_prefix_of_recent_builds() -> None:
    telemetry = RouterTelemetry()
    telemetry.observe(
        "0.00.081.100 I srv  load_models: spawning server instance with name=qwen on port 5001"
    )
    telemetry.observe(f"[ 5001] 0.01.204.311 I {slots_line(1, 4096)}")
    telemetry.observe(
        "0.02.000.000 I [ 5001] slot release: id  0 | task 3 | stop processing: n_tokens = 9"
    )

    assert telemetry.context() == [
        {"model": "qwen", "shared_capacity": None, "slots": [{"id": 0, "used": 9, "total": 4096}]}
    ]


def test_respawn_resets_and_stop_forgets_a_model() -> None:
    telemetry = RouterTelemetry()
    spawn(telemetry, "qwen", 5001)
    telemetry.observe(f"[ 5001] {slots_line(1, 1000)}")
    spawn(telemetry, "qwen", 5002)
    telemetry.observe(f"[ 5001] {slots_line(1, 9999)}")
    telemetry.observe(f"[ 5002] {slots_line(1, 2000)}")

    context = telemetry.context()
    assert [entry["model"] for entry in context] == ["qwen", "port 5001"]
    assert context[0]["slots"] == [{"id": 0, "used": 0, "total": 2000}]

    telemetry.observe("srv  unload: stopping model instance name=qwen")
    assert [entry["model"] for entry in telemetry.context()] == ["port 5001"]

    telemetry.reset()
    assert telemetry.context() == []


def test_unprefixed_output_belongs_to_the_router_itself() -> None:
    telemetry = RouterTelemetry()
    telemetry.observe(slots_line(1, 4096))

    assert telemetry.context()[0]["model"] is None


def test_tracks_context_usage_per_slot() -> None:
    telemetry = RouterTelemetry()
    spawn(telemetry, "qwen", 5001)
    slot = "[ 5001] slot launch_slot_: id  1 | task 7 | "
    for line in (
        "[ 5001] srv    load_model: initializing, n_slots = 2, n_ctx_slot = 8192, "
        "kv_unified = 'true'",
        f"{slot}new prompt, n_ctx_slot = 8192, n_keep = 0, task.n_tokens = 1200",
        f"{slot}prompt processing, n_tokens =    600, progress = 0.50, "
        "t = 0.40 s / 1500.00 tokens per second",
    ):
        telemetry.observe(line)
    assert telemetry.context() == [
        {
            "model": "qwen",
            "shared_capacity": 8192,
            "slots": [{"id": 0, "used": 0, "total": 8192}, {"id": 1, "used": 600, "total": 8192}],
        }
    ]

    telemetry.observe(f"{slot}n_gen =     30, tg =  40.00 t/s, tg_3s =  45.00 t/s")
    assert telemetry.context()[0]["slots"][1]["used"] == 1230  # type: ignore[index]
    telemetry.observe(f"{slot}accepted 3/4 draft tokens, new n_tokens = 1240")
    assert telemetry.context()[0]["slots"][1]["used"] == 1240  # type: ignore[index]
    telemetry.observe(f"{slot}stop processing: n_tokens = 1251, truncated = 0")
    assert telemetry.context()[0]["slots"][1]["used"] == 1251  # type: ignore[index]

    telemetry.observe("[ 5001] slot init: id  0 | task -1 | new slot, n_ctx = 4096")
    telemetry.observe(
        "[ 5001] slot update_slots: id  0 | task 9 | stopped due to running out of context "
        "capacity, prompt.n_tokens() = 4096, task.n_tokens = 10, n_gen = 4086, n_ctx = 4096"
    )
    assert telemetry.context()[0]["slots"][0] == {"id": 0, "used": 4096, "total": 4096}


def test_single_slot_context_is_not_shared() -> None:
    telemetry = RouterTelemetry()
    telemetry.observe(
        "srv load_model: initializing, n_slots = 1, n_ctx_slot = 4096, kv_unified = 'true'"
    )

    assert telemetry.context()[0]["shared_capacity"] is None


def test_rolling_peak_averages_the_window_and_decays_when_idle() -> None:
    clock = Clock()
    telemetry = RouterTelemetry(clock)
    progress = (
        "[ 5001] slot print_timing: id  0 | task 3 | n_gen = {0}, tg = {1} t/s, tg_3s = {1} t/s"
    )
    assert telemetry.rolling_peak() is None
    assert telemetry.session_peak is None

    telemetry.observe(progress.format(10, 100.0))
    clock.now += 2
    telemetry.observe(progress.format(20, 80.0))
    clock.now += 2
    # 2 s at 100 plus 2 s at 80, averaged over the 10 s window.
    assert telemetry.rolling_peak() == 36.0
    assert telemetry.session_peak == 100.0

    telemetry.observe(
        "[ 5001] slot release: id  0 | task 3 | stop processing: n_tokens = 40, truncated = 0"
    )
    clock.now += 30
    assert telemetry.rolling_peak() == 0.0
    assert telemetry.session_peak == 100.0

    # Without a closing line, a stale sample only counts for the hold period.
    telemetry.observe(progress.format(5, 50.0))
    clock.now += 20
    telemetry.observe("[ 5001] slot print_timing: id  0 | task 4 | n_gen = 6, tg = 10.00 t/s")
    assert telemetry.rolling_peak() == 0.0
    clock.now += 1
    assert telemetry.rolling_peak() == 1.0
