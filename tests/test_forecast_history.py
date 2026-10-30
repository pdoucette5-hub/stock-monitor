"""A ticker's saved forecasts, for the stock chart's projection lines."""
import unittest
from unittest import mock

from backend import forecast_service


class ForecastHistoryTests(unittest.TestCase):
    def test_returns_only_complete_forecasts_for_the_ticker_oldest_first(self):
        snapshots = [
            {"id": "b", "ticker": "spcx", "timestamp": "2026-10-22T00:00:00+00:00", "price": 150.0, "bear_cagr_y3": 0.8, "base_cagr_y3": 1.2, "bull_cagr_y3": 1.6},
            {"id": "a", "ticker": "SPCX", "timestamp": "2026-07-22T00:00:00+00:00", "price": 120.0, "bear_cagr_y3": 0.5, "base_cagr_y3": 0.9, "bull_cagr_y3": 1.3},
            {"id": "c", "ticker": "SPCX", "timestamp": "2026-08-01T00:00:00+00:00", "price": None, "bear_cagr_y3": 0.5, "base_cagr_y3": 0.9, "bull_cagr_y3": 1.3},
            {"id": "d", "ticker": "SPCX", "timestamp": "2026-08-02T00:00:00+00:00", "price": 130.0, "bear_cagr_y3": None, "base_cagr_y3": 0.9, "bull_cagr_y3": 1.3},
            {"id": "e", "ticker": "MU", "timestamp": "2026-09-01T00:00:00+00:00", "price": 90.0, "bear_cagr_y3": 0.1, "base_cagr_y3": 0.2, "bull_cagr_y3": 0.3},
        ]
        with mock.patch.object(forecast_service, "load_assumption_snapshots", return_value=snapshots):
            history = forecast_service.forecast_history("SPCX")
        self.assertEqual([item["id"] for item in history], ["a", "b"])
        self.assertEqual(history[0]["base_cagr_y3"], 0.9)


if __name__ == "__main__":
    unittest.main()
