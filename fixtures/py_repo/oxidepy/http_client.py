"""Minimal HTTP client with retry support."""

import json
import urllib.error
import urllib.request

from .retry import RetryPolicy, TooManyAttemptsError


def parse_headers(raw_headers):
    """Normalize urllib header tuples into a plain dict."""
    return {key.lower(): value for key, value in raw_headers.items()}


class HttpClient:
    """GET/JSON helper that retries transient failures."""

    def __init__(self, base_url: str, policy: RetryPolicy | None = None):
        self.base_url = base_url.rstrip("/")
        self.policy = policy or RetryPolicy()

    def fetch(self, path: str) -> bytes:
        """Fetch `path` and return the raw body, retrying failed requests."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(url, timeout=5.0) as response:
                    return response.read()
            except (urllib.error.HTTPError, urllib.error.URLError) as err:
                attempt += 1
                if not self.policy.should_retry(attempt, err):
                    raise TooManyAttemptsError(attempt) from err
                self.policy.wait(attempt)

    def request_json(self, path: str):
        body = self.fetch(path)
        return json.loads(body.decode("utf-8"))
