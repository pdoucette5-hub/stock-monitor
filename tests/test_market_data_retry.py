"""Normal page loads should not keep retrying tickers that Finnhub can't serve.

Run from the repository root:  python -m unittest tests/test_market_data_retry.py
"""

import threading
import time
import unittest
from unittest import mock

from backend import data_ingest


class FailedFetchRetryTests(unittest.TestCase):
    def setUp(self):
        data_ingest._failed_fetches.clear()
        self.calls = []
        patches = [
            mock.patch.object(data_ingest, "FINNHUB_API_KEY", "test-key"),
            mock.patch.object(data_ingest, "_read_cache", return_value=None),
            mock.patch.object(data_ingest, "_write_cache"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fetch_fails(self, symbol):
        self.calls.append(symbol)
        raise RuntimeError("403 Forbidden")

    def test_failed_ticker_is_not_retried_on_the_next_load(self):
        with mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=self.fetch_fails):
            first = data_ingest.get_live_market_data(["CWGIX"], max_age_hours=99999)
            second = data_ingest.get_live_market_data(["CWGIX"], max_age_hours=99999)
        self.assertEqual(self.calls, ["CWGIX"])
        for df in (first, second):
            self.assertEqual(df.iloc[0]["cache_source"], "none")
            self.assertEqual(df.iloc[0]["status"], "ERROR: 403 Forbidden")

    def test_update_prices_always_retries(self):
        with mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=self.fetch_fails):
            data_ingest.get_live_market_data(["CWGIX"], max_age_hours=99999)
            data_ingest.get_live_market_data(["CWGIX"], force_refresh=True, max_age_hours=12)
        self.assertEqual(self.calls, ["CWGIX", "CWGIX"])

    def test_retry_after_the_wait_period(self):
        with mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=self.fetch_fails):
            data_ingest.get_live_market_data(["CWGIX"], max_age_hours=99999)
            failed_at, status = data_ingest._failed_fetches["CWGIX"]
            data_ingest._failed_fetches["CWGIX"] = (failed_at - data_ingest.FAILED_FETCH_RETRY_SECONDS - 1, status)
            data_ingest.get_live_market_data(["CWGIX"], max_age_hours=99999)
        self.assertEqual(self.calls, ["CWGIX", "CWGIX"])

    def test_successful_fetch_clears_the_failure(self):
        with mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=self.fetch_fails):
            data_ingest.get_live_market_data(["CWGIX"], max_age_hours=99999)
        ok_row = {"ticker": "CWGIX", "price": 70.0, "status": "OK", "cache_source": "live"}
        with mock.patch.object(data_ingest, "_get_market_data_for_ticker", return_value=ok_row):
            df = data_ingest.get_live_market_data(["CWGIX"], force_refresh=True, max_age_hours=12)
        self.assertEqual(df.iloc[0]["price"], 70.0)
        self.assertNotIn("CWGIX", data_ingest._failed_fetches)

    def test_other_tickers_still_fetched(self):
        ok_row = {"ticker": "ORCL", "price": 141.0, "status": "OK", "cache_source": "live"}
        def fetch(symbol):
            if symbol == "CWGIX":
                return self.fetch_fails(symbol)
            self.calls.append(symbol)
            return dict(ok_row)
        with mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=fetch):
            df = data_ingest.get_live_market_data(["CWGIX", "ORCL"], max_age_hours=99999)
        self.assertEqual(self.calls, ["CWGIX", "ORCL"])
        self.assertEqual(list(df["ticker"]), ["CWGIX", "ORCL"])


class ParallelFetchTests(unittest.TestCase):
    """A normal load fetches uncached tickers together; Update prices does not."""

    FUNDS = ["ACRNX", "CWGIX", "SENAX", "SGRAX", "SPAXX", "TEDIX"]

    def setUp(self):
        data_ingest._failed_fetches.clear()
        self.active = 0
        self.max_active = 0
        self.order = []
        self.lock = threading.Lock()
        patches = [
            mock.patch.object(data_ingest, "FINNHUB_API_KEY", "test-key"),
            mock.patch.object(data_ingest, "_write_cache"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def slow_fail(self, symbol):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.order.append(symbol)
        time.sleep(0.4)
        with self.lock:
            self.active -= 1
        raise RuntimeError("403 Forbidden")

    def cached_or_none(self, ticker, max_age_hours):
        if ticker in self.FUNDS:
            return None
        return {"ticker": ticker, "price": 100.0, "status": "OK"}

    def test_uncached_tickers_are_fetched_together(self):
        tickers = ["ORCL", *self.FUNDS, "MU"]
        with mock.patch.object(data_ingest, "_read_cache", side_effect=self.cached_or_none), \
             mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=self.slow_fail):
            started = time.perf_counter()
            df = data_ingest.get_live_market_data(tickers, max_age_hours=99999)
            elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.2, "six 0.4s fetches should overlap")
        self.assertGreater(self.max_active, 1)
        self.assertEqual(list(df["ticker"]), tickers)
        self.assertEqual(list(df["cache_source"]), ["cache", *["none"] * 6, "cache"])

    def test_update_prices_fetches_one_at_a_time(self):
        with mock.patch.object(data_ingest, "_read_cache", return_value=None), \
             mock.patch.object(data_ingest, "_get_market_data_for_ticker", side_effect=self.slow_fail):
            df = data_ingest.get_live_market_data(self.FUNDS[:3], force_refresh=True, max_age_hours=12)
        self.assertEqual(self.max_active, 1)
        self.assertEqual(self.order, self.FUNDS[:3])
        self.assertEqual(list(df["ticker"]), self.FUNDS[:3])


if __name__ == "__main__":
    unittest.main()
