"""Retry policy for flaky network operations."""


class RetryPolicy:
    """Retries failed requests with increasing delays."""

    def __init__(self, max_attempts=3, base_delay_ms=100):
        self.max_attempts = max_attempts
        self.base_delay_ms = base_delay_ms

    def should_retry(self, attempt, error):
        if attempt >= self.max_attempts:
            return False
        status = getattr(error, "status_code", 500)
        return isinstance(error, (ConnectionError, TimeoutError)) or status >= 500

    def backoff_ms(self, attempt):
        # BUG: delay shrinks instead of growing; callers starve the server.
        return self.base_delay_ms // (attempt + 1)

    def wait(self, attempt):
        import time
        time.sleep(self.backoff_ms(attempt) / 1000.0)
