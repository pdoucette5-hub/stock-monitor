"""The trades endpoint used by the Holdings summary.

Run from the repository root:  python -m unittest tests/test_transactions_window.py
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from backend.routers import transactions as router

TXS = {
    "AAA": [
        {"id": "1", "date": "2026-09-01", "type": "buy", "shares": 10, "price_per_share": 5, "account": "Roth"},
        {"id": "2", "date": "2026-10-02", "type": "sell", "shares": 4, "price_per_share": 6, "account": "Roth"},
        {"id": "3", "date": "2026-10-03", "type": "dividend", "shares": 0, "price_per_share": 1, "account": "Roth"},
        {"id": "4", "date": "2026-10-04", "type": "buy", "shares": 2, "price_per_share": 7, "account": "401k"},
    ],
}


def fake_filter(transactions, mode):
    account = {"managed": "Roth", "track": "401k"}.get(mode)
    return {t: [e for e in rows if e["account"] == account] for t, rows in transactions.items()}


class WindowTests(unittest.TestCase):
    def call(self, since, modes="all"):
        with mock.patch.object(router, "load_transactions", return_value=TXS), \
             mock.patch.object(router, "filter_transactions_by_management_mode", side_effect=fake_filter):
            return router.get_transactions_window(since, modes)

    def test_only_share_changing_trades_in_window(self):
        result = self.call("2026-10-01")
        self.assertEqual([t["date"] for t in result["trades"]["AAA"]], ["2026-10-02", "2026-10-04"])
        self.assertNotIn("account", result["trades"]["AAA"][0])

    def test_modes_filter_and_combine(self):
        self.assertEqual(len(self.call("2026-10-01", "managed")["trades"]["AAA"]), 1)
        self.assertEqual(len(self.call("2026-10-01", "managed,track")["trades"]["AAA"]), 2)

    def test_bad_input(self):
        with self.assertRaises(HTTPException):
            self.call("yesterday")
        with self.assertRaises(HTTPException):
            self.call("2026-10-01", "bogus")


if __name__ == "__main__":
    unittest.main()
