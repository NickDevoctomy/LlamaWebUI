"""Derive live model telemetry from llama.cpp router output.

The router prefixes each line a model process writes with that process's port
(``[ 5123] ...``) and announces the port when it spawns the process, so lines can
be attributed to the model alias they came from.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic

ROLLING_WINDOW_SECONDS = 10.0
# A progress sample counts until the next one, but no longer than this, so a
# stream that stops without a closing line still decays to zero.
_SAMPLE_HOLD_SECONDS = 5.0

_CHILD_LINE = re.compile(r"^\[\s*(\d+)\]\s?(.*)$")
# Recent builds start each line with elapsed time and a level, e.g. "0.00.082.533 I ".
_LOG_PREFIX = re.compile(r"^\d+(?:\.\d+)+\s+[A-Z]\s+")
_SPAWN = re.compile(r"spawning server instance with name=(\S+) on port (\d+)")
_STOP = re.compile(r"stopping model instance name=(\S+)")
_SLOTS = re.compile(
    r"initializing, n_slots = (\d+), n_ctx_slot = (\d+)(?:, kv_unified = '(\w+)')?"
)
_SLOT_LINE = re.compile(r"\bid\s+(\d+)\s*\|\s*task\s+-?\d+\s*\|\s*(.*)$")
_NEW_SLOT = re.compile(r"new slot, n_ctx = (\d+)")
_NEW_PROMPT = re.compile(r"new prompt, n_ctx_slot = (\d+), n_keep = -?\d+, task\.n_tokens = (\d+)")
_PROMPT_PROGRESS = re.compile(r"prompt processing, n_tokens\s*=\s*(\d+)")
_GENERATION = re.compile(r"n_gen\s*=\s*(\d+),.*?tg\s*=\s*([\d.]+) t/s(?:, tg_3s\s*=\s*([\d.]+))?")
_ACCEPTED = re.compile(r"draft tokens, new n_tokens = (\d+)")
_STOPPED = re.compile(r"stop processing: n_tokens = (\d+)")
_OUT_OF_CONTEXT = re.compile(r"running out of context capacity, prompt\.n_tokens\(\) = (\d+)")


@dataclass(slots=True)
class _Slot:
    total: int | None = None
    base: int = 0
    used: int = 0


@dataclass(slots=True)
class _Model:
    slots: dict[int, _Slot] = field(default_factory=dict)
    shared_capacity: int | None = None


class RouterTelemetry:
    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._ports: dict[int, str] = {}
        self._models: dict[str, _Model] = {}
        self._decode: deque[tuple[float, float]] = deque()
        self._session_peak: float | None = None

    def reset(self) -> None:
        self._ports.clear()
        self._models.clear()
        self._decode.clear()
        self._session_peak = None

    def observe(self, text: str) -> None:
        text = _LOG_PREFIX.sub("", text)
        child = _CHILD_LINE.match(text)
        if child is None:
            self._observe_router(text)
            return
        name = self._ports.get(int(child.group(1)), f"port {child.group(1)}")
        line = _LOG_PREFIX.sub("", child.group(2))
        self._observe_model(self._models.setdefault(name, _Model()), line)

    def _observe_router(self, text: str) -> None:
        if spawn := _SPAWN.search(text):
            name = spawn.group(1)
            self._ports = {port: alias for port, alias in self._ports.items() if alias != name}
            self._ports[int(spawn.group(2))] = name
            self._models[name] = _Model()
        elif stop := _STOP.search(text):
            self._models.pop(stop.group(1), None)
        else:
            # Without a model prefix the router itself served the model.
            self._observe_model(self._models.setdefault("", _Model()), text)

    def _observe_model(self, model: _Model, text: str) -> None:
        if slots := _SLOTS.search(text):
            capacity = int(slots.group(2))
            model.slots = {index: _Slot(total=capacity) for index in range(int(slots.group(1)))}
            unified = slots.group(3) == "true" and len(model.slots) > 1
            model.shared_capacity = capacity if unified else None
        elif slot_line := _SLOT_LINE.search(text):
            self._observe_slot(
                model.slots.setdefault(int(slot_line.group(1)), _Slot()), slot_line.group(2)
            )

    def _observe_slot(self, slot: _Slot, text: str) -> None:
        if new_slot := _NEW_SLOT.search(text):
            slot.total = int(new_slot.group(1))
        elif prompt := _NEW_PROMPT.search(text):
            slot.total = int(prompt.group(1))
            slot.base = slot.used = int(prompt.group(2))
        elif progress := _PROMPT_PROGRESS.search(text):
            slot.used = int(progress.group(1))
        elif generation := _GENERATION.search(text):
            slot.used = slot.base + int(generation.group(1))
            rate = float(generation.group(3) or generation.group(2))
            self._record_decode(rate)
        elif accepted := _ACCEPTED.search(text):
            slot.used = int(accepted.group(1))
        elif stopped := _STOPPED.search(text) or _OUT_OF_CONTEXT.search(text):
            slot.used = slot.base = int(stopped.group(1))
            self._record_decode(0.0)

    def _record_decode(self, rate: float) -> None:
        now = self._clock()
        self._decode.append((now, rate))
        if rate > (self._session_peak or 0.0):
            self._session_peak = rate
        horizon = now - ROLLING_WINDOW_SECONDS - _SAMPLE_HOLD_SECONDS
        while len(self._decode) > 1 and self._decode[1][0] < horizon:
            self._decode.popleft()

    @property
    def session_peak(self) -> float | None:
        return self._session_peak

    def rolling_peak(self) -> float | None:
        """Time-weighted decode rate over the last window; idle time counts as zero."""
        if self._session_peak is None:
            return None
        now = self._clock()
        start = now - ROLLING_WINDOW_SECONDS
        samples = list(self._decode)
        total = 0.0
        for index, (moment, rate) in enumerate(samples):
            following = samples[index + 1][0] if index + 1 < len(samples) else now
            end = min(following, moment + _SAMPLE_HOLD_SECONDS, now)
            overlap = end - max(moment, start)
            if overlap > 0:
                total += rate * overlap
        return round(total / ROLLING_WINDOW_SECONDS, 2)

    def context(self) -> list[dict[str, object]]:
        return [
            {
                "model": name or None,
                "shared_capacity": model.shared_capacity,
                "slots": [
                    {"id": index, "used": slot.used, "total": slot.total}
                    for index, slot in sorted(model.slots.items())
                ],
            }
            for name, model in self._models.items()
            if model.slots
        ]
