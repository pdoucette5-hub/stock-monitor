"""Paths, environment settings, and shared constants for the backend."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# The .env file was previously loaded as a side effect of importing
# backend.data_ingest before these settings were read. Loading it here keeps
# the same values regardless of import order (existing variables win).
load_dotenv(BASE_DIR / ".env")

SCENARIO_STATE_FILE = BASE_DIR / "cache" / "scenario_inputs.json"
GLOBAL_SETTINGS_FILE = BASE_DIR / "cache" / "global_settings.json"
HOLDINGS_OVERRIDES_FILE = BASE_DIR / "cache" / "holdings_overrides.json"
TICKERS_OVERRIDES_FILE = BASE_DIR / "cache" / "tickers_overrides.json"
TRANSACTIONS_FILE = BASE_DIR / "cache" / "transactions.json"
ACCOUNT_ALIASES_FILE = BASE_DIR / "cache" / "account_aliases.json"
MANAGEMENT_MODES_FILE = BASE_DIR / "cache" / "management_modes.json"
MANUAL_ALLOCATIONS_FILE = BASE_DIR / "cache" / "manual_allocations.json"
PORTFOLIO_EVENTS_FILE = BASE_DIR / "cache" / "portfolio_events.json"
ASSUMPTION_SNAPSHOTS_FILE = BASE_DIR / "cache" / "assumption_snapshots.json"
REPORTED_FUNDAMENTALS_FILE = BASE_DIR / "cache" / "reported_fundamentals.json"
EARNINGS_CALENDAR_FILE = BASE_DIR / "cache" / "earnings_calendar.json"
TICKERS_FILE = BASE_DIR / "config" / "tickers.yaml"

PRICE_IMPORT_SECRET = os.getenv("PRICE_IMPORT_SECRET", "")
# A separate key can be used for intraday sampling; without one, the main
# Finnhub key is used (the live service only has FINNHUB_API_KEY set, so
# sampling failed for every ticker except VENU from Oct 13).
INTRADAY_FINNHUB_API_KEY = (
    os.getenv("INTRADAY_FINNHUB_API_KEY", "").strip()
    or os.getenv("FINNHUB_API_KEY", "").strip()
)
GOOGLE_AUTH_ENABLED = os.getenv("GOOGLE_AUTH_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
ALLOWED_USER_EMAILS = {
    email.strip().casefold()
    for email in os.getenv("ALLOWED_USER_EMAILS", "").split(",")
    if email.strip()
}
LIMITED_USER_EMAILS = {
    email.strip().casefold()
    for email in os.getenv("LIMITED_USER_EMAILS", "").split(",")
    if email.strip()
}
AUTHORIZED_USER_EMAILS = ALLOWED_USER_EMAILS | LIMITED_USER_EMAILS
STATE_GCS_BUCKET = os.getenv(
    "STOCK_MONITOR_STATE_GCS_BUCKET",
    os.getenv("PRICE_HISTORY_GCS_BUCKET", ""),
)
STATE_GCS_PREFIX = os.getenv("STOCK_MONITOR_STATE_GCS_PREFIX", "stock-monitor/state")
STATE_GCS_BUNDLE_BLOB = (
    f"{STATE_GCS_PREFIX.rstrip('/')}/state_bundle.json"
)

SCENARIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
GLOBAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
HOLDINGS_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
TICKERS_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
TRANSACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
ACCOUNT_ALIASES_FILE.parent.mkdir(parents=True, exist_ok=True)
MANAGEMENT_MODES_FILE.parent.mkdir(parents=True, exist_ok=True)
MANUAL_ALLOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
PORTFOLIO_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
ASSUMPTION_SNAPSHOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
REPORTED_FUNDAMENTALS_FILE.parent.mkdir(parents=True, exist_ok=True)
EARNINGS_CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)

RESPONSE_CACHE_TTL_SECONDS = int(os.getenv("STOCK_MONITOR_RESPONSE_CACHE_TTL_SECONDS", "600"))

VALID_TRANSACTION_TYPES = {
    "buy",
    "sell",
    "dividend",
    "split",
    "transfer_in",
    "transfer_out",
    "adjustment",
}

VALID_MANAGEMENT_MODES = {"managed", "track", "excluded"}
UNASSIGNED_ACCOUNT = "Unassigned"
MATTHEW_COMPENSATION_ACCOUNTS = {
    "Paul ROTH IRA",
    "Julianne ROTH IRA",
    "Paul Rollover IRA",
    "Julianne Rollover IRA",
}
# Money market funds that hold an account's uninvested cash. Moving money in
# or out of them is not a trade; their dividends are interest on the cash.
# (Not "USD": that is ProShares Ultra Semiconductors, a real holding.)
CASH_TICKERS = {"SPAXX", "FDRXX", "FZFXX", "SPRXX", "FCASH", "CORE"}

MATTHEW_COMPENSATION_ACCOUNT_KEYS = {
    account.casefold()
    for account in MATTHEW_COMPENSATION_ACCOUNTS
}

SCENARIO_CHANGE_LABELS = {
    "latest_quarter_revenue": "Latest quarter revenue",
    "latest_quarter_net_income": "Latest quarter net income",
    "shares_outstanding": "Shares outstanding",
    "actuals_source_preference": "Actuals source",
    "notes": "Notes",
    "bear.rev_growth_rates.0": "Bear revenue growth Y1",
    "bear.rev_growth_rates.1": "Bear revenue growth Y2",
    "bear.rev_growth_rates.2": "Bear revenue growth Y3",
    "bear.net_income_growth_rates.0": "Bear net income growth Y1",
    "bear.net_income_growth_rates.1": "Bear net income growth Y2",
    "bear.net_income_growth_rates.2": "Bear net income growth Y3",
    "bear.durable_growth_view": "Bear durable growth",
    "bear.growth_weight_pct": "Bear growth weight",
    "base.rev_growth_rates.0": "Base revenue growth Y1",
    "base.rev_growth_rates.1": "Base revenue growth Y2",
    "base.rev_growth_rates.2": "Base revenue growth Y3",
    "base.net_income_growth_rates.0": "Base net income growth Y1",
    "base.net_income_growth_rates.1": "Base net income growth Y2",
    "base.net_income_growth_rates.2": "Base net income growth Y3",
    "base.durable_growth_view": "Base durable growth",
    "base.growth_weight_pct": "Base growth weight",
    "bull.rev_growth_rates.0": "Bull revenue growth Y1",
    "bull.rev_growth_rates.1": "Bull revenue growth Y2",
    "bull.rev_growth_rates.2": "Bull revenue growth Y3",
    "bull.net_income_growth_rates.0": "Bull net income growth Y1",
    "bull.net_income_growth_rates.1": "Bull net income growth Y2",
    "bull.net_income_growth_rates.2": "Bull net income growth Y3",
    "bull.durable_growth_view": "Bull durable growth",
    "bull.growth_weight_pct": "Bull growth weight",
}

CONTROL_CHANGE_LABELS = {
    "show_in_holdings": "Show in holdings",
    "include_in_redistribution": "Include in redistribution",
    "eligible_redistribution_shares": "Eligible redistribution shares",
}
