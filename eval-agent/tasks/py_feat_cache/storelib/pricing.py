"""Price lookup helpers built on top of the cache."""

from .cache import TTLCache


class PricingService:
    def __init__(self, rates):
        self.rates = rates
        self.cache = TTLCache(ttl_seconds=30.0)
        self.lookups = 0

    def price_for(self, symbol):
        def compute():
            self.lookups += 1
            return self.rates[symbol]
        return self.cache.get_or_set(symbol, compute)
