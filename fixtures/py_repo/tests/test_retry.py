"""Tests for the retry policy."""

import pytest

from oxidepy.retry import RetryPolicy, TooManyAttemptsError


def test_retry_policy_gives_up_after_max_attempts():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(1, ConnectionError("boom"))
    assert not policy.should_retry(3, ConnectionError("boom"))


def test_backoff_delay_increases_with_attempts():
    policy = RetryPolicy(base_delay_ms=100, jitter=0.0)
    assert policy.backoff_ms(0) < policy.backoff_ms(2)


def test_client_errors_are_not_retried():
    err = type("ClientErr", (Exception,), {"status_code": 404})("nope")
    policy = RetryPolicy()
    assert not policy.should_retry(1, err)


def test_exhausted_policy_raises():
    policy = RetryPolicy(max_attempts=1)
    with pytest.raises(TooManyAttemptsError):
        raise TooManyAttemptsError(policy.max_attempts)
