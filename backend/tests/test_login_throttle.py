from __future__ import annotations

import pytest

from llamawebui.services.login_throttle import LoginRateLimitedError, LoginThrottle


def test_login_throttle_limits_concurrent_attempts() -> None:
    throttle = LoginThrottle(backoff_base_seconds=0)
    first = throttle.begin("Admin", "127.0.0.1")

    with pytest.raises(LoginRateLimitedError):
        throttle.begin("admin", "127.0.0.1")

    throttle.abort(first)


def test_login_throttle_locks_out_after_repeated_failures() -> None:
    throttle = LoginThrottle(
        failure_threshold=2,
        backoff_base_seconds=0,
        lockout_seconds=60,
    )

    for _ in range(2):
        attempt = throttle.begin("admin", "127.0.0.1")
        throttle.failure(attempt)

    with pytest.raises(LoginRateLimitedError) as error:
        throttle.begin("admin", "127.0.0.1")
    assert error.value.retry_after >= 1


def test_login_throttle_success_clears_account_and_client_failures() -> None:
    throttle = LoginThrottle(backoff_base_seconds=0)
    failed = throttle.begin("admin", "127.0.0.1")
    throttle.failure(failed)

    successful = throttle.begin("admin", "127.0.0.1")
    throttle.success(successful)

    next_attempt = throttle.begin("admin", "127.0.0.1")
    throttle.abort(next_attempt)


def test_login_throttle_validates_configuration() -> None:
    with pytest.raises(ValueError, match="threshold"):
        LoginThrottle(failure_threshold=0)
    with pytest.raises(ValueError, match="lockout"):
        LoginThrottle(lockout_seconds=0)
    with pytest.raises(ValueError, match="base"):
        LoginThrottle(backoff_base_seconds=-1)
    with pytest.raises(ValueError, match="maximum"):
        LoginThrottle(backoff_base_seconds=2, backoff_max_seconds=1)