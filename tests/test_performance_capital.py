"""Gain/loss treats only external money as contributions.

Selling one stock to buy another is internal; cash waiting to be reinvested is
still part of the portfolio; dividends are gain; new money is not gain.

Run from the repository root:  python -m unittest tests/test_performance_capital.py
"""

import datetime as dt
import unittest
from unittest import mock

from backend import performance_service


def weekdays(start, end):
    day = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    while day <= stop:
        if day.weekday() < 5:
            yield day.isoformat()
        day += dt.timedelta(days=1)


def flat_prices(*tickers, price=100.0, start="2026-06-01", end="2026-08-31", overrides=None):
    store = {}
    for ticker in (*tickers, "SPY"):
        store[ticker] = [{"date": d, "close": price, "source": "test"} for d in weekdays(start, end)]
    for (ticker, date), close in (overrides or {}).items():
        for row in store[ticker]:
            if row["date"] >= date:
                row["close"] = close
    return store


def tx(id_, date, type_, shares, price, ticker):
    return {"id": id_, "date": date, "type": type_, "shares": shares, "price_per_share": price, "account": "Rollover IRA", "ticker": ticker}


def series_for(transactions, store, holdings):
    def points(ticker, range_key="3y", store=None):
        return [dict(row) for row in (store or {}).get(ticker, [])]
    with mock.patch.object(performance_service, "load_price_history_store", return_value=store), \
         mock.patch.object(performance_service, "get_price_points_for_ticker", side_effect=lambda t, range_key="3y", store=None: points(t, store=store_ref[0])):
        store_ref[0] = store
        payload = performance_service.build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config={"portfolio": [{"ticker": t, "shares": s} for t, s in holdings.items()], "watchlist": []},
            range_key="3y",
        )
    return {row["date"]: row for row in payload["series"]}


store_ref = [None]


class CapitalTests(unittest.TestCase):
    def test_selling_and_holding_cash_is_not_a_loss(self):
        store = flat_prices("AAA", "BBB")
        s = series_for(
            {"AAA": [tx("a1", "2026-06-01", "buy", 100, 100, "AAA"), tx("a2", "2026-07-01", "sell", 100, 100, "AAA")],
             "BBB": [tx("b1", "2026-08-03", "buy", 100, 100, "BBB")]},
            store, {"BBB": 100})
        for day in ("2026-06-30", "2026-07-01", "2026-07-15", "2026-08-03", "2026-08-31"):
            self.assertAlmostEqual(s[day]["capital_gain_loss"], 0.0, msg=day)
        self.assertEqual(s["2026-07-15"]["cash"], 10000)
        self.assertEqual(s["2026-08-31"]["capital_basis"], 10000)

    def test_same_day_swap_is_internal(self):
        # As in the Fidelity export: sell ELF and buy VENU on the same day.
        store = flat_prices("ELF", "VENU", overrides={("VENU", "2026-08-03"): 110.0})
        s = series_for(
            {"ELF": [tx("e1", "2026-06-01", "buy", 50, 100, "ELF"), tx("e2", "2026-07-01", "sell", 50, 100, "ELF")],
             "VENU": [tx("v1", "2026-07-01", "buy", 50, 100, "VENU")]},
            store, {"VENU": 50})
        self.assertEqual(s["2026-07-01"]["capital_basis"], 5000)  # no new money
        self.assertAlmostEqual(s["2026-07-01"]["capital_gain_loss"], 0.0)
        self.assertAlmostEqual(s["2026-08-31"]["capital_gain_loss"], 500.0)  # VENU +10%

    def test_dividend_counts_as_gain(self):
        store = flat_prices("NVDA")
        s = series_for(
            {"NVDA": [tx("n1", "2026-06-01", "buy", 10, 100, "NVDA"),
                      {"id": "n2", "date": "2026-07-01", "type": "dividend", "shares": 0, "price_per_share": 24.96, "account": "Rollover IRA", "ticker": "NVDA"}]},
            store, {"NVDA": 10})
        self.assertAlmostEqual(s["2026-06-30"]["capital_gain_loss"], 0.0)
        self.assertAlmostEqual(s["2026-07-01"]["capital_gain_loss"], 24.96)

    def test_new_money_is_not_gain(self):
        store = flat_prices("AAA", "BBB")
        s = series_for(
            {"AAA": [tx("a1", "2026-06-01", "buy", 100, 100, "AAA")],
             "BBB": [tx("b1", "2026-07-01", "buy", 200, 100, "BBB")]},
            store, {"AAA": 100, "BBB": 200})
        self.assertEqual(s["2026-07-01"]["capital_basis"], 30000)
        self.assertAlmostEqual(s["2026-07-01"]["capital_gain_loss"], 0.0)

    def test_cash_stays_in_its_account(self):
        # Sell in the TOD account; a buy in an IRA is new money, and the TOD
        # cash stays part of the portfolio value.
        store = flat_prices("AAA", "BBB")
        sell = tx("a2", "2026-07-01", "sell", 100, 100, "AAA"); sell["account"] = "Individual - TOD"
        buy_a = tx("a1", "2026-06-01", "buy", 100, 100, "AAA"); buy_a["account"] = "Individual - TOD"
        buy_b = tx("b1", "2026-07-02", "buy", 100, 100, "BBB"); buy_b["account"] = "Roth IRA"
        s = series_for({"AAA": [buy_a, sell], "BBB": [buy_b]}, store, {"BBB": 100})
        self.assertEqual(s["2026-07-15"]["cash"], 10000)
        self.assertEqual(s["2026-07-15"]["capital_basis"], 20000)
        self.assertAlmostEqual(s["2026-07-15"]["capital_gain_loss"], 0.0)

    def test_money_market_reinvestment_is_cash_not_a_purchase(self):
        # Interest paid into SPAXX and "bought" with itself stays as cash.
        store = flat_prices("AAA")
        div = {"id": "s1", "date": "2026-07-01", "type": "dividend", "shares": 0, "price_per_share": 3.0, "account": "Rollover IRA", "ticker": "SPAXX"}
        reinvest = {"id": "s2", "date": "2026-07-01", "type": "buy", "shares": 3, "price_per_share": 1.0, "account": "Rollover IRA", "ticker": "SPAXX"}
        s = series_for({"AAA": [tx("a1", "2026-06-01", "buy", 10, 100, "AAA")], "SPAXX": [div, reinvest]}, store, {"AAA": 10})
        self.assertAlmostEqual(s["2026-07-15"]["cash"], 3.0)
        self.assertAlmostEqual(s["2026-07-15"]["capital_gain_loss"], 3.0)

    def test_uses_fidelity_amount_when_present(self):
        store = flat_prices("AAA")
        buy = tx("a1", "2026-06-01", "buy", 10, 100, "AAA"); buy["source_amount"] = -1000.5
        s = series_for({"AAA": [buy]}, store, {"AAA": 10})
        self.assertAlmostEqual(s["2026-06-30"]["capital_basis"], 1000.5)

    def test_price_moves_are_gain(self):
        store = flat_prices("AAA", overrides={("AAA", "2026-07-01"): 90.0})
        s = series_for({"AAA": [tx("a1", "2026-06-01", "buy", 100, 100, "AAA")]}, store, {"AAA": 100})
        self.assertAlmostEqual(s["2026-07-01"]["capital_gain_loss"], -1000.0)


if __name__ == "__main__":
    unittest.main()
