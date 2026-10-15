"""YTD starts from the last close of the previous year."""
import unittest

from backend.price_store import _filter_rows_for_range


class YtdTests(unittest.TestCase):
    def test_starts_at_last_close_of_prior_year(self):
        rows = [{"date": d, "close": i} for i, d in enumerate(["2025-12-29", "2025-12-31", "2026-01-02", "2026-06-01", "2026-10-15"])]
        self.assertEqual([r["date"] for r in _filter_rows_for_range(rows, "ytd")], ["2025-12-31", "2026-01-02", "2026-06-01", "2026-10-15"])

    def test_no_prior_year_history(self):
        rows = [{"date": "2026-03-01", "close": 1}, {"date": "2026-10-15", "close": 2}]
        self.assertEqual(len(_filter_rows_for_range(rows, "ytd")), 2)


if __name__ == "__main__":
    unittest.main()
