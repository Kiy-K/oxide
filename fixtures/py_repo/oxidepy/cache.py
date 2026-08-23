"""Tiny in-memory TTL cache."""

import time


class TTLCache:
    """Caches values until their time-to-live expires."""

    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl_seconds = ttl_seconds
        self._entries = {}

    def get_or_set(self, key: str, producer):
        """Return cached `key`, or compute it with `producer` and cache it."""
        hit = self._entries.get(key)
        now = time.monotonic()
        if hit is not None and (now - hit[1]) < self.ttl_seconds:
            return hit[0]
        value = producer()
        self._entries[key] = (value, now)
        return value

    def invalidate(self, key: str) -> None:
        """Drop a single expired or unwanted entry."""
        self._entries.pop(key, None)
