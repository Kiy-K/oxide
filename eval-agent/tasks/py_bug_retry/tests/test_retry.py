import unittest

from app.retry import RetryPolicy


class TestBackoff(unittest.TestCase):
    def test_backoff_grows_with_attempts(self):
        p = RetryPolicy(base_delay_ms=100)
        b0 = p.backoff_ms(0)
        b1 = p.backoff_ms(1)
        b2 = p.backoff_ms(2)
        self.assertLess(b0, b1, "sequence must increase")
        self.assertLess(b1, b2)
        self.assertEqual(b0, 100)
        self.assertEqual(b1, 200)
        self.assertEqual(b2, 400)

    def test_gives_up_after_max_attempts(self):
        err = TimeoutError("slow")
        p = RetryPolicy(max_attempts=3)
        self.assertTrue(p.should_retry(1, err))
        self.assertFalse(p.should_retry(3, err))


if __name__ == "__main__":
    unittest.main()
