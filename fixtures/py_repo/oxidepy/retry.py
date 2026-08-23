"""Retry policy with exponential backoff for flaky operations."""

import random
import time


class TooManyAttemptsError(RuntimeError):
    """Raised when every retry attempt has been exhausted."""

    def __init__(self, attempts: int):
        super().__init__(f"gave up after {attempts} attempts")
        self.attempts = attempts


class RetryPolicy:
    """Decides whether a failed operation should be attempted again.

    The policy grows the delay exponentially between attempts and adds a
    small jitter so many callers do not synchronize their retries.
    """

    def __init__(self, max_attempts: int = 3, base_delay_ms: int = 100, jitter: float = 0.1):
        self.max_attempts = max_attempts
        self.base_delay_ms = base_delay_ms
        self.jitter = jitter

    @property
    def exhausted(self) -> bool:
        return self.attempts_left <= 0

    def should_retry(self, attempt: int, error: Exception) -> bool:
        """Return True when `error` seen on `attempt` deserves another try."""
        if attempt >= self.max_attempts:
            return False
        # Connection errors and 5xx responses are transient; 4xx are not.
        transient = isinstance(error, (ConnectionError, TimeoutError))
        return transient or getattr(error, "status_code", 500) >= 500

    def backoff_ms(self, attempt: int) -> int:
        delay = self.base_delay_ms * (2 ** attempt)
        noise = 1.0 + random.uniform(-self.jitter, self.jitter)
        return int(delay * noise)

    def wait(self, attempt: int) -> None:
        time.sleep(self.backoff_ms(attempt) / 1000.0)
