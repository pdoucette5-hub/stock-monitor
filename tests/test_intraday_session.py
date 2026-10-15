"""Stale intraday samples must not be shown as today's."""
import unittest
from unittest import mock

from backend import intraday_store


class SessionTests(unittest.TestCase):
    def test_stale_ticker_gets_no_samples(self):
        store = {
            "MU": [{"timestamp": 1, "date": "2026-10-13", "close": 936.7}],
            "VENU": [{"timestamp": 2, "date": "2026-10-13", "close": 1.2}, {"timestamp": 3, "date": "2026-10-15", "close": 1.13}],
        }
        with mock.patch.object(intraday_store, "load_intraday_prices", return_value=store):
            result = intraday_store.latest_session_prices(["MU", "VENU", "NONE"])
        self.assertEqual(result["MU"], [])
        self.assertEqual([r["close"] for r in result["VENU"]], [1.13])
        self.assertEqual(result["NONE"], [])


if __name__ == "__main__":
    unittest.main()
