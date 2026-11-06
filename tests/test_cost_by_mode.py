"""Average cost follows the Holdings filter (VENU-like accounts)."""

import unittest

from backend.management_service import build_management_snapshot


def buy(account, shares, price):
    return {"date": "2026-01-02", "type": "buy", "shares": shares, "price_per_share": price, "fees": 0.0, "account": account}


class CostByModeTests(unittest.TestCase):
    def setUp(self):
        transactions = {
            "VENU": [
                buy("Paul ROTH IRA", 1000, 2.00),
                buy("Paul Rollover IRA", 500, 2.30),
                buy("Individual - TOD", 400, 1.45),
            ],
        }
        settings = {
            "account_defaults": {"Paul ROTH IRA": "managed", "Paul Rollover IRA": "managed", "Individual - TOD": "track"},
            "position_overrides": {},
        }
        self.bucket = build_management_snapshot(
            transactions=transactions, settings=settings, manual_allocations=[], config={"portfolio": []},
        )["shares_by_mode"]["VENU"]

    def test_managed_and_tracked_costs_are_separate(self):
        self.assertAlmostEqual(self.bucket["managed_cost"], 2000 + 1150)
        self.assertAlmostEqual(self.bucket["managed_cost_shares"], 1500)
        self.assertAlmostEqual(self.bucket["track_cost"], 580)
        self.assertAlmostEqual(self.bucket["track_cost_shares"], 400)
        self.assertAlmostEqual(self.bucket["managed_cost"] / self.bucket["managed_cost_shares"], 2.10)

    def test_share_counts_unchanged(self):
        self.assertEqual(self.bucket["managed"], 1500)
        self.assertEqual(self.bucket["track"], 400)
        self.assertEqual(self.bucket["excluded"], 0.0)


if __name__ == "__main__":
    unittest.main()
