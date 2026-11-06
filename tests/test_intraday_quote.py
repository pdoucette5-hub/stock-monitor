"""When the holdings view swaps in the latest 15-minute quote."""

import unittest
from datetime import datetime, timedelta, timezone

from backend.portfolio_service import _apply_intraday_quote

NOW = datetime(2026, 11, 6, 15, 40, tzinfo=timezone.utc)
DAY = "2026-11-06"


def sample(minutes_ago, close, day=DAY):
    return {"timestamp": int((NOW - timedelta(minutes=minutes_ago)).timestamp()), "date": day, "close": close}


def sheet_row(price, fetched_minutes_ago, day=DAY):
    return {
        "ticker": "MU",
        "price": price,
        "price_date": day,
        "price_fetched_at": (NOW - timedelta(minutes=fetched_minutes_ago)).isoformat(),
        "cache_source": "googlefinance",
    }


class IntradayQuoteTests(unittest.TestCase):
    def test_stale_sheet_price_replaced_by_newer_sample(self):
        # Sheet fetched at 15:25 UTC still said 707.42; MU traded 691.17 at 15:30.
        result = _apply_intraday_quote(sheet_row(707.42, 15), [sample(25, 703.41), sample(10, 691.17)])
        self.assertEqual(result["price"], 691.17)
        self.assertEqual(result["cache_source"], "intraday")

    def test_sheet_price_kept_when_sample_is_older_than_its_quote(self):
        # Nightly push of the official close comes hours after the last sample.
        row = sheet_row(656.39, 0, day="2026-11-05")
        result = _apply_intraday_quote(row, [sample(12 * 60, 656.0, day="2026-11-05")])
        self.assertIs(result, row)

    def test_sample_from_earlier_day_never_replaces_newer_price(self):
        row = sheet_row(700.0, 60 * 24)
        old = sample(60 * 23, 650.0, day="2026-11-05")
        self.assertIs(_apply_intraday_quote(row, [old]), row)

    def test_live_quote_kept(self):
        row = {"ticker": "MU", "price": 690.0, "cache_source": "live"}
        self.assertIs(_apply_intraday_quote(row, [sample(1, 691.0)]), row)

    def test_old_cached_quote_replaced(self):
        row = {"ticker": "MU", "price": 400.0, "cache_source": "cache", "cache_fetched_at": (NOW - timedelta(days=150)).timestamp()}
        self.assertEqual(_apply_intraday_quote(row, [sample(5, 691.0)])["price"], 691.0)

    def test_cash_row_kept(self):
        row = {"ticker": "SPAXX", "price": 1.0, "cache_source": "none"}
        self.assertIs(_apply_intraday_quote(row, [sample(5, 1.0)]), row)

    def test_no_samples(self):
        row = sheet_row(707.42, 15)
        self.assertIs(_apply_intraday_quote(row, []), row)
        self.assertIs(_apply_intraday_quote(row, None), row)


if __name__ == "__main__":
    unittest.main()
