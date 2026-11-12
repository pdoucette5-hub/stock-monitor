"""Combined Fidelity rows that a later export splits are counted once."""

import unittest

from backend.ledger_cleanup import remove_split_duplicates


def row(shares, amount, source_file, account="Julianne Rollover IRA", tx_type="sell", price=113.69, date="2026-10-30"):
    return {"date": date, "type": tx_type, "shares": shares, "price_per_share": price, "account": account,
            "source": "fidelity-csv", "source_file": source_file, "source_amount": amount}


class SplitDuplicateTests(unittest.TestCase):
    def test_combined_sale_dropped_when_split_rows_match(self):
        ledger = {"ELF": [row(401.175, 45606.64, "(27).csv"), row(0.175, 19.89, "(29).csv"), row(401.0, 45586.75, "(29).csv")]}
        removed = remove_split_duplicates(ledger)
        self.assertEqual([r["shares"] for r in removed], [401.175])
        self.assertEqual(sorted(r["shares"] for r in ledger["ELF"]), [0.175, 401.0])

    def test_combined_buy_dropped(self):
        ledger = {"VENU": [row(3200.135, -3936.17, "(24).csv", tx_type="buy", price=1.23), row(3200.0, -3936.0, "(25).csv", tx_type="buy", price=1.23), row(0.135, -0.17, "(25).csv", tx_type="buy", price=1.23)]}
        self.assertEqual(len(remove_split_duplicates(ledger)), 1)
        self.assertEqual(len(ledger["VENU"]), 2)

    def test_separate_trades_from_one_file_are_kept(self):
        # Three real buys the same day in one export: nothing to drop.
        ledger = {"VENU": [row(300, -369, "(24).csv", tx_type="buy", price=1.23), row(200, -246, "(24).csv", tx_type="buy", price=1.23), row(100, -123, "(24).csv", tx_type="buy", price=1.23)]}
        self.assertEqual(remove_split_duplicates(ledger), [])
        self.assertEqual(len(ledger["VENU"]), 3)

    def test_different_accounts_are_not_matched(self):
        ledger = {"ELF": [row(401.175, 45606.64, "(27).csv"), row(0.175, 19.89, "(29).csv", account="Paul ROTH IRA"), row(401.0, 45586.75, "(29).csv")]}
        self.assertEqual(remove_split_duplicates(ledger), [])

    def test_manual_entries_are_left_alone(self):
        manual = {"date": "2026-10-30", "type": "sell", "shares": 401.175, "price_per_share": 113.69, "account": "Julianne Rollover IRA"}
        ledger = {"ELF": [manual, row(0.175, 19.89, "(29).csv"), row(401.0, 45586.75, "(29).csv")]}
        self.assertEqual(remove_split_duplicates(ledger), [])


if __name__ == "__main__":
    unittest.main()
