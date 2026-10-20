"""Stale intraday samples must not be shown as today's."""
import unittest
from unittest import mock

from backend import intraday_store


class SessionTests(unittest.TestCase):
    def test_stale_ticker_gets_no_samples(self):
        # Timestamps are 11:00 New York time (inside the session); the date field picks the session.
        store = {
            "MU": [{"timestamp": 1792076400, "date": "2026-10-13", "close": 936.7}],
            "VENU": [{"timestamp": 1792076401, "date": "2026-10-13", "close": 1.2}, {"timestamp": 1792249200, "date": "2026-10-15", "close": 1.13}],
        }
        with mock.patch.object(intraday_store, "load_intraday_prices", return_value=store):
            result = intraday_store.latest_session_prices(["MU", "VENU", "NONE"])
        self.assertEqual(result["MU"], [])
        self.assertEqual([r["close"] for r in result["VENU"]], [1.13])
        self.assertEqual(result["NONE"], [])


    def test_pre_market_and_after_hours_samples_are_left_out(self):
        # 2026-10-20 New York time: 8:30, 9:30, 12:00, 16:00 and 17:00.
        store = {"VENU": [
            {"timestamp": 1792499400, "date": "2026-10-20", "close": 1.14},
            {"timestamp": 1792503000, "date": "2026-10-20", "close": 1.17},
            {"timestamp": 1792512000, "date": "2026-10-20", "close": 1.25},
            {"timestamp": 1792526400, "date": "2026-10-20", "close": 1.3},
            {"timestamp": 1792530000, "date": "2026-10-20", "close": 1.4},
        ]}
        with mock.patch.object(intraday_store, "load_intraday_prices", return_value=store):
            result = intraday_store.latest_session_prices(["VENU"])
        self.assertEqual([r["close"] for r in result["VENU"]], [1.17, 1.25, 1.3])


if __name__ == "__main__":
    unittest.main()
