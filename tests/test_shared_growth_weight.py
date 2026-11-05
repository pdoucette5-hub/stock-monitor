"""All three scenarios use one growth-view weight (the base scenario's)."""
import unittest

from backend.portfolio_service import _build_summary_row


def scenario(growth, durable, weight):
    return {"rev_growth_rates": [growth] * 3, "net_income_growth_rates": [growth] * 3, "durable_growth_view": durable, "growth_weight_pct": weight}


class SharedGrowthWeightTests(unittest.TestCase):
    def test_expensive_stock_keeps_bear_base_bull_in_order(self):
        # PLTR-like: today's P/E (~116x) is far above the capped table P/E (68x).
        # With separate weights (35/60/100) the bull came out below the bear.
        state = {
            "latest_quarter_revenue": 1.6e9,
            "latest_quarter_net_income": 8.764e8,
            "shares_outstanding": 2.369e9,
            "bear": scenario(70.0, 50.0, 35.0),
            "base": scenario(80.0, 50.0, 60.0),
            "bull": scenario(90.0, 60.0, 100.0),
        }
        row = _build_summary_row("PLTR", {"price": 171.7}, state, {}, 10.0, [])
        self.assertLess(row["bear_cagr_y3"], row["base_cagr_y3"])
        self.assertLess(row["base_cagr_y3"], row["bull_cagr_y3"])
        current_pe = 171.7 / (4 * 8.764e8 / 2.369e9)
        self.assertAlmostEqual(row["blended_future_pe"], 0.6 * 68.0 + 0.4 * current_pe)


if __name__ == "__main__":
    unittest.main()
