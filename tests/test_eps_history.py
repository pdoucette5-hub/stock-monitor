"""Trailing EPS history from SEC company facts."""

import unittest

from backend.fundamentals_service import build_ttm_eps_history


def fact(start, end, filed, val, form="10-Q"):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": form}


def facts(rows):
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": rows}}}}}


class EpsHistoryTests(unittest.TestCase):
    def test_q4_from_year_less_nine_months(self):
        rows = [
            fact("2024-01-01", "2024-03-31", "2024-05-01", 1.0),
            fact("2024-04-01", "2024-06-30", "2024-08-01", 1.0),
            fact("2024-07-01", "2024-09-30", "2024-11-01", 1.0),
            fact("2024-01-01", "2024-09-30", "2024-11-01", 3.0),
            fact("2024-01-01", "2024-12-31", "2025-02-15", 5.0, "10-K"),
        ]
        history = build_ttm_eps_history(facts(rows))
        self.assertEqual(history, [{"period_end": "2024-12-31", "filed": "2025-02-15", "ttm_eps": 5.0}])

    def test_split_restates_older_quarters(self):
        # 10-for-1 split after Q2 2024: the Q3 filing restates last year's Q3.
        rows = [
            fact("2023-07-01", "2023-09-30", "2023-11-01", 10.0),
            fact("2023-10-01", "2023-12-31", "2024-02-15", 10.0),
            fact("2024-01-01", "2024-03-31", "2024-05-01", 10.0),
            fact("2024-04-01", "2024-06-30", "2024-08-01", 10.0),
            fact("2024-07-01", "2024-09-30", "2024-11-01", 1.0),
            fact("2023-07-01", "2023-09-30", "2024-11-01", 1.0),
        ]
        history = build_ttm_eps_history(facts(rows))
        self.assertEqual([h["ttm_eps"] for h in history], [4.0, 4.0])
        self.assertEqual(history[-1]["filed"], "2024-11-01")

    def test_gap_in_quarters_breaks_the_window(self):
        rows = [
            fact("2024-01-01", "2024-03-31", "2024-05-01", 1.0),
            fact("2024-04-01", "2024-06-30", "2024-08-01", 1.0),
            fact("2024-10-01", "2024-12-31", "2025-02-15", 1.0),
            fact("2025-01-01", "2025-03-31", "2025-05-01", 1.0),
        ]
        self.assertEqual(build_ttm_eps_history(facts(rows)), [])


if __name__ == "__main__":
    unittest.main()
