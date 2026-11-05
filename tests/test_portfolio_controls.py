import unittest

from backend.portfolio_service import apply_portfolio_controls, prepare_ticker_state


class PortfolioControlsTests(unittest.TestCase):
    def test_hides_held_ticker_without_saved_assumptions(self):
        # SPAXX-style cash fund: held, shown in holdings, but never had
        # assumptions saved. Hiding it used to be dropped silently.
        result = apply_portfolio_controls(
            {"NVDA": {"display_rules": {"show_in_holdings": True}}},
            [{"ticker": "spaxx", "show_in_holdings": False}],
            {"SPAXX": 1000.0, "NVDA": 10.0},
        )
        self.assertFalse(result["SPAXX"]["display_rules"]["show_in_holdings"])
        state = prepare_ticker_state(result["SPAXX"], {"ticker": "SPAXX"})
        self.assertFalse(state["display_rules"]["show_in_holdings"])
        self.assertIn("base", state)

    def test_ignores_unknown_ticker(self):
        result = apply_portfolio_controls(
            {},
            [{"ticker": "ZZZZ", "show_in_holdings": False}],
            {"SPAXX": 1000.0},
        )
        self.assertNotIn("ZZZZ", result)


if __name__ == "__main__":
    unittest.main()
