"""Which price the holdings view shows when market data comes from the cache.

Run from the repository root:  python -m unittest tests/test_price_fallback.py
"""

import math
import time
import unittest
from datetime import date, timedelta

from backend.portfolio_service import _apply_price_store_fallback

TODAY = date.today().isoformat()
LONG_AGO = time.time() - 150 * 86400  # roughly five months ago


def store(price, day=TODAY):
    return {"ORCL": [{"date": day, "close": price, "source": "googlefinance"}]}


class PriceFallbackTests(unittest.TestCase):
    def test_months_old_cached_quote_is_replaced_by_imported_price(self):
        row = {"ticker": "ORCL", "price": 192.1, "cache_source": "cache", "cache_fetched_at": LONG_AGO}
        result = _apply_price_store_fallback(row, "ORCL", store(141.25))
        self.assertEqual(result["price"], 141.25)
        self.assertEqual(result["price_date"], TODAY)

    def test_failed_live_fetch_falls_back_to_imported_price(self):
        row = {"ticker": "ORCL", "price": 192.1, "cache_source": "cache-fallback", "cache_fetched_at": LONG_AGO}
        self.assertEqual(_apply_price_store_fallback(row, "ORCL", store(141.25))["price"], 141.25)

    def test_cached_quote_newer_than_imported_price_is_kept(self):
        row = {"ticker": "ORCL", "price": 143.0, "cache_source": "cache", "cache_fetched_at": time.time()}
        yesterday = (date.today() - timedelta(days=2)).isoformat()
        self.assertEqual(_apply_price_store_fallback(row, "ORCL", store(139.0, yesterday))["price"], 143.0)

    def test_live_quote_is_kept(self):
        row = {"ticker": "ORCL", "price": 143.5, "cache_source": "live"}
        self.assertIs(_apply_price_store_fallback(row, "ORCL", store(141.25)), row)

    def test_cached_quote_kept_when_no_imported_price(self):
        row = {"ticker": "ORCL", "price": 192.1, "cache_source": "cache", "cache_fetched_at": LONG_AGO}
        self.assertEqual(_apply_price_store_fallback(row, "ORCL", {})["price"], 192.1)

    def test_unknown_cache_age_prefers_imported_price(self):
        row = {"ticker": "ORCL", "price": 192.1, "cache_source": "cache", "cache_fetched_at": math.nan}
        self.assertEqual(_apply_price_store_fallback(row, "ORCL", store(141.25))["price"], 141.25)

    def test_missing_price_uses_imported_price_as_before(self):
        row = {"ticker": "ORCL", "price": None, "cache_source": "none"}
        self.assertEqual(_apply_price_store_fallback(row, "ORCL", store(141.25))["price"], 141.25)


if __name__ == "__main__":
    unittest.main()
