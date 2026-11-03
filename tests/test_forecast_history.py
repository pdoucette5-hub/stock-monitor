"""A ticker's saved forecasts, for the stock chart's projection lines."""
import unittest
from datetime import datetime, timezone
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



class QuarterlyForecastTests(unittest.TestCase):
    def run_job(self, now, snapshots):
        saved = []
        view = {
            "portfolio": [{"ticker": "SPCX", "price": 140.0, "bear_cagr_y3": 0.8, "base_cagr_y3": 1.2, "bull_cagr_y3": 1.6}],
            "watchlist": [{"ticker": "MU", "price": 90.0, "bear_cagr_y3": 0.1, "base_cagr_y3": 0.2, "bull_cagr_y3": 0.3}, {"ticker": "NOPE", "price": None}],
        }
        with mock.patch.multiple(
            forecast_service,
            load_assumption_snapshots=mock.Mock(return_value=list(snapshots)),
            save_assumption_snapshots=mock.Mock(side_effect=saved.append),
            load_scenario_inputs=mock.Mock(return_value={"SPCX": {}, "MU": {}, "NOPE": {}}),
            build_portfolio_views=mock.Mock(return_value=view),
            get_effective_tickers_config=mock.Mock(return_value={}),
            load_settings_dict=mock.Mock(return_value={}),
            build_management_snapshot=mock.Mock(return_value={"shares_by_mode": {}}),
            build_position_summaries=mock.Mock(return_value={}),
            load_earnings_calendar=mock.Mock(return_value={}),
            load_reported_fundamentals=mock.Mock(return_value={}),
        ):
            result = forecast_service.record_quarterly_forecasts(now)
        return result, saved

    def test_saves_each_priced_ticker_once_per_quarter(self):
        now = datetime(2027, 1, 4, 16, 0, tzinfo=timezone.utc)
        earlier = {"id": "x", "ticker": "SPCX", "timestamp": "2027-01-02T15:00:00+00:00", "revision_type": "quarterly"}
        last_quarter = {"id": "y", "ticker": "MU", "timestamp": "2026-12-30T15:00:00+00:00", "revision_type": "quarterly"}
        result, saved = self.run_job(now, [earlier, last_quarter])
        self.assertEqual(result["quarter"], "2027-Q1")
        self.assertEqual(result["saved"], ["MU"])
        added = saved[0][-1]
        self.assertEqual((added["ticker"], added["revision_type"], added["price"], added["base_cagr_y3"]), ("MU", "quarterly", 90.0, 0.2))

    def test_skips_weekends(self):
        result, saved = self.run_job(datetime(2027, 1, 2, 16, 0, tzinfo=timezone.utc), [])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(saved, [])

    def test_quarterly_snapshots_feed_the_chart_history(self):
        snapshots = [{"id": "q", "ticker": "SPCX", "timestamp": "2027-01-04T16:00:00+00:00", "revision_type": "quarterly", "price": 140.0, "bear_cagr_y3": 0.8, "base_cagr_y3": 1.2, "bull_cagr_y3": 1.6}]
        with mock.patch.object(forecast_service, "load_assumption_snapshots", return_value=snapshots):
            self.assertEqual([item["id"] for item in forecast_service.forecast_history("SPCX")], ["q"])


if __name__ == "__main__":
    unittest.main()
