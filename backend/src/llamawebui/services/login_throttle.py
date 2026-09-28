"""In-memory throttling for control-plane login attempts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Lock
from time import monotonic


class LoginRateLimitedError(Exception):
    """Raised when a login attempt must be delayed or rejected."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("too many login attempts; try again later")
        self.retry_after = max(1, math.ceil(retry_after))


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    keys: tuple[str, ...]
    delay_seconds: float = 0.0


@dataclass(slots=True)
class _Bucket:
    failures: int = 0
    retry_at: float = 0.0
    locked_until: float = 0.0
    retire_at: float = 0.0
    active: int = 0


class LoginThrottle:
    """Apply account- and client-address limits without persistent credentials state."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        lockout_seconds: float = 60.0,
        backoff_base_seconds: float = 0.25,
        backoff_max_seconds: float = 5.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("login failure threshold must be positive")
        if lockout_seconds <= 0:
            raise ValueError("login lockout must be positive")
        if backoff_base_seconds < 0:
            raise ValueError("login backoff base must not be negative")
        if backoff_max_seconds < backoff_base_seconds:
            raise ValueError("login backoff maximum must not be below its base")
        self._failure_threshold = failure_threshold
        self._lockout_seconds = lockout_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def begin(self, username: str, client_ip: str) -> LoginAttempt:
        keys = (f"user:{username.strip().casefold()}", f"ip:{client_ip}")
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            locked_for = max(
                (
                    max(self._buckets.get(key, _Bucket()).locked_until - now, 0.0)
                    for key in keys
                ),
                default=0.0,
            )
            if locked_for > 0:
                raise LoginRateLimitedError(locked_for)
            if any(self._buckets.get(key, _Bucket()).active for key in keys):
                raise LoginRateLimitedError(1.0)
            delay = max(
                (
                    max(self._buckets.get(key, _Bucket()).retry_at - now, 0.0)
                    for key in keys
                ),
                default=0.0,
            )
            for key in keys:
                self._buckets.setdefault(key, _Bucket()).active += 1
        return LoginAttempt(keys, delay)

    def success(self, attempt: LoginAttempt) -> None:
        with self._lock:
            for key in attempt.keys:
                self._buckets.pop(key, None)

    def failure(self, attempt: LoginAttempt) -> None:
        now = monotonic()
        with self._lock:
            for key in attempt.keys:
                bucket = self._buckets.setdefault(key, _Bucket())
                bucket.active = max(bucket.active - 1, 0)
                bucket.failures += 1
                if bucket.failures >= self._failure_threshold:
                    bucket.locked_until = now + self._lockout_seconds
                    bucket.retry_at = 0.0
                else:
                    delay = min(
                        self._backoff_base_seconds * (2 ** (bucket.failures - 1)),
                        self._backoff_max_seconds,
                    )
                    bucket.retry_at = now + delay
                bucket.retire_at = now + self._lockout_seconds
            self._remove_expired(now)

    def abort(self, attempt: LoginAttempt) -> None:
        with self._lock:
            for key in attempt.keys:
                bucket = self._buckets.get(key)
                if bucket is None:
                    continue
                bucket.active = max(bucket.active - 1, 0)
                if bucket.active == 0 and bucket.failures == 0:
                    self._buckets.pop(key, None)

    @staticmethod
    def _retry_after(bucket: _Bucket | None, now: float) -> float:
        if bucket is None:
            return 0.0
        return max(bucket.locked_until, bucket.retry_at, 0.0) - now

    def _remove_expired(self, now: float) -> None:
        for key, bucket in tuple(self._buckets.items()):
            if bucket.active:
                continue
            if bucket.locked_until > now or bucket.retry_at > now or bucket.retire_at > now:
                continue
            self._buckets.pop(key, None)