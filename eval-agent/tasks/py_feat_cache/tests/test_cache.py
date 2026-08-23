import unittest
from unittest import mock

from storelib.pricing import PricingService


class TestTTLCache(unittest.TestCase):
    def test_caches_computed_value(self):
        svc = PricingService({"AAPL": 182.5})
        self.assertEqual(svc.price_for("AAPL"), 182.5)
        self.assertEqual(svc.price_for("AAPL"), 182.5)
        self.assertEqual(svc.lookups, 1)

    def test_expired_entry_is_recomputed(self):
        svc = PricingService({"MSFT": 410.0})
        with mock.patch("storelib.cache.time.monotonic", side_effect=[1000.0, 1100.0]):
            first = svc.price_for("MSFT")
            second = svc.price_for("MSFT")
        self.assertEqual(first, second)
        self.assertEqual(svc.lookups, 2)

    def test_fresh_entry_is_not_recomputed(self):
        svc = PricingService({"GOOG": 171.2})
        with mock.patch("storelib.cache.time.monotonic", side_effect=[1000.0, 1010.0]):
            svc.price_for("GOOG")
            svc.price_for("GOOG")
        self.assertEqual(svc.lookups, 1)


if __name__ == "__main__":
    unittest.main()
