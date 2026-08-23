"""Thin HTTP wrapper used by the dashboard client."""

import json


class HttpClient:
    def __init__(self, policy):
        self.policy = policy

    def request_json(self, path):
        body = self._fetch(path)
        return json.loads(body)

    def _fetch(self, path):
        raise NotImplementedError("transport injected in tests")
