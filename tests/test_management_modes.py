"""Only the four managed accounts default to 'managed'."""
import unittest

from backend.management_service import automatic_account_mode, get_management_mode


class ModeTests(unittest.TestCase):
    def test_defaults(self):
        for account in ("Paul ROTH IRA", "Julianne ROTH IRA", "Paul Rollover IRA", "julianne rollover ira"):
            self.assertEqual(automatic_account_mode(account), "managed", account)
        for account in ("Individual - TOD", "REHRIG PACIFIC", "Unassigned", ""):
            self.assertEqual(automatic_account_mode(account), "track", account)

    def test_overrides_and_account_defaults_still_win(self):
        settings = {"account_defaults": {"Individual - TOD": "excluded"}, "position_overrides": {"paul roth ira|AAA": "track"}}
        self.assertEqual(get_management_mode(settings, "Individual - TOD", "NEWFX"), "excluded")
        self.assertEqual(get_management_mode(settings, "Paul ROTH IRA", "AAA"), "track")
        self.assertEqual(get_management_mode(settings, "Paul ROTH IRA", "BBB"), "managed")


if __name__ == "__main__":
    unittest.main()
