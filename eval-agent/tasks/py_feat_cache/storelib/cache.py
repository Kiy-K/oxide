"""In-memory TTL cache used by the pricing service."""

import time


class TTLCache:
    """Caches values until their time-to-live expires."""

    def __init__(self, ttl_seconds=60.0):
        self.ttl_seconds = ttl_seconds
        self._entries = {}

    def get_or_set(self, key, producer):
        """Return cached `key` if fresh; otherwise compute via `producer`,
        cache it with the current monotonic time, and return it.

        A stale entry must be recomputed, never returned.
        """
        raise NotImplementedError("implement me")

    def invalidate(self, key):
        self._entries.pop(key, None)
