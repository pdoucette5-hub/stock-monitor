from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from backend.logic import merge_global_settings
from backend.logic import safe_float
from backend.models import (
    GlobalSettings,
    PortfolioConfig,
    PortfolioControlsUpdate,
    PortfolioViewResponse,
    ScenarioInputsResponse,
    StockScenarioResponse,
    TickerScenarioInputs,
)
from backend.performance_service import build_portfolio_performance
from backend.portfolio_service import (
    apply_portfolio_controls,
    build_portfolio_views,
    normalize_portfolio,
)
from backend.price_store import (
    get_price_comparison_response,
    get_price_history_response,
    get_price_history_status,
    get_price_points_for_ticker,
    upsert_price_rows,
)
from backend.sheets_service import push_tickers_to_sheet
from backend.transactions_service import compute_position_summary

BASE_DIR = Path(__file__).resolve().parent.parent

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
_response_cache: dict[str, tuple[float, Any]] = {}
_response_cache_lock = threading.Lock()

app = FastAPI(title="Stock Monitor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/api/prices/import",
    "/api/accounts/merge",
}

FULL_ACCESS_ONLY_EXACT_PATHS = {
    "/api/accounts",
    "/api/management",
    "/api/management/allocation",
    "/api/compensation",
    "/api/performance/portfolio",
    "/api/portfolio/controls",
    "/api/sheets/tickers/sync",
    "/api/tickers/archive",
    "/api/tickers/restore",
    "/api/tickers/watchlist",
}

FULL_ACCESS_ONLY_PREFIXES = (
    "/api/position/",
    "/api/transactions",
)

FULL_ACCESS_ONLY_WRITE_EXACT_PATHS = {
    "/api/portfolio",
}

FULL_ACCESS_ONLY_WRITE_PREFIXES = (
    "/api/stock/",
)


@app.middleware("http")
async def require_google_auth(request: Request, call_next):
    if (
        not GOOGLE_AUTH_ENABLED
        or request.method == "OPTIONS"
        or not request.url.path.startswith("/api")
        or request.url.path in PUBLIC_API_PATHS
    ):
        return await call_next(request)

    if not GOOGLE_CLIENT_ID or not AUTHORIZED_USER_EMAILS:
        return JSONResponse(
            status_code=503,
            content={"detail": "Google authentication is not fully configured"},
        )

    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return JSONResponse(status_code=401, content={"detail": "Sign in required"})

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError:
        return JSONResponse(status_code=401, content={"detail": "Invalid sign-in token"})

    email = str(claims.get("email") or "").strip().casefold()
    if not email or email not in AUTHORIZED_USER_EMAILS:
        return JSONResponse(status_code=403, content={"detail": "Email is not allowed"})

    role = "full" if email in ALLOWED_USER_EMAILS else "limited"
    request.state.user = {
        "email": email,
        "name": claims.get("name") or "",
        "picture": claims.get("picture") or "",
        "role": role,
    }

    write_method = request.method.upper() not in {"GET", "HEAD"}
    if role != "full" and (
        request.url.path in FULL_ACCESS_ONLY_EXACT_PATHS
        or any(request.url.path.startswith(prefix) for prefix in FULL_ACCESS_ONLY_PREFIXES)
        or (
            write_method
            and (
                request.url.path in FULL_ACCESS_ONLY_WRITE_EXACT_PATHS
                or any(
                    request.url.path.startswith(prefix)
                    for prefix in FULL_ACCESS_ONLY_WRITE_PREFIXES
                )
            )
        )
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Private portfolio data is not available for this account"},
        )

    return await call_next(request)


class PortfolioSharesUpdate(BaseModel):
    ticker: str
    shares: float


class PortfolioTickerUpsert(BaseModel):
    ticker: str
    shares: float


class WatchlistTickerUpsert(BaseModel):
    ticker: str


class ArchiveTickerRequest(BaseModel):
    ticker: str


class RestoreTickerRequest(BaseModel):
    ticker: str
    list: str


class TransactionCreate(BaseModel):
    date: str
    type: str
    shares: float
    price_per_share: float | None = None
    fees: float | None = 0.0
    account: str | None = ""
    notes: str | None = ""


class TransactionUpdate(BaseModel):
    date: str
    type: str
    shares: float
    price_per_share: float | None = None
    fees: float | None = 0.0
    account: str | None = ""
    notes: str | None = ""


class ImportedPriceRow(BaseModel):
    ticker: str
    date: str
    close: float
    source: str | None = "googlefinance"
    fetched_at: str | None = None


class PriceImportRequest(BaseModel):
    rows: list[ImportedPriceRow]


class TransactionImportFile(BaseModel):
    name: str
    content: str


class TransactionImportRequest(BaseModel):
    files: list[TransactionImportFile]
    account_mappings: dict[str, str] = Field(default_factory=dict)
    commit: bool = False


class ManagementModeUpdate(BaseModel):
    account: str
    ticker: str | None = None
    mode: str


class ManagementModesUpdate(BaseModel):
    updates: list[ManagementModeUpdate]


class ManualAllocationUpdate(BaseModel):
    ticker: str
    account: str
    shares: float
    mode: str = "track"


class AccountMergeRequest(BaseModel):
    from_account: str
    to_account: str
    source_account_keys: list[str] = Field(default_factory=list)


class FundamentalsRefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)


class EarningsCalendarRefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    days_ahead: int = 180


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


def load_json_file(path: Path, default_data: Any) -> Any:
    if STATE_GCS_BUCKET:
        try:
            from google.cloud import storage

            client = storage.Client()
            blob = client.bucket(STATE_GCS_BUCKET).blob(
                f"{STATE_GCS_PREFIX.rstrip('/')}/{path.name}",
            )
            if blob.exists():
                data = json.loads(blob.download_as_text(encoding="utf-8"))
                return data if isinstance(data, type(default_data)) else default_data
        except Exception:
            pass

    if not path.exists():
        return default_data
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default_data)) else default_data
    except (json.JSONDecodeError, OSError):
        return default_data


def save_json_file(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    invalidate_response_cache()

    if not STATE_GCS_BUCKET:
        return

    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(STATE_GCS_BUCKET).blob(
        f"{STATE_GCS_PREFIX.rstrip('/')}/{path.name}",
    )
    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json",
    )


def invalidate_response_cache() -> None:
    with _response_cache_lock:
        _response_cache.clear()


def get_cached_response(key: str) -> Any | None:
    with _response_cache_lock:
        cached = _response_cache.get(key)
        if not cached:
            return None
        cached_at, payload = cached
        if time.monotonic() - cached_at > RESPONSE_CACHE_TTL_SECONDS:
            _response_cache.pop(key, None)
            return None
        return payload


def set_cached_response(key: str, payload: Any) -> Any:
    with _response_cache_lock:
        _response_cache[key] = (time.monotonic(), payload)
    return payload


def load_scenario_inputs() -> dict[str, Any]:
    return load_json_file(SCENARIO_STATE_FILE, {})


def save_scenario_inputs(data: dict[str, Any]) -> None:
    save_json_file(SCENARIO_STATE_FILE, data)


def load_settings_dict() -> dict[str, Any]:
    saved = load_json_file(GLOBAL_SETTINGS_FILE, {})
    return merge_global_settings(saved)


def save_settings_dict(data: dict[str, Any]) -> None:
    save_json_file(GLOBAL_SETTINGS_FILE, data)


def load_holdings_overrides() -> dict[str, float]:
    raw = load_json_file(HOLDINGS_OVERRIDES_FILE, {})
    cleaned: dict[str, float] = {}
    if not isinstance(raw, dict):
        return cleaned

    for ticker, shares in raw.items():
        try:
            cleaned[str(ticker).strip().upper()] = float(shares)
        except (TypeError, ValueError):
            continue
    return cleaned


def save_holdings_overrides(data: dict[str, float]) -> None:
    cleaned = {str(k).strip().upper(): float(v) for k, v in data.items()}
    save_json_file(HOLDINGS_OVERRIDES_FILE, cleaned)


def load_tickers_overrides() -> dict[str, Any]:
    raw = load_json_file(TICKERS_OVERRIDES_FILE, {"tickers": {}})
    if not isinstance(raw, dict):
        return {"tickers": {}}

    tickers = raw.get("tickers", {})
    if not isinstance(tickers, dict):
        tickers = {}

    cleaned: dict[str, Any] = {}
    for ticker, payload in tickers.items():
        normalized = str(ticker).strip().upper()
        if not normalized or not isinstance(payload, dict):
            continue

        list_value = payload.get("list")
        if list_value not in {"portfolio", "watchlist"}:
            list_value = None

        shares = payload.get("shares")
        try:
            shares = None if shares is None else float(shares)
        except (TypeError, ValueError):
            shares = None

        cleaned[normalized] = {
            "list": list_value,
            "shares": shares,
            "archived": bool(payload.get("archived", False)),
            "removed": bool(payload.get("removed", False)),
        }

    return {"tickers": cleaned}


def save_tickers_overrides(data: dict[str, Any]) -> None:
    tickers = data.get("tickers", {}) if isinstance(data, dict) else {}
    cleaned: dict[str, Any] = {}

    if isinstance(tickers, dict):
        for ticker, payload in tickers.items():
            normalized = str(ticker).strip().upper()
            if not normalized or not isinstance(payload, dict):
                continue

            list_value = payload.get("list")
            if list_value not in {"portfolio", "watchlist"}:
                list_value = None

            shares = payload.get("shares")
            try:
                shares = None if shares is None else float(shares)
            except (TypeError, ValueError):
                shares = None

            cleaned[normalized] = {
                "list": list_value,
                "shares": shares,
                "archived": bool(payload.get("archived", False)),
                "removed": bool(payload.get("removed", False)),
            }

    save_json_file(TICKERS_OVERRIDES_FILE, {"tickers": cleaned})


def load_transactions() -> dict[str, list[dict[str, Any]]]:
    raw = load_json_file(TRANSACTIONS_FILE, {})
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in raw.items():
        normalized = normalize_ticker(ticker)
        if not normalized or not isinstance(entries, list):
            continue
        cleaned[normalized] = [entry for entry in entries if isinstance(entry, dict)]
    return cleaned


def save_transactions(data: dict[str, list[dict[str, Any]]]) -> None:
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in data.items():
        normalized = normalize_ticker(ticker)
        if not normalized or not isinstance(entries, list):
            continue
        cleaned[normalized] = [entry for entry in entries if isinstance(entry, dict)]
    save_json_file(TRANSACTIONS_FILE, cleaned)


def build_position_summaries() -> dict[str, dict[str, Any]]:
    return {
        ticker: compute_position_summary(entries)
        for ticker, entries in load_transactions().items()
    }


def load_account_aliases() -> dict[str, str]:
    raw = load_json_file(ACCOUNT_ALIASES_FILE, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


def save_account_aliases(data: dict[str, str]) -> None:
    cleaned = {
        str(key).strip(): str(value).strip()
        for key, value in data.items()
        if str(key).strip() and str(value).strip()
    }
    save_json_file(ACCOUNT_ALIASES_FILE, cleaned)


def load_management_modes() -> dict[str, Any]:
    raw = load_json_file(
        MANAGEMENT_MODES_FILE,
        {"account_defaults": {}, "position_overrides": {}},
    )
    if not isinstance(raw, dict):
        raw = {}

    account_defaults = raw.get("account_defaults", {})
    position_overrides = raw.get("position_overrides", {})

    return {
        "account_defaults": {
            str(account).strip(): str(mode).strip().lower()
            for account, mode in account_defaults.items()
            if str(account).strip() and str(mode).strip().lower() in VALID_MANAGEMENT_MODES
        }
        if isinstance(account_defaults, dict)
        else {},
        "position_overrides": {
            str(key).strip(): str(mode).strip().lower()
            for key, mode in position_overrides.items()
            if str(key).strip() and str(mode).strip().lower() in VALID_MANAGEMENT_MODES
        }
        if isinstance(position_overrides, dict)
        else {},
    }


def save_management_modes(data: dict[str, Any]) -> None:
    save_json_file(MANAGEMENT_MODES_FILE, data)


def load_manual_allocations() -> list[dict[str, Any]]:
    raw = load_json_file(MANUAL_ALLOCATIONS_FILE, [])
    if not isinstance(raw, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ticker = normalize_ticker(row.get("ticker", ""))
        account = str(row.get("account") or "").strip()
        try:
            shares = float(row.get("shares") or 0.0)
        except (TypeError, ValueError):
            continue
        if ticker and account and account != UNASSIGNED_ACCOUNT and shares > 0:
            cleaned.append({"ticker": ticker, "account": account, "shares": shares})
    return cleaned


def save_manual_allocations(rows: list[dict[str, Any]]) -> None:
    save_json_file(MANUAL_ALLOCATIONS_FILE, rows)


def account_matches(
    value: str | None,
    from_account: str,
    source_account_key: str | None = None,
    source_account_keys: set[str] | None = None,
) -> bool:
    account = str(value or "").strip()
    if account.casefold() == from_account.casefold():
        return True

    key = str(source_account_key or "").strip()
    return bool(key and source_account_keys and key in source_account_keys)


def merge_account_records(
    from_account: str,
    to_account: str,
    source_account_keys: list[str] | None = None,
) -> dict[str, Any]:
    from_account = str(from_account or "").strip()
    to_account = str(to_account or "").strip()
    source_keys = {
        str(key).strip()
        for key in (source_account_keys or [])
        if str(key).strip()
    }

    if not from_account or not to_account:
        raise ValueError("Both source and target accounts are required")
    if from_account.casefold() == to_account.casefold():
        raise ValueError("Source and target accounts must be different")

    aliases = load_account_aliases()
    aliases[from_account.casefold()] = to_account
    for key in source_keys:
        aliases[key] = to_account
    save_account_aliases(aliases)

    transactions = load_transactions()
    transaction_updates = 0
    source_key_updates = 0
    for entries in transactions.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_key = str(entry.get("source_account_key") or "").strip()
            if account_matches(
                entry.get("account"),
                from_account,
                source_key,
                source_keys,
            ):
                if entry.get("account") != to_account:
                    entry["account"] = to_account
                    transaction_updates += 1
                if source_key in source_keys:
                    source_key_updates += 1
    if transaction_updates:
        save_transactions(transactions)

    allocations = load_manual_allocations()
    allocation_updates = 0
    for allocation in allocations:
        if account_matches(allocation.get("account"), from_account):
            allocation["account"] = to_account
            allocation_updates += 1
    if allocation_updates:
        save_manual_allocations(allocations)

    settings = load_management_modes()
    account_defaults = settings.get("account_defaults", {})
    if from_account in account_defaults:
        account_defaults.setdefault(to_account, account_defaults[from_account])
        account_defaults.pop(from_account, None)

    position_overrides = settings.get("position_overrides", {})
    updated_overrides: dict[str, str] = {}
    mode_updates = 0
    for key, mode in position_overrides.items():
        account_part, sep, ticker_part = str(key).partition("|")
        if sep and account_part.casefold() == from_account.casefold():
            new_key = management_position_key(to_account, ticker_part)
            updated_overrides.setdefault(new_key, mode)
            mode_updates += 1
        else:
            updated_overrides[key] = mode
    settings["account_defaults"] = account_defaults
    settings["position_overrides"] = updated_overrides
    if mode_updates or allocation_updates or transaction_updates:
        save_management_modes(settings)

    return {
        "from_account": from_account,
        "to_account": to_account,
        "source_account_keys": sorted(source_keys),
        "transactions_updated": transaction_updates,
        "source_key_matches": source_key_updates,
        "manual_allocations_updated": allocation_updates,
        "management_settings_updated": mode_updates,
    }


def apply_account_aliases_to_transactions(
    transactions: dict[str, list[dict[str, Any]]],
    aliases: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    updates = 0
    for entries in transactions.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            source_key = str(entry.get("source_account_key") or "").strip()
            account_name = str(entry.get("account") or "").strip()
            source_name = str(entry.get("source_account") or "").strip()
            alias = (
                aliases.get(source_key)
                or aliases.get(account_name.casefold())
                or aliases.get(source_name.casefold())
            )
            if alias and entry.get("account") != alias:
                entry["account"] = alias
                updates += 1

    return transactions, updates


def management_position_key(account: str, ticker: str) -> str:
    return f"{str(account).strip().casefold()}|{normalize_ticker(ticker)}"


def automatic_account_mode(account: str) -> str:
    return "track" if str(account).strip() == UNASSIGNED_ACCOUNT else "managed"


def get_management_mode(
    settings: dict[str, Any],
    account: str,
    ticker: str,
) -> str:
    override = settings["position_overrides"].get(
        management_position_key(account, ticker),
    )
    if override in VALID_MANAGEMENT_MODES:
        return override
    return settings["account_defaults"].get(account, automatic_account_mode(account))


def get_management_mode_source(
    settings: dict[str, Any],
    account: str,
    ticker: str,
) -> str:
    if management_position_key(account, ticker) in settings["position_overrides"]:
        return "ticker_override"
    if account in settings["account_defaults"]:
        return "account_default"
    return "automatic"


def build_management_snapshot() -> dict[str, Any]:
    transactions = load_transactions()
    settings = load_management_modes()
    manual_allocations = load_manual_allocations()
    config = get_effective_tickers_config()
    configured_shares = {
        row["ticker"]: float(row.get("shares") or 0.0)
        for row in normalize_portfolio(config.get("portfolio", []))
    }

    positions: list[dict[str, Any]] = []
    transaction_shares_by_ticker: dict[str, float] = {}
    transaction_backed_tickers = {
        ticker
        for ticker, entries in transactions.items()
        if isinstance(entries, list) and entries
    }
    accounts: set[str] = set()

    for ticker, entries in transactions.items():
        entries_by_account: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            account = str(entry.get("account") or "").strip() or UNASSIGNED_ACCOUNT
            entries_by_account.setdefault(account, []).append(entry)
            accounts.add(account)

        for account, account_entries in entries_by_account.items():
            summary = compute_position_summary(account_entries)
            shares = max(float(summary.get("current_shares") or 0.0), 0.0)
            if shares <= 0:
                continue
            transaction_shares_by_ticker[ticker] = (
                transaction_shares_by_ticker.get(ticker, 0.0) + shares
            )
            positions.append(
                {
                    "account": account,
                    "ticker": ticker,
                    "shares": shares,
                    "mode": get_management_mode(settings, account, ticker),
                    "mode_source": get_management_mode_source(settings, account, ticker),
                    "source": "transactions",
                },
            )

    allocations_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for allocation in manual_allocations:
        allocations_by_ticker.setdefault(allocation["ticker"], []).append(allocation)

    accounts.add(UNASSIGNED_ACCOUNT)
    for ticker, total_shares in configured_shares.items():
        if ticker in transaction_backed_tickers:
            continue
        residual = max(
            total_shares - transaction_shares_by_ticker.get(ticker, 0.0),
            0.0,
        )
        remaining = residual
        for allocation in allocations_by_ticker.get(ticker, []):
            allocated_shares = min(float(allocation["shares"]), remaining)
            if allocated_shares <= 0:
                continue
            account = allocation["account"]
            accounts.add(account)
            positions.append(
                {
                    "account": account,
                    "ticker": ticker,
                    "shares": allocated_shares,
                    "mode": get_management_mode(settings, account, ticker),
                    "mode_source": get_management_mode_source(settings, account, ticker),
                    "source": "manual_allocation",
                },
            )
            remaining -= allocated_shares

        if remaining > 0:
            positions.append(
                {
                    "account": UNASSIGNED_ACCOUNT,
                    "ticker": ticker,
                    "shares": remaining,
                    "mode": get_management_mode(settings, UNASSIGNED_ACCOUNT, ticker),
                    "mode_source": get_management_mode_source(
                        settings,
                        UNASSIGNED_ACCOUNT,
                        ticker,
                    ),
                    "source": "configured_residual",
                },
            )

    shares_by_mode: dict[str, dict[str, float]] = {}
    for position in positions:
        ticker = position["ticker"]
        mode = position["mode"]
        bucket = shares_by_mode.setdefault(
            ticker,
            {"managed": 0.0, "track": 0.0, "excluded": 0.0},
        )
        bucket[mode] += float(position["shares"])

    account_rows = [
        {
            "account": account,
            "default_mode": settings["account_defaults"].get(
                account,
                automatic_account_mode(account),
            ),
            "default_source": (
                "saved" if account in settings["account_defaults"] else "automatic"
            ),
            "position_count": sum(1 for row in positions if row["account"] == account),
        }
        for account in sorted(accounts, key=str.casefold)
    ]

    return {
        "accounts": account_rows,
        "positions": sorted(
            positions,
            key=lambda row: (str(row["account"]).casefold(), row["ticker"]),
        ),
        "shares_by_mode": shares_by_mode,
        "manual_allocations": manual_allocations,
    }


def filter_transactions_by_management_mode(
    transactions: dict[str, list[dict[str, Any]]],
    mode: str,
) -> dict[str, list[dict[str, Any]]]:
    if mode not in VALID_MANAGEMENT_MODES:
        return transactions

    settings = load_management_modes()
    filtered: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        rows = []
        for entry in entries:
            account = str(entry.get("account") or "").strip() or UNASSIGNED_ACCOUNT
            if get_management_mode(settings, account, ticker) == mode:
                rows.append(entry)
        if rows:
            filtered[ticker] = rows
    return filtered


def apply_management_shares_to_config(
    config: dict[str, Any],
    shares_by_mode: dict[str, dict[str, float]],
    mode: str,
) -> dict[str, Any]:
    if mode not in VALID_MANAGEMENT_MODES:
        return config

    portfolio = []
    for item in normalize_portfolio(config.get("portfolio", [])):
        ticker = item["ticker"]
        shares = float(shares_by_mode.get(ticker, {}).get(mode, 0.0))
        if shares > 0:
            portfolio.append({"ticker": ticker, "shares": shares})

    return {
        "portfolio": portfolio,
        "watchlist": config.get("watchlist", []) or [],
    }


def load_portfolio_events() -> list[dict[str, Any]]:
    raw = load_json_file(PORTFOLIO_EVENTS_FILE, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_portfolio_events(events: list[dict[str, Any]]) -> None:
    cleaned = [item for item in events if isinstance(item, dict)][-5000:]
    save_json_file(PORTFOLIO_EVENTS_FILE, cleaned)


def append_portfolio_event(
    event_type: str,
    ticker: str,
    payload: dict[str, Any] | None = None,
) -> None:
    events = load_portfolio_events()
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": str(event_type).strip(),
        "ticker": normalize_ticker(ticker),
        "payload": payload or {},
    }
    events.append(entry)
    save_portfolio_events(events)


def load_assumption_snapshots() -> list[dict[str, Any]]:
    raw = load_json_file(ASSUMPTION_SNAPSHOTS_FILE, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_assumption_snapshots(snapshots: list[dict[str, Any]]) -> None:
    cleaned = [item for item in snapshots if isinstance(item, dict)][-10000:]
    save_json_file(ASSUMPTION_SNAPSHOTS_FILE, cleaned)


def load_reported_fundamentals() -> dict[str, Any]:
    return load_json_file(REPORTED_FUNDAMENTALS_FILE, {})


def save_reported_fundamentals(data: dict[str, Any]) -> None:
    cleaned = {
        normalize_ticker(ticker): payload
        for ticker, payload in data.items()
        if normalize_ticker(ticker) and isinstance(payload, dict)
    }
    save_json_file(REPORTED_FUNDAMENTALS_FILE, dict(sorted(cleaned.items())))


def load_earnings_calendar() -> dict[str, Any]:
    return load_json_file(EARNINGS_CALENDAR_FILE, {})


def save_earnings_calendar(data: dict[str, Any]) -> None:
    cleaned = {
        normalize_ticker(ticker): payload
        for ticker, payload in data.items()
        if normalize_ticker(ticker) and isinstance(payload, dict)
    }
    save_json_file(EARNINGS_CALENDAR_FILE, dict(sorted(cleaned.items())))


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_ANALYST_EARNINGS_URL = "https://api.nasdaq.com/api/analyst/{ticker}/earnings-forecast"
STOCK_ANALYSIS_FORECAST_URL = "https://stockanalysis.com/stocks/{ticker}/forecast/"
SEC_HEADERS = {
    "User-Agent": "stock-monitor/1.0 pdoucette5@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
REVENUE_FACT_CANDIDATES = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "SalesRevenueServicesNet",
)
NET_INCOME_FACT_CANDIDATES = (
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)
SHARES_FACT_CANDIDATES = (
    "EntityCommonStockSharesOutstanding",
    "CommonStocksIncludingAdditionalPaidInCapitalMember",
)
_sec_ticker_map_cache: dict[str, str] | None = None


def _sec_get_json(url: str) -> Any:
    response = requests.get(url, headers=SEC_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def load_sec_ticker_map() -> dict[str, str]:
    global _sec_ticker_map_cache
    if _sec_ticker_map_cache is not None:
        return _sec_ticker_map_cache

    payload = _sec_get_json(SEC_TICKER_MAP_URL)
    mapping: dict[str, str] = {}
    if isinstance(payload, dict):
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            ticker = normalize_ticker(item.get("ticker", ""))
            cik = item.get("cik_str")
            if ticker and cik is not None:
                mapping[ticker] = str(cik).zfill(10)
    _sec_ticker_map_cache = mapping
    return mapping


def _parse_compact_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return safe_float(value)
    text = str(value).strip().strip('"').strip("'")
    if not text or text == "[PRO]" or text in {"null", "void 0", "undefined"}:
        return None
    multiplier = 1.0
    suffix = text[-1:].upper()
    if suffix in {"K", "M", "B", "T"}:
        multiplier = {
            "K": 1_000.0,
            "M": 1_000_000.0,
            "B": 1_000_000_000.0,
            "T": 1_000_000_000_000.0,
        }[suffix]
        text = text[:-1]
    text = text.replace("$", "").replace(",", "").replace("%", "")
    parsed = safe_float(text)
    return parsed * multiplier if parsed is not None else None


def _first_regex_float(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return _parse_compact_number(match.group(1))


def _stockanalysis_metric(page: str, key: str) -> dict[str, float | None]:
    pattern = (
        rf"{re.escape(key)}:\{{"
        r"last:([^,}]+),"
        r"this:([^,}]+),"
        r"growth:([^,}]+)"
    )
    match = re.search(pattern, page)
    if not match:
        return {"last": None, "this": None, "growth": None}
    return {
        "last": _parse_compact_number(match.group(1)),
        "this": _parse_compact_number(match.group(2)),
        "growth": _parse_compact_number(match.group(3)),
    }


def _timestamp_ms_to_iso(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _nasdaq_yearly_eps_rows(ticker: str) -> list[dict[str, Any]]:
    response = requests.get(
        NASDAQ_ANALYST_EARNINGS_URL.format(ticker=ticker),
        headers=NASDAQ_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    forecast = data.get("yearlyForecast") if isinstance(data, dict) else None
    rows = forecast.get("rows") if isinstance(forecast, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def fetch_online_projection(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise ValueError("Ticker is required")

    stockanalysis_url = STOCK_ANALYSIS_FORECAST_URL.format(ticker=normalized.lower())
    response = requests.get(
        stockanalysis_url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
        timeout=20,
    )
    response.raise_for_status()
    page = response.text

    revenue_this = _stockanalysis_metric(page, "revenueThis")
    revenue_next = _stockanalysis_metric(page, "revenueNext")
    eps_this = _stockanalysis_metric(page, "epsThis")
    eps_next = _stockanalysis_metric(page, "epsNext")
    analyst_count = _first_regex_float(page, r"analysts:\[null,null,null,null,null,([^,\]]+)")
    last_updated = _timestamp_ms_to_iso(_first_regex_float(page, r"lastUpdated:(\d+)"))
    estimates_source = "stockanalysis"
    source_match = re.search(r"estimatesSource:\"([^\"]+)\"", page)
    if source_match:
        estimates_source = f"stockanalysis-{source_match.group(1)}"

    earnings_growth = [eps_this.get("growth"), eps_next.get("growth"), None]
    eps_estimates = [eps_this.get("this"), eps_next.get("this"), None]
    eps_estimate_counts = [analyst_count, None, None]

    try:
        nasdaq_rows = _nasdaq_yearly_eps_rows(normalized)
    except Exception:
        nasdaq_rows = []
    previous_eps = eps_this.get("last")
    for idx, row in enumerate(nasdaq_rows[:3]):
        consensus_eps = _parse_compact_number(row.get("consensusEPSForecast"))
        if consensus_eps is None:
            continue
        if idx < len(eps_estimates):
            eps_estimates[idx] = eps_estimates[idx] if eps_estimates[idx] is not None else consensus_eps
            eps_estimate_counts[idx] = _parse_compact_number(row.get("noOfEstimates"))
        if idx < len(earnings_growth) and earnings_growth[idx] is None and previous_eps:
            earnings_growth[idx] = ((consensus_eps / previous_eps) - 1.0) * 100.0
        previous_eps = consensus_eps

    return {
        "source": estimates_source,
        "source_url": stockanalysis_url,
        "secondary_sources": [
            {
                "source": "nasdaq-analyst-earnings-forecast",
                "source_url": NASDAQ_ANALYST_EARNINGS_URL.format(ticker=normalized),
            },
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": last_updated,
        "revenue_growth": [revenue_this.get("growth"), revenue_next.get("growth"), None],
        "earnings_growth": earnings_growth,
        "revenue_estimates": [revenue_this.get("this"), revenue_next.get("this"), None],
        "eps_estimates": eps_estimates,
        "revenue_estimate_counts": [analyst_count, None, None],
        "eps_estimate_counts": eps_estimate_counts,
    }


def _duration_days(fact: dict[str, Any]) -> int | None:
    start = fact.get("start")
    end = fact.get("end")
    if not start or not end:
        return None
    try:
        start_date = datetime.fromisoformat(str(start)).date()
        end_date = datetime.fromisoformat(str(end)).date()
    except ValueError:
        return None
    return (end_date - start_date).days


def _fact_units(company_facts: dict[str, Any], concept: str, unit: str) -> list[dict[str, Any]]:
    facts = company_facts.get("facts")
    if not isinstance(facts, dict):
        return []
    matches: list[dict[str, Any]] = []
    for namespace, namespace_facts in facts.items():
        if not isinstance(namespace_facts, dict):
            continue
        concept_payload = namespace_facts.get(concept)
        if not isinstance(concept_payload, dict):
            continue
        units = concept_payload.get("units")
        if not isinstance(units, dict):
            continue
        rows = units.get(unit)
        if isinstance(rows, list):
            matches.extend({**row, "namespace": namespace} for row in rows if isinstance(row, dict))
    return matches


def _latest_quarterly_fact(
    company_facts: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for concept in concepts:
        for fact in _fact_units(company_facts, concept, unit):
            if safe_float(fact.get("val")) is None:
                continue
            form = str(fact.get("form") or "")
            if form not in {"10-Q", "10-K"}:
                continue
            duration = _duration_days(fact)
            if duration is None or duration > 130:
                continue
            candidates.append({**fact, "concept": concept, "duration_days": duration})

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            str(item.get("end") or ""),
            str(item.get("filed") or ""),
            str(item.get("concept") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def _latest_instant_fact(
    company_facts: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for concept in concepts:
        for fact in _fact_units(company_facts, concept, unit):
            if safe_float(fact.get("val")) is None:
                continue
            form = str(fact.get("form") or "")
            if form not in {"10-Q", "10-K"}:
                continue
            candidates.append({**fact, "concept": concept})

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            str(item.get("end") or ""),
            str(item.get("filed") or ""),
            str(item.get("concept") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def fetch_sec_reported_fundamentals(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise ValueError("Ticker is required")

    cik = load_sec_ticker_map().get(normalized)
    if not cik:
        raise ValueError(f"No SEC CIK found for {normalized}")

    company_facts = _sec_get_json(SEC_COMPANY_FACTS_URL.format(cik=cik))
    revenue_fact = _latest_quarterly_fact(
        company_facts,
        REVENUE_FACT_CANDIDATES,
        "USD",
    )
    net_income_fact = _latest_quarterly_fact(
        company_facts,
        NET_INCOME_FACT_CANDIDATES,
        "USD",
    )
    shares_fact = _latest_instant_fact(
        company_facts,
        ("EntityCommonStockSharesOutstanding",),
        "shares",
    )

    missing = []
    if revenue_fact is None:
        missing.append("quarterly revenue")
    if net_income_fact is None:
        missing.append("quarterly net income")
    if shares_fact is None:
        missing.append("shares outstanding")

    period_end = None
    filed_date = None
    form = None
    for fact in (revenue_fact, net_income_fact, shares_fact):
        if not isinstance(fact, dict):
            continue
        period_end = max(filter(None, [period_end, fact.get("end")]), default=None)
        filed_date = max(filter(None, [filed_date, fact.get("filed")]), default=None)
        form = form or fact.get("form")

    confidence = "high" if not missing else "partial"
    payload = {
        "ticker": normalized,
        "cik": cik,
        "source": "sec-companyfacts",
        "source_url": SEC_COMPANY_FACTS_URL.format(cik=cik),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "period_end": period_end,
        "filed_date": filed_date,
        "form": form,
        "latest_quarter_revenue": safe_float(
            revenue_fact.get("val") if isinstance(revenue_fact, dict) else None,
        ),
        "latest_quarter_net_income": safe_float(
            net_income_fact.get("val") if isinstance(net_income_fact, dict) else None,
        ),
        "shares_outstanding": safe_float(
            shares_fact.get("val") if isinstance(shares_fact, dict) else None,
        ),
        "revenue_concept": revenue_fact.get("concept") if isinstance(revenue_fact, dict) else None,
        "net_income_concept": (
            net_income_fact.get("concept") if isinstance(net_income_fact, dict) else None
        ),
        "shares_concept": shares_fact.get("concept") if isinstance(shares_fact, dict) else None,
        "confidence": confidence,
        "missing": missing,
    }
    try:
        payload["online_projection"] = fetch_online_projection(normalized)
    except Exception as exc:
        payload["online_projection_error"] = str(exc)
    return payload


def _within_one_percent(manual_value: Any, reported_value: Any) -> bool | None:
    manual = safe_float(manual_value)
    reported = safe_float(reported_value)
    if manual is None or reported is None:
        return None
    if manual == 0 and reported == 0:
        return True
    denominator = max(abs(manual), abs(reported))
    if denominator <= 0:
        return None
    return abs(manual - reported) / denominator <= 0.01


def _reported_actuals_match_manual(
    scenario: dict[str, Any],
    reported: dict[str, Any],
) -> bool:
    required = (
        _within_one_percent(
            scenario.get("latest_quarter_revenue"),
            reported.get("latest_quarter_revenue"),
        ),
        _within_one_percent(
            scenario.get("latest_quarter_net_income"),
            reported.get("latest_quarter_net_income"),
        ),
    )
    if any(value is not True for value in required):
        return False

    shares_match = _within_one_percent(
        scenario.get("shares_outstanding"),
        reported.get("shares_outstanding"),
    )
    return shares_match is not False


def refresh_reported_fundamentals(tickers: list[str]) -> dict[str, Any]:
    stored = load_reported_fundamentals()
    scenario_inputs = load_scenario_inputs()
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    auto_selected: list[dict[str, Any]] = []

    normalized_tickers = sorted(
        {
            normalize_ticker(ticker)
            for ticker in tickers
            if normalize_ticker(ticker)
        },
    )
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_sec_reported_fundamentals, ticker): ticker
            for ticker in normalized_tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                payload = future.result()
                stored[ticker] = payload
                refreshed.append(payload)
                scenario = scenario_inputs.get(ticker)
                if isinstance(scenario, dict) and _reported_actuals_match_manual(
                    scenario,
                    payload,
                ):
                    if scenario.get("actuals_source_preference") != "reported":
                        scenario["actuals_source_preference"] = "reported"
                        auto_selected.append(
                            {
                                "ticker": ticker,
                                "source": "reported",
                                "reason": "manual and pulled actuals within 1%",
                            },
                        )
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc)})

    save_reported_fundamentals(stored)
    if auto_selected:
        save_scenario_inputs(scenario_inputs)
    return {
        "refreshed": sorted(refreshed, key=lambda row: str(row.get("ticker") or "")),
        "auto_selected_actuals": auto_selected,
        "errors": errors,
        "fundamentals": stored,
    }


def _parse_nasdaq_calendar_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _nasdaq_earnings_for_date(day: datetime.date) -> list[dict[str, Any]]:
    response = requests.get(
        NASDAQ_EARNINGS_URL,
        params={"date": day.isoformat()},
        headers=NASDAQ_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []

    rows = data.get("rows")
    if not isinstance(rows, list):
        return []

    as_of = _parse_nasdaq_calendar_date(data.get("asOf")) or day.isoformat()
    results: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = normalize_ticker(row.get("symbol", ""))
        if not ticker:
            continue
        results.append(
            {
                "ticker": ticker,
                "next_earnings_date": as_of,
                "time": row.get("time"),
                "source": "nasdaq-earnings-calendar",
                "source_url": f"{NASDAQ_EARNINGS_URL}?date={as_of}",
                "confidence": "scheduled",
                "fiscal_quarter_ending": row.get("fiscalQuarterEnding"),
                "eps_forecast": row.get("epsForecast"),
                "estimate_count": row.get("noOfEsts"),
                "last_year_report_date": row.get("lastYearRptDt"),
                "last_year_eps": row.get("lastYearEPS"),
                "company_name": row.get("name"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return results


def _estimate_next_earnings_date(ticker: str, reported: dict[str, Any]) -> dict[str, Any] | None:
    filed_date_text = reported.get("filed_date")
    if not filed_date_text:
        return None
    try:
        next_date = datetime.fromisoformat(str(filed_date_text)).date() + timedelta(days=91)
    except ValueError:
        return None

    today = datetime.now(timezone.utc).date()
    while next_date < today:
        next_date += timedelta(days=91)

    return {
        "ticker": ticker,
        "next_earnings_date": next_date.isoformat(),
        "time": None,
        "source": "sec-filing-cadence-estimate",
        "source_url": reported.get("source_url"),
        "confidence": "estimated",
        "fiscal_quarter_ending": None,
        "eps_forecast": None,
        "estimate_count": None,
        "last_year_report_date": None,
        "last_year_eps": None,
        "company_name": None,
        "basis_filed_date": filed_date_text,
        "basis_period_end": reported.get("period_end"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh_earnings_calendar(tickers: list[str], days_ahead: int = 180) -> dict[str, Any]:
    requested = {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    stored = load_earnings_calendar()
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not requested:
        return {"refreshed": [], "errors": [], "calendar": stored}

    today = datetime.now(timezone.utc).date()
    days = max(1, min(int(days_ahead or 180), 365))
    dates = [today + timedelta(days=offset) for offset in range(days + 1)]
    found: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_nasdaq_earnings_for_date, day): day
            for day in dates
        }
        for future in as_completed(futures):
            day = futures[future]
            try:
                rows = future.result()
            except Exception as exc:
                errors.append({"ticker": "*", "error": f"{day.isoformat()}: {exc}"})
                continue
            for row in rows:
                ticker = row.get("ticker")
                if ticker in requested and ticker not in found:
                    found[ticker] = row
            if requested.issubset(found.keys()):
                break

    reported_fundamentals = load_reported_fundamentals()
    for ticker in sorted(requested):
        payload = found.get(ticker)
        if payload is None:
            reported = reported_fundamentals.get(ticker)
            if isinstance(reported, dict):
                payload = _estimate_next_earnings_date(ticker, reported)
        if payload is None:
            errors.append({"ticker": ticker, "error": "No scheduled date or SEC estimate available"})
            continue
        stored[ticker] = payload
        refreshed.append(payload)

    save_earnings_calendar(stored)
    return {
        "refreshed": refreshed,
        "errors": errors,
        "calendar": stored,
    }


def _scenario_copy(state: dict[str, Any], scenario_name: str) -> dict[str, Any]:
    scenario = state.get(scenario_name)
    return json.loads(json.dumps(scenario)) if isinstance(scenario, dict) else {}


def _first_growth_rate(state: dict[str, Any], scenario_name: str, field: str) -> float | None:
    scenario = state.get(scenario_name)
    if not isinstance(scenario, dict):
        return None
    values = scenario.get(field)
    if not isinstance(values, list) or not values:
        return None
    value = safe_float(values[0])
    return None if value is None else value / 100.0


def _snapshot_change_summary(snapshot: dict[str, Any]) -> list[str]:
    changes = snapshot.get("changes")
    if not isinstance(changes, list):
        return []
    summary: list[str] = []
    for change in changes[:4]:
        if not isinstance(change, dict):
            continue
        label = str(change.get("label") or change.get("field") or "").strip()
        if label:
            summary.append(label)
    return summary


def _growth_error(
    actual_start: Any,
    actual_current: Any,
    annual_assumption: float | None,
    elapsed_days: int,
) -> dict[str, Any]:
    start = safe_float(actual_start)
    current = safe_float(actual_current)
    if start is None or current is None or start <= 0 or current <= 0:
        return {
            "actual_growth": None,
            "expected_growth_to_date": None,
            "error": None,
            "status": "needs actuals",
        }

    actual_growth = (current / start) - 1.0
    expected_growth = None
    error = None
    status = "baseline only"
    if annual_assumption is not None and elapsed_days > 0:
        expected_growth = ((1.0 + annual_assumption) ** (elapsed_days / 365.0)) - 1.0
        error = actual_growth - expected_growth
        if error > 0.05:
            status = "ahead"
        elif error < -0.05:
            status = "behind"
        else:
            status = "tracking"

    return {
        "actual_growth": actual_growth,
        "expected_growth_to_date": expected_growth,
        "error": error,
        "status": status,
    }


def append_assumption_snapshot(
    ticker: str,
    scenario: dict[str, Any],
    row_context: dict[str, Any] | None,
    previous_scenario: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
) -> None:
    normalized = normalize_ticker(ticker)
    if not normalized:
        return

    context = row_context or {}
    snapshots = load_assumption_snapshots()
    previous = previous_scenario if isinstance(previous_scenario, dict) else {}
    snapshots.append(
        {
            "id": uuid.uuid4().hex,
            "ticker": normalized,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "revision_type": "update" if previous else "initial",
            "changes": changes if isinstance(changes, list) else [],
            "latest_quarter_revenue": safe_float(scenario.get("latest_quarter_revenue")),
            "latest_quarter_net_income": safe_float(scenario.get("latest_quarter_net_income")),
            "shares_outstanding": safe_float(scenario.get("shares_outstanding")),
            "previous_latest_quarter_revenue": safe_float(previous.get("latest_quarter_revenue")),
            "previous_latest_quarter_net_income": safe_float(previous.get("latest_quarter_net_income")),
            "previous_shares_outstanding": safe_float(previous.get("shares_outstanding")),
            "price": safe_float(context.get("price")),
            "current_pe": safe_float(context.get("current_pe")),
            "bear_cagr_y3": safe_float(context.get("bear_cagr_y3")),
            "base_cagr_y3": safe_float(context.get("base_cagr_y3")),
            "bull_cagr_y3": safe_float(context.get("bull_cagr_y3")),
            "weighted_cagr_y3": safe_float(context.get("weighted_cagr_y3")),
            "action": context.get("action"),
            "action_rank": context.get("action_rank"),
            "compression_opportunity_score": safe_float(
                context.get("compression_opportunity_score"),
            ),
            "bear": _scenario_copy(scenario, "bear"),
            "base": _scenario_copy(scenario, "base"),
            "bull": _scenario_copy(scenario, "bull"),
            "previous_bear": _scenario_copy(previous, "bear"),
            "previous_base": _scenario_copy(previous, "base"),
            "previous_bull": _scenario_copy(previous, "bull"),
        },
    )
    save_assumption_snapshots(snapshots)


def _current_assumption_snapshot(
    ticker: str,
    scenario: dict[str, Any],
    row_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = row_context or {}
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"current-{normalize_ticker(ticker)}",
        "ticker": normalize_ticker(ticker),
        "timestamp": now,
        "latest_quarter_revenue": safe_float(scenario.get("latest_quarter_revenue")),
        "latest_quarter_net_income": safe_float(scenario.get("latest_quarter_net_income")),
        "shares_outstanding": safe_float(scenario.get("shares_outstanding")),
        "price": safe_float(context.get("price")),
        "current_pe": safe_float(context.get("current_pe")),
        "bear_cagr_y3": safe_float(context.get("bear_cagr_y3")),
        "base_cagr_y3": safe_float(context.get("base_cagr_y3")),
        "bull_cagr_y3": safe_float(context.get("bull_cagr_y3")),
        "weighted_cagr_y3": safe_float(context.get("weighted_cagr_y3")),
        "action": context.get("action"),
        "action_rank": context.get("action_rank"),
        "compression_opportunity_score": safe_float(
            context.get("compression_opportunity_score"),
        ),
        "bear": _scenario_copy(scenario, "bear"),
        "base": _scenario_copy(scenario, "base"),
        "bull": _scenario_copy(scenario, "bull"),
        "is_current_assumption": True,
    }


def build_forecast_scorecard() -> dict[str, Any]:
    snapshots = sorted(
        load_assumption_snapshots(),
        key=lambda item: str(item.get("timestamp") or ""),
        reverse=True,
    )
    scenario_inputs = load_scenario_inputs()
    tickers_config = get_effective_tickers_config()
    portfolio_tickers = {
        row["ticker"]
        for row in normalize_portfolio(tickers_config.get("portfolio", []))
        if row.get("ticker")
    }
    portfolio_view = build_portfolio_views(
        tickers_config,
        scenario_inputs,
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=False,
    )
    reported_fundamentals = load_reported_fundamentals()
    row_by_ticker = {
        row.get("ticker"): row
        for row in portfolio_view.get("portfolio", [])
        if isinstance(row, dict) and row.get("ticker")
    }

    latest_by_ticker: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        ticker = normalize_ticker(snapshot.get("ticker", ""))
        if ticker and ticker in portfolio_tickers and ticker not in latest_by_ticker:
            latest_by_ticker[ticker] = snapshot

    for ticker, scenario in scenario_inputs.items():
        normalized = normalize_ticker(ticker)
        if (
            not normalized
            or normalized not in portfolio_tickers
            or normalized in latest_by_ticker
            or not isinstance(scenario, dict)
        ):
            continue
        latest_by_ticker[normalized] = _current_assumption_snapshot(
            normalized,
            scenario,
            row_by_ticker.get(normalized),
        )

    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for ticker, snapshot in latest_by_ticker.items():
        current_state = scenario_inputs.get(ticker, {})
        if not isinstance(current_state, dict):
            current_state = {}
        current_row = row_by_ticker.get(ticker, {})
        reported = reported_fundamentals.get(ticker)
        if not isinstance(reported, dict):
            reported = {}
        reported_revenue = safe_float(reported.get("latest_quarter_revenue"))
        reported_net_income = safe_float(reported.get("latest_quarter_net_income"))
        reported_shares = safe_float(reported.get("shares_outstanding"))
        preference = current_state.get("actuals_source_preference")
        if preference not in {"manual", "reported"}:
            preference = "manual"
        use_reported = preference == "reported" and bool(reported)
        current_revenue = current_state.get("latest_quarter_revenue")
        current_net_income = current_state.get("latest_quarter_net_income")
        current_shares = current_state.get("shares_outstanding")
        reported_fields_used = []
        if use_reported:
            if reported_revenue is not None:
                current_revenue = reported_revenue
                reported_fields_used.append("latest_quarter_revenue")
            if reported_net_income is not None:
                current_net_income = reported_net_income
                reported_fields_used.append("latest_quarter_net_income")
            if reported_shares is not None:
                current_shares = reported_shares
                reported_fields_used.append("shares_outstanding")
        actuals_source = "stock-detail"
        if use_reported and reported_fields_used:
            actuals_source = (
                "sec-companyfacts"
                if len(reported_fields_used) >= 2
                else "mixed"
            )
        try:
            snapshot_time = datetime.fromisoformat(str(snapshot.get("timestamp")))
            if snapshot_time.tzinfo is None:
                snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
        except ValueError:
            snapshot_time = now
        elapsed_days = max(0, (now - snapshot_time).days)

        base_rev_assumption = _first_growth_rate(snapshot, "base", "rev_growth_rates")
        base_earnings_assumption = _first_growth_rate(
            snapshot,
            "base",
            "net_income_growth_rates",
        )
        revenue_score = _growth_error(
            snapshot.get("latest_quarter_revenue"),
            current_revenue,
            base_rev_assumption,
            elapsed_days,
        )
        earnings_score = _growth_error(
            snapshot.get("latest_quarter_net_income"),
            current_net_income,
            base_earnings_assumption,
            elapsed_days,
        )

        start_price = safe_float(snapshot.get("price"))
        current_price = safe_float(current_row.get("price"))
        price_return = None
        cagr_error = None
        if start_price is not None and current_price is not None and start_price > 0:
            price_return = (current_price / start_price) - 1.0
            base_cagr = safe_float(snapshot.get("base_cagr_y3"))
            if base_cagr is not None and elapsed_days > 0:
                expected_price_return = ((1.0 + base_cagr) ** (elapsed_days / 365.0)) - 1.0
                cagr_error = price_return - expected_price_return

        rows.append(
            {
                "ticker": ticker,
                "snapshot_id": snapshot.get("id"),
                "snapshot_timestamp": snapshot.get("timestamp"),
                "is_current_assumption": bool(snapshot.get("is_current_assumption")),
                "revision_type": snapshot.get("revision_type") or (
                    "current" if snapshot.get("is_current_assumption") else "saved"
                ),
                "change_summary": _snapshot_change_summary(snapshot),
                "elapsed_days": elapsed_days,
                "start_price": start_price,
                "current_price": current_price,
                "price_return": price_return,
                "base_cagr_y3_at_snapshot": safe_float(snapshot.get("base_cagr_y3")),
                "cagr_error_to_date": cagr_error,
                "base_revenue_growth_y1": base_rev_assumption,
                "base_earnings_growth_y1": base_earnings_assumption,
                "actuals_source_preference": preference,
                "actuals_source": actuals_source,
                "actuals_reported_fields_used": reported_fields_used,
                "reported_period_end": reported.get("period_end"),
                "reported_filed_date": reported.get("filed_date"),
                "reported_confidence": reported.get("confidence"),
                "reported_missing": reported.get("missing", []),
                "current_latest_quarter_revenue": safe_float(current_revenue),
                "current_latest_quarter_net_income": safe_float(current_net_income),
                "current_shares_outstanding": safe_float(current_shares),
                "revenue_actual_growth": revenue_score["actual_growth"],
                "revenue_expected_growth_to_date": revenue_score["expected_growth_to_date"],
                "revenue_error": revenue_score["error"],
                "revenue_status": revenue_score["status"],
                "earnings_actual_growth": earnings_score["actual_growth"],
                "earnings_expected_growth_to_date": earnings_score[
                    "expected_growth_to_date"
                ],
                "earnings_error": earnings_score["error"],
                "earnings_status": earnings_score["status"],
                "action_at_snapshot": snapshot.get("action"),
                "action_rank_at_snapshot": snapshot.get("action_rank"),
                "current_action": current_row.get("action"),
                "current_base_cagr_y3": safe_float(current_row.get("base_cagr_y3")),
            },
        )

    rows.sort(
        key=lambda row: (
            row.get("revenue_error") is None and row.get("earnings_error") is None,
            str(row.get("ticker") or ""),
        ),
    )

    history_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        ticker = normalize_ticker(snapshot.get("ticker", ""))
        if not ticker or ticker not in portfolio_tickers:
            continue
        current_state = scenario_inputs.get(ticker, {})
        if not isinstance(current_state, dict):
            current_state = {}
        current_row = row_by_ticker.get(ticker, {})
        reported = reported_fundamentals.get(ticker)
        if not isinstance(reported, dict):
            reported = {}
        preference = current_state.get("actuals_source_preference")
        if preference not in {"manual", "reported"}:
            preference = "manual"
        current_revenue = current_state.get("latest_quarter_revenue")
        current_net_income = current_state.get("latest_quarter_net_income")
        if preference == "reported":
            current_revenue = (
                safe_float(reported.get("latest_quarter_revenue"))
                if safe_float(reported.get("latest_quarter_revenue")) is not None
                else current_revenue
            )
            current_net_income = (
                safe_float(reported.get("latest_quarter_net_income"))
                if safe_float(reported.get("latest_quarter_net_income")) is not None
                else current_net_income
            )
        try:
            snapshot_time = datetime.fromisoformat(str(snapshot.get("timestamp")))
            if snapshot_time.tzinfo is None:
                snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
        except ValueError:
            snapshot_time = now
        elapsed_days = max(0, (now - snapshot_time).days)
        base_rev_assumption = _first_growth_rate(snapshot, "base", "rev_growth_rates")
        base_earnings_assumption = _first_growth_rate(
            snapshot,
            "base",
            "net_income_growth_rates",
        )
        revenue_score = _growth_error(
            snapshot.get("latest_quarter_revenue"),
            current_revenue,
            base_rev_assumption,
            elapsed_days,
        )
        earnings_score = _growth_error(
            snapshot.get("latest_quarter_net_income"),
            current_net_income,
            base_earnings_assumption,
            elapsed_days,
        )
        start_price = safe_float(snapshot.get("price"))
        current_price = safe_float(current_row.get("price"))
        price_return = None
        if start_price is not None and current_price is not None and start_price > 0:
            price_return = (current_price / start_price) - 1.0
        previous_base_rev = _first_growth_rate(snapshot, "previous_base", "rev_growth_rates")
        previous_base_earnings = _first_growth_rate(
            snapshot,
            "previous_base",
            "net_income_growth_rates",
        )
        history_rows.append(
            {
                "ticker": ticker,
                "snapshot_id": snapshot.get("id"),
                "snapshot_timestamp": snapshot.get("timestamp"),
                "revision_type": snapshot.get("revision_type") or "saved",
                "change_summary": _snapshot_change_summary(snapshot),
                "elapsed_days": elapsed_days,
                "base_revenue_growth_y1": base_rev_assumption,
                "previous_base_revenue_growth_y1": previous_base_rev,
                "base_earnings_growth_y1": base_earnings_assumption,
                "previous_base_earnings_growth_y1": previous_base_earnings,
                "base_cagr_y3_at_snapshot": safe_float(snapshot.get("base_cagr_y3")),
                "action_at_snapshot": snapshot.get("action"),
                "start_price": start_price,
                "current_price": current_price,
                "price_return": price_return,
                "revenue_actual_growth": revenue_score["actual_growth"],
                "revenue_expected_growth_to_date": revenue_score["expected_growth_to_date"],
                "revenue_error": revenue_score["error"],
                "revenue_status": revenue_score["status"],
                "earnings_actual_growth": earnings_score["actual_growth"],
                "earnings_expected_growth_to_date": earnings_score["expected_growth_to_date"],
                "earnings_error": earnings_score["error"],
                "earnings_status": earnings_score["status"],
            },
        )
    history_rows.sort(
        key=lambda row: (
            str(row.get("ticker") or ""),
            str(row.get("snapshot_timestamp") or ""),
        ),
        reverse=True,
    )
    return {
        "rows": rows,
        "history": history_rows[:500],
        "snapshots": snapshots[:500],
        "snapshot_count": len(snapshots),
    }


def values_are_equal(previous: Any, current: Any) -> bool:
    if previous is None and current in ("", None):
        return True
    if current is None and previous in ("", None):
        return True

    try:
        return abs(float(previous) - float(current)) < 0.000001
    except (TypeError, ValueError):
        return previous == current


def flatten_change_values(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, nested_value in value.items():
            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_change_values(nested_value, nested_prefix))
        return flattened

    if isinstance(value, list):
        flattened = {}
        for idx, nested_value in enumerate(value):
            nested_prefix = f"{prefix}.{idx}" if prefix else str(idx)
            flattened.update(flatten_change_values(nested_value, nested_prefix))
        return flattened

    return {prefix: value}


def build_change_set(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    labels: dict[str, str],
) -> list[dict[str, Any]]:
    previous_values = flatten_change_values(previous or {})
    current_values = flatten_change_values(current)
    changes: list[dict[str, Any]] = []

    for field in labels:
        old_value = previous_values.get(field)
        new_value = current_values.get(field)
        if values_are_equal(old_value, new_value):
            continue
        changes.append(
            {
                "field": field,
                "label": labels[field],
                "old": old_value,
                "new": new_value,
            },
        )

    return changes


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def parse_import_float(value: Any, default: float = 0.0) -> float:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def normalize_import_date(value: Any) -> str:
    text = str(value or "").strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def transaction_import_fingerprint(parts: list[Any]) -> str:
    normalized = "|".join(str(part or "").strip().casefold() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_fidelity_transaction_files(
    files: list[TransactionImportFile],
    account_mappings: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    aliases = load_account_aliases()
    aliases.update(
        {
            str(key).strip(): str(value).strip()
            for key, value in account_mappings.items()
            if str(key).strip() and str(value).strip()
        },
    )

    rows: list[dict[str, Any]] = []
    accounts: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for uploaded in files:
        reader = csv.reader(io.StringIO(uploaded.content.lstrip("\ufeff")))
        raw_rows = list(reader)
        header_index = next(
            (
                idx
                for idx, row in enumerate(raw_rows)
                if row and str(row[0]).strip() == "Run Date"
            ),
            None,
        )

        if header_index is None:
            skipped.append(
                {
                    "file": uploaded.name,
                    "reason": "Unsupported CSV: Run Date header was not found",
                },
            )
            continue

        header = [str(value).strip() for value in raw_rows[header_index]]
        for row_number, raw_row in enumerate(raw_rows[header_index + 1 :], header_index + 2):
            if not raw_row or not any(str(value).strip() for value in raw_row):
                continue

            padded = list(raw_row) + [""] * max(0, len(header) - len(raw_row))
            record = dict(zip(header, padded))
            action = str(record.get("Action") or "").strip()
            run_date = str(record.get("Run Date") or "").strip()

            if not action and not normalize_import_date(run_date):
                continue

            if action.startswith("YOU BOUGHT"):
                tx_type = "buy"
            elif action.startswith("YOU SOLD"):
                tx_type = "sell"
            else:
                skipped.append(
                    {
                        "file": uploaded.name,
                        "row": row_number,
                        "reason": "Not a supported buy or sell transaction",
                        "action": action,
                    },
                )
                continue

            ticker = normalize_ticker(record.get("Symbol") or "")
            date = normalize_import_date(record.get("Run Date"))
            shares = abs(parse_import_float(record.get("Quantity")))
            price = abs(parse_import_float(record.get("Price ($)")))
            fees = abs(parse_import_float(record.get("Commission ($)"))) + abs(
                parse_import_float(record.get("Fees ($)")),
            )
            amount = parse_import_float(record.get("Amount ($)"))
            account_name = str(record.get("Account") or "").strip()
            account_number = str(record.get("Account Number") or "").strip()
            account_key = account_number or account_name.casefold()
            account = aliases.get(account_key) or account_name or account_number

            if not ticker or not date or shares <= 0 or price <= 0 or not account_key:
                skipped.append(
                    {
                        "file": uploaded.name,
                        "row": row_number,
                        "reason": "Missing ticker, date, shares, price, or account",
                        "action": action,
                    },
                )
                continue

            accounts.setdefault(
                account_key,
                {
                    "key": account_key,
                    "source_name": account_name or "Unnamed account",
                    "account_number_last4": account_number[-4:] if account_number else "",
                    "nickname": account,
                },
            )

            fingerprint = transaction_import_fingerprint(
                [
                    "fidelity",
                    account_key,
                    date,
                    action,
                    ticker,
                    record.get("Price ($)"),
                    record.get("Quantity"),
                    record.get("Commission ($)"),
                    record.get("Fees ($)"),
                    record.get("Amount ($)"),
                    record.get("Settlement Date"),
                ],
            )

            rows.append(
                {
                    "ticker": ticker,
                    "date": date,
                    "type": tx_type,
                    "shares": shares,
                    "price_per_share": price,
                    "fees": fees,
                    "account": account,
                    "notes": str(record.get("Description") or action).strip(),
                    "source": "fidelity-csv",
                    "source_file": uploaded.name,
                    "source_account": account_name,
                    "source_account_key": account_key,
                    "source_amount": amount,
                    "settlement_date": normalize_import_date(record.get("Settlement Date")),
                    "import_fingerprint": fingerprint,
                },
            )

    return rows, list(accounts.values()), skipped


def existing_import_fingerprints(
    transactions: dict[str, list[dict[str, Any]]],
) -> set[str]:
    return {
        str(entry.get("import_fingerprint"))
        for entries in transactions.values()
        for entry in entries
        if isinstance(entry, dict) and entry.get("import_fingerprint")
    }


def validate_transaction_payload(payload: TransactionCreate | TransactionUpdate) -> dict[str, Any]:
    tx_type = str(payload.type).strip().lower()
    if tx_type not in VALID_TRANSACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transaction type. Must be one of: {', '.join(sorted(VALID_TRANSACTION_TYPES))}",
        )

    if payload.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    price_per_share = payload.price_per_share
    if price_per_share is not None and price_per_share < 0:
        raise HTTPException(status_code=400, detail="Price per share must be non-negative")

    fees = payload.fees if payload.fees is not None else 0.0
    if fees < 0:
        raise HTTPException(status_code=400, detail="Fees must be non-negative")

    return {
        "date": str(payload.date).strip(),
        "type": tx_type,
        "shares": float(payload.shares),
        "price_per_share": None if price_per_share is None else float(price_per_share),
        "fees": float(fees),
        "account": str(payload.account or "").strip(),
        "notes": str(payload.notes or "").strip(),
    }


def load_tickers_config() -> dict[str, Any]:
    if not TICKERS_FILE.exists():
        return {"portfolio": [], "watchlist": []}
    with open(TICKERS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {"portfolio": [], "watchlist": []}


def apply_tickers_overrides_to_config(
    tickers_config: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    base_portfolio = tickers_config.get("portfolio", []) or []
    base_watchlist = tickers_config.get("watchlist", []) or []

    portfolio_map: dict[str, dict[str, Any]] = {}
    watchlist_set: set[str] = set()

    for item in base_portfolio:
        if isinstance(item, str):
            ticker = normalize_ticker(item)
            if ticker:
                portfolio_map[ticker] = {"ticker": ticker, "shares": None}
        elif isinstance(item, dict):
            ticker = normalize_ticker(item.get("ticker", ""))
            if ticker:
                portfolio_map[ticker] = {
                    "ticker": ticker,
                    "shares": item.get("shares"),
                }

    for item in base_watchlist:
        ticker = normalize_ticker(item)
        if ticker:
            watchlist_set.add(ticker)

    tickers_payload = overrides.get("tickers", {}) if isinstance(overrides, dict) else {}
    if isinstance(tickers_payload, dict):
        for ticker, payload in tickers_payload.items():
            if not isinstance(payload, dict):
                continue

            if payload.get("removed"):
                portfolio_map.pop(ticker, None)
                watchlist_set.discard(ticker)
                continue

            if payload.get("archived"):
                portfolio_map.pop(ticker, None)
                watchlist_set.discard(ticker)
                continue

            list_value = payload.get("list")
            shares = payload.get("shares")

            if list_value == "portfolio":
                portfolio_map[ticker] = {
                    "ticker": ticker,
                    "shares": shares,
                }
                watchlist_set.discard(ticker)
            elif list_value == "watchlist":
                portfolio_map.pop(ticker, None)
                watchlist_set.add(ticker)

    return {
        "portfolio": list(portfolio_map.values()),
        "watchlist": sorted(watchlist_set),
    }


def apply_holdings_overrides_to_config(
    tickers_config: dict[str, Any],
    overrides: dict[str, float],
) -> dict[str, Any]:
    portfolio = tickers_config.get("portfolio", []) or []
    updated_portfolio: list[dict[str, Any]] = []

    for item in portfolio:
        if isinstance(item, str):
            ticker = normalize_ticker(item)
            updated_portfolio.append(
                {
                    "ticker": ticker,
                    "shares": overrides.get(ticker),
                }
            )
            continue

        if isinstance(item, dict):
            ticker = normalize_ticker(item.get("ticker", ""))
            if not ticker:
                continue
            updated_item = dict(item)
            if ticker in overrides:
                updated_item["shares"] = overrides[ticker]
            updated_portfolio.append(updated_item)

    return {
        "portfolio": updated_portfolio,
        "watchlist": tickers_config.get("watchlist", []) or [],
    }


def get_effective_tickers_config() -> dict[str, Any]:
    base = load_tickers_config()
    tickers_overrides = load_tickers_overrides()
    with_ticker_overrides = apply_tickers_overrides_to_config(base, tickers_overrides)
    holdings_overrides = load_holdings_overrides()
    return apply_holdings_overrides_to_config(with_ticker_overrides, holdings_overrides)


def serialize_ticker_scenario(raw: dict[str, Any]) -> TickerScenarioInputs:
    return TickerScenarioInputs.model_validate(raw)


def current_user_role(request: Request | None) -> str:
    if not request:
        return "full"
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return str(user.get("role") or "full")
    return "full"


def has_full_access(request: Request | None) -> bool:
    return current_user_role(request) == "full"


def current_user_email(request: Request | None) -> str:
    if not request:
        return ""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return str(user.get("email") or "").strip().casefold()
    return ""


def user_holdings_path(email: str) -> Path:
    key = hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
    return BASE_DIR / "cache" / f"user_holdings_{key}.json"


def load_user_holdings(email: str) -> dict[str, float]:
    if not email:
        return {}

    raw = load_json_file(user_holdings_path(email), {})
    if not isinstance(raw, dict):
        return {}

    holdings: dict[str, float] = {}
    for ticker, shares in raw.items():
        normalized = normalize_ticker(str(ticker))
        if not normalized:
            continue
        try:
            value = float(shares)
        except (TypeError, ValueError):
            continue
        if value > 0:
            holdings[normalized] = value
    return holdings


def save_user_holdings(email: str, holdings: dict[str, float]) -> None:
    if not email:
        raise HTTPException(status_code=403, detail="Signed-in user is required")
    cleaned: dict[str, float] = {}
    for ticker, shares in holdings.items():
        normalized = normalize_ticker(str(ticker))
        if not normalized:
            continue
        try:
            value = float(shares)
        except (TypeError, ValueError):
            continue
        if value > 0:
            cleaned[normalized] = value
    save_json_file(user_holdings_path(email), dict(sorted(cleaned.items())))


def shared_tracking_tickers() -> set[str]:
    config = get_effective_tickers_config()
    portfolio_tickers = {
        row["ticker"]
        for row in normalize_portfolio(config.get("portfolio", []))
        if row.get("ticker")
    }
    watchlist_tickers = {
        normalize_ticker(ticker)
        for ticker in config.get("watchlist", []) or []
        if normalize_ticker(str(ticker))
    }
    scenario_tickers = {
        normalize_ticker(ticker)
        for ticker in load_scenario_inputs()
        if normalize_ticker(str(ticker))
    }
    return portfolio_tickers | watchlist_tickers | scenario_tickers


def get_limited_tickers_config(request: Request) -> dict[str, Any]:
    holdings = load_user_holdings(current_user_email(request))
    shared_config = get_effective_tickers_config()
    shared_portfolio = normalize_portfolio(shared_config.get("portfolio", []))
    portfolio = [
        {
            "ticker": row["ticker"],
            "shares": 0.0,
        }
        for row in sorted(shared_portfolio, key=lambda item: item["ticker"])
        if row.get("ticker")
    ]
    shared_portfolio_tickers = {
        row["ticker"] for row in shared_portfolio if row.get("ticker")
    }
    portfolio.extend(
        {"ticker": ticker, "shares": shares}
        for ticker, shares in sorted(holdings.items())
        if ticker not in shared_portfolio_tickers
    )
    portfolio_tickers = {row["ticker"] for row in portfolio}
    watchlist = sorted(shared_tracking_tickers() - portfolio_tickers)
    return {
        "portfolio": portfolio,
        "watchlist": watchlist,
    }


def get_tickers_config_for_request(request: Request | None) -> dict[str, Any]:
    if request is not None and not has_full_access(request):
        return get_limited_tickers_config(request)
    return get_effective_tickers_config()


def redact_tickers_config_for_limited(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload))

    def redact_config(config: Any) -> Any:
        if not isinstance(config, dict):
            return config
        portfolio = []
        for row in config.get("portfolio", []) or []:
            if isinstance(row, dict):
                next_row = dict(row)
                next_row["shares"] = None
                portfolio.append(next_row)
            else:
                portfolio.append(row)
        return {
            **config,
            "portfolio": portfolio,
        }

    for key in ("base", "effective"):
        if key in redacted:
            redacted[key] = redact_config(redacted[key])

    overrides = redacted.get("overrides")
    if isinstance(overrides, dict):
        tickers = overrides.get("tickers")
        if isinstance(tickers, dict):
            for value in tickers.values():
                if isinstance(value, dict):
                    value["shares"] = None

    if "portfolio" in redacted:
        redacted = redact_config(redacted)

    return redacted


def redact_portfolio_view_for_limited(payload: Any) -> Any:
    redacted = json.loads(json.dumps(payload))
    sensitive_fields = {
        "shares",
        "market_value",
        "total_value",
        "managed_shares",
        "track_shares",
        "excluded_shares",
        "eligible_redistribution_shares",
        "locked_shares",
        "total_cost_basis",
        "average_cost_per_share",
        "dollar_trade",
        "shares_trade",
        "current_eligible_weight",
        "target_weight_effective",
    }

    for section in ("portfolio", "watchlist", "action_queue"):
        rows = redacted.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in sensitive_fields:
                if field in row:
                    row[field] = None

    metrics = redacted.get("metrics")
    if isinstance(metrics, dict):
        for field in ("total_value", "portfolio_value", "market_value"):
            if field in metrics:
                metrics[field] = None

    redacted["action_queue"] = []
    redacted["action_queue_summary"] = {}
    return redacted


def redact_event_for_limited(event: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(event)
    payload = redacted.get("payload")
    if not isinstance(payload, dict):
        return redacted

    sensitive_keys = {
        "account",
        "accounts",
        "date",
        "files",
        "from_account",
        "shares",
        "to_account",
        "transaction_id",
        "transaction_type",
        "transactions_updated",
    }
    clean_payload = {
        key: value
        for key, value in payload.items()
        if key not in sensitive_keys
    }
    if isinstance(clean_payload.get("changes"), list):
        clean_payload["changes"] = [
            change
            for change in clean_payload["changes"]
            if isinstance(change, dict)
            and "share" not in str(change.get("field") or "").casefold()
            and "share" not in str(change.get("label") or "").casefold()
        ]
    redacted["payload"] = clean_payload
    return redacted


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/config")
def get_auth_config() -> dict[str, Any]:
    return {
        "enabled": GOOGLE_AUTH_ENABLED,
        "client_id": GOOGLE_CLIENT_ID if GOOGLE_AUTH_ENABLED else "",
    }


@app.get("/api/auth/me")
def get_auth_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        return {"authenticated": False, "role": "full"}
    return {
        "authenticated": True,
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "picture": user.get("picture") or "",
        "role": user.get("role") or "full",
    }


@app.get("/api/tickers")
def get_ticker_registry(request: Request) -> dict[str, Any]:
    if has_full_access(request):
        return {
            "base": load_tickers_config(),
            "overrides": load_tickers_overrides(),
            "effective": get_effective_tickers_config(),
        }

    redacted = redact_tickers_config_for_limited(
        {
            "base": load_tickers_config(),
            "overrides": load_tickers_overrides(),
            "effective": get_effective_tickers_config(),
        },
    )
    redacted["effective"] = get_limited_tickers_config(request)
    return redacted


def build_portfolio_view_for_request(
    request: Request,
    force_refresh: bool = False,
) -> dict[str, Any]:
    position_summaries = (
        build_position_summaries()
        if has_full_access(request)
        else None
    )
    return build_portfolio_views(
        get_tickers_config_for_request(request),
        load_scenario_inputs(),
        load_settings_dict(),
        management_shares=(
            build_management_snapshot()["shares_by_mode"]
            if has_full_access(request)
            else None
        ),
        position_summaries=position_summaries,
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )


@app.get("/api/tickers/shared")
def get_shared_ticker_registry() -> dict[str, Any]:
    return {
        "base": load_tickers_config(),
        "overrides": load_tickers_overrides(),
        "effective": get_effective_tickers_config(),
    }


@app.post("/api/sheets/tickers/sync")
def sync_tickers_to_sheet() -> dict[str, Any]:
    try:
        return push_tickers_to_sheet(get_effective_tickers_config())
    except requests.HTTPError as exc:
        detail = str(exc)
        if exc.response is not None:
            detail = exc.response.text or detail
        raise HTTPException(status_code=502, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/events")
def get_portfolio_events(
    request: Request,
    ticker: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    events = load_portfolio_events()

    if ticker:
        normalized = normalize_ticker(ticker)
        events = [event for event in events if event.get("ticker") == normalized]

    events = sorted(
        events,
        key=lambda event: str(event.get("timestamp", "")),
        reverse=True,
    )

    limit = max(1, min(int(limit), 5000))
    events = events[:limit]
    if not has_full_access(request):
        events = [redact_event_for_limited(event) for event in events]

    return {
        "count": len(events),
        "events": events,
    }


@app.get("/api/forecasts")
def get_forecast_scorecard(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Forecast scorecard is private")
    return build_forecast_scorecard()


@app.get("/api/fundamentals/reported")
def get_reported_fundamentals(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Reported fundamentals are private")
    return {"fundamentals": load_reported_fundamentals()}


@app.post("/api/fundamentals/reported/refresh")
def refresh_reported_fundamentals_endpoint(
    request: Request,
    body: FundamentalsRefreshRequest,
) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Reported fundamentals are private")

    tickers = [normalize_ticker(ticker) for ticker in body.tickers]
    tickers = [ticker for ticker in tickers if ticker]
    if not tickers:
        config = get_effective_tickers_config()
        portfolio = normalize_portfolio(config.get("portfolio", []))
        tickers = sorted(
            {
                row["ticker"]
                for row in portfolio
                if row.get("ticker")
            }
        )

    payload = refresh_reported_fundamentals(tickers)
    invalidate_response_cache()
    return payload


@app.get("/api/earnings-calendar")
def get_earnings_calendar(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Earnings calendar is private")
    return {"calendar": load_earnings_calendar()}


@app.post("/api/earnings-calendar/refresh")
def refresh_earnings_calendar_endpoint(
    request: Request,
    body: EarningsCalendarRefreshRequest,
) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Earnings calendar is private")

    tickers = [normalize_ticker(ticker) for ticker in body.tickers]
    tickers = [ticker for ticker in tickers if ticker]
    if not tickers:
        config = get_effective_tickers_config()
        portfolio = normalize_portfolio(config.get("portfolio", []))
        tickers = sorted(
            {
                row["ticker"]
                for row in portfolio
                if row.get("ticker")
            }
            | {
                normalize_ticker(ticker)
                for ticker in config.get("watchlist", [])
                if normalize_ticker(ticker)
            },
        )

    payload = refresh_earnings_calendar(tickers, days_ahead=body.days_ahead)
    invalidate_response_cache()
    return payload


@app.get("/api/performance/portfolio")
def get_portfolio_performance(
    range: str = "3y",
    accounts: str = "",
    mode: str = "all",
) -> dict[str, Any]:
    try:
        cache_key = (
            f"performance:{str(range).strip().lower()}:"
            f"{str(accounts).strip().casefold()}:{str(mode).strip().lower()}"
        )
        cached = get_cached_response(cache_key)
        if cached is not None:
            return cached

        transactions = load_transactions()
        tickers_config = get_effective_tickers_config()
        management = build_management_snapshot()
        normalized_mode = str(mode or "all").strip().lower()
        if normalized_mode != "all" and normalized_mode not in VALID_MANAGEMENT_MODES:
            raise ValueError("Mode must be all, managed, track, or excluded")

        if normalized_mode in VALID_MANAGEMENT_MODES:
            transactions = filter_transactions_by_management_mode(
                transactions,
                normalized_mode,
            )
            tickers_config = apply_management_shares_to_config(
                tickers_config,
                management["shares_by_mode"],
                normalized_mode,
            )
        account_filter = [
            account.strip()
            for account in str(accounts).split(",")
            if account.strip()
        ]
        payload = build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=tickers_config,
            range_key=range,
            accounts=account_filter or None,
            supplemental_positions=[
                position
                for position in management["positions"]
                if position["source"] != "transactions"
                and (
                    normalized_mode == "all"
                    or position["mode"] == normalized_mode
                )
            ],
        )
        return set_cached_response(cache_key, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def transaction_cash_flow_amount(tx: dict[str, Any]) -> tuple[float, float]:
    tx_type = str(tx.get("type", "")).strip().lower()
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0

    if tx_type in {"buy", "transfer_in", "adjustment"}:
        if shares <= 0 or price_per_share is None:
            return 0.0, 0.0
        return (shares * price_per_share) + fees, 0.0

    if tx_type in {"sell", "transfer_out"}:
        if shares <= 0 or price_per_share is None:
            return 0.0, 0.0
        return -max((shares * price_per_share) - fees, 0.0), 0.0

    if tx_type == "dividend":
        return 0.0, max(price_per_share or 0.0, 0.0)

    return 0.0, 0.0


def benchmark_close_on_or_after(
    benchmark_points: list[dict[str, Any]],
    flow_date: str,
) -> float | None:
    for point in benchmark_points:
        point_date = str(point.get("date") or "")
        close = safe_float(point.get("close"))
        if point_date >= flow_date and close is not None and close > 0:
            return close
    return None


def benchmark_close_on_or_before(
    benchmark_lookup: dict[str, float],
    sorted_dates: list[str],
    target_date: str,
) -> float | None:
    for point_date in reversed(sorted_dates):
        if point_date <= target_date:
            return benchmark_lookup.get(point_date)
    return None


def filter_transactions_by_accounts(
    transactions: dict[str, list[dict[str, Any]]],
    accounts: set[str],
) -> dict[str, list[dict[str, Any]]]:
    selected = {account.casefold() for account in accounts}
    filtered: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        rows = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("account") or "").strip().casefold() in selected
        ]
        if rows:
            filtered[ticker] = rows
    return filtered


@app.get("/api/compensation")
def get_compensation_tracker(
    range: str = "1y",
    benchmark: str = "SPY",
    share_pct: float = 0.33,
) -> dict[str, Any]:
    try:
        normalized_benchmark = normalize_ticker(benchmark) or "SPY"
        payout_share = max(0.0, min(float(share_pct), 1.0))
        transactions = filter_transactions_by_accounts(
            load_transactions(),
            MATTHEW_COMPENSATION_ACCOUNTS,
        )
        management = build_management_snapshot()
        performance = build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=get_effective_tickers_config(),
            range_key=range,
            accounts=sorted(MATTHEW_COMPENSATION_ACCOUNTS),
            supplemental_positions=[
                position
                for position in management["positions"]
                if position["source"] != "transactions"
                and str(position.get("account") or "").strip().casefold()
                in MATTHEW_COMPENSATION_ACCOUNT_KEYS
            ],
        )
        portfolio_series = performance.get("series")
        if not isinstance(portfolio_series, list):
            portfolio_series = []
        valid_portfolio = [
            row
            for row in portfolio_series
            if isinstance(row, dict) and safe_float(row.get("market_value")) is not None
        ]
        benchmark_points = [
            row
            for row in get_price_points_for_ticker(normalized_benchmark, range_key=range)
            if isinstance(row, dict) and safe_float(row.get("close")) is not None
        ]

        if len(valid_portfolio) < 2:
            raise ValueError("Need at least two portfolio performance points")
        if len(benchmark_points) < 2:
            raise ValueError(f"Need at least two price points for {normalized_benchmark}")

        portfolio_dates = [str(row.get("date") or "") for row in valid_portfolio]
        benchmark_dates = [str(row.get("date") or "") for row in benchmark_points]
        start_date = max(portfolio_dates[0], benchmark_dates[0])
        end_date = portfolio_dates[-1]
        if start_date >= end_date:
            raise ValueError("Need overlapping portfolio and benchmark history")

        window_portfolio = [
            row
            for row in valid_portfolio
            if start_date <= str(row.get("date") or "") <= end_date
        ]
        window_benchmark = [
            row
            for row in benchmark_points
            if start_date <= str(row.get("date") or "") <= end_date
        ]
        if len(window_portfolio) < 2 or len(window_benchmark) < 2:
            raise ValueError("Need at least two overlapping performance points")

        start_portfolio = window_portfolio[0]
        end_portfolio = window_portfolio[-1]
        start_value = safe_float(start_portfolio.get("market_value")) or 0.0
        end_value = safe_float(end_portfolio.get("market_value")) or 0.0
        end_cost_basis = safe_float(end_portfolio.get("cost_basis")) or 0.0
        if start_value <= 0:
            raise ValueError("Starting portfolio value must be positive")
        if end_cost_basis <= 0:
            raise ValueError("Current cost basis must be positive")

        benchmark_lookup = {
            str(row.get("date") or ""): safe_float(row.get("close")) or 0.0
            for row in window_benchmark
            if row.get("date") and safe_float(row.get("close")) is not None
        }
        benchmark_dates_sorted = sorted(benchmark_lookup)
        start_portfolio_date = str(start_portfolio.get("date") or start_date)
        end_portfolio_date = str(end_portfolio.get("date") or end_date)
        start_close = benchmark_close_on_or_before(
            benchmark_lookup,
            benchmark_dates_sorted,
            start_portfolio_date,
        ) or 0.0
        benchmark_as_of = next(
            (
                point_date
                for point_date in reversed(benchmark_dates_sorted)
                if point_date <= end_portfolio_date
            ),
            "",
        )
        end_close = benchmark_close_on_or_before(
            benchmark_lookup,
            benchmark_dates_sorted,
            end_portfolio_date,
        ) or 0.0
        if start_close <= 0:
            raise ValueError(f"Starting {normalized_benchmark} price must be positive")
        if end_close <= 0:
            raise ValueError(f"Ending {normalized_benchmark} price must be positive")

        benchmark_shares = 0.0
        prior_cost_basis = 0.0
        benchmark_series: list[dict[str, Any]] = []
        for row in window_portfolio:
            row_date = str(row.get("date") or "")
            if not row_date:
                continue
            close = benchmark_close_on_or_before(
                benchmark_lookup,
                benchmark_dates_sorted,
                row_date,
            )
            if close is None or close <= 0:
                continue
            cost_basis = safe_float(row.get("cost_basis")) or 0.0
            market_value = safe_float(row.get("market_value")) or 0.0
            cost_delta = cost_basis - prior_cost_basis
            if abs(cost_delta) > 1e-9:
                benchmark_shares += cost_delta / close
                prior_cost_basis = cost_basis
            benchmark_value_for_date = benchmark_shares * close
            benchmark_series.append(
                {
                    "date": row_date,
                    "market_value": round(market_value, 8),
                    "cost_basis": round(cost_basis, 8),
                    "benchmark_value": round(benchmark_value_for_date, 8),
                }
            )
        if len(benchmark_series) < 2:
            raise ValueError("Need at least two benchmark-equivalent points")

        distributions = 0.0
        for ticker, entries in transactions.items():
            for tx in entries:
                if not isinstance(tx, dict):
                    continue
                tx_date = str(tx.get("date") or "").strip()
                if not tx_date or not (start_portfolio_date < tx_date <= end_portfolio_date):
                    continue
                _, distribution = transaction_cash_flow_amount(tx)
                if distribution > 0:
                    distributions += distribution

        actual_terminal_value = end_value + distributions
        spy_window_return = (end_close / start_close) - 1.0
        benchmark_value = benchmark_series[-1]["benchmark_value"]
        actual_gain = actual_terminal_value - end_cost_basis
        benchmark_gain = benchmark_value - end_cost_basis
        excess_gain = actual_terminal_value - benchmark_value
        portfolio_return = actual_gain / end_cost_basis
        benchmark_return = benchmark_gain / end_cost_basis
        excess_return = portfolio_return - benchmark_return
        payout_base = max(excess_gain, 0.0)
        payout = payout_base * payout_share

        return {
            "range": range,
            "benchmark": normalized_benchmark,
            "mode": "matthew",
            "accounts": sorted(MATTHEW_COMPENSATION_ACCOUNTS),
            "share_pct": payout_share,
            "start_date": start_portfolio_date,
            "end_date": end_portfolio_date,
            "portfolio_start_value": start_value,
            "portfolio_end_value": end_value,
            "cost_basis": end_cost_basis,
            "portfolio_return": portfolio_return,
            "actual_gain": actual_gain,
            "distributions": distributions,
            "actual_terminal_value": actual_terminal_value,
            "benchmark_start_price": start_close,
            "benchmark_end_price": end_close,
            "benchmark_as_of": benchmark_as_of,
            "spy_window_return": spy_window_return,
            "benchmark_return": benchmark_return,
            "benchmark_equivalent_value": benchmark_value,
            "benchmark_gain": benchmark_gain,
            "excess_return": excess_return,
            "excess_gain": excess_gain,
            "payout_base": payout_base,
            "payout": payout,
            "series": benchmark_series,
            "portfolio_series_points": len(window_portfolio),
            "benchmark_points": len(window_benchmark),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/prices/import")
def import_price_history(
    body: PriceImportRequest,
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    if not PRICE_IMPORT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="PRICE_IMPORT_SECRET is not configured on the server",
        )

    if x_import_secret != PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid import secret")

    try:
        result = upsert_price_rows(
            [row.model_dump(mode="json") for row in body.rows],
            default_source="googlefinance",
        )
        invalidate_response_cache()
        return {
            "status": "ok",
            **result,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/prices/status")
def get_price_import_status(tickers: str = "") -> dict[str, Any]:
    ticker_list = [
        normalize_ticker(ticker)
        for ticker in str(tickers).split(",")
        if str(ticker).strip()
    ]
    return get_price_history_status(ticker_list or None)


@app.put("/api/tickers/portfolio", response_model=PortfolioConfig)
def add_portfolio_ticker(request: Request, body: PortfolioTickerUpsert) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        if body.shares > 0:
            holdings[ticker] = float(body.shares)
        else:
            holdings.pop(ticker, None)
        save_user_holdings(current_user_email(request), holdings)
        return PortfolioConfig.model_validate(get_limited_tickers_config(request))

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    tickers_map[ticker] = {
        "list": "portfolio",
        "shares": float(body.shares),
        "archived": False,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    holdings = load_holdings_overrides()
    holdings[ticker] = float(body.shares)
    save_holdings_overrides(holdings)

    append_portfolio_event(
        "add_portfolio",
        ticker,
        {"shares": float(body.shares)},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.put("/api/tickers/watchlist", response_model=PortfolioConfig)
def add_watchlist_ticker(body: WatchlistTickerUpsert) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    tickers_map[ticker] = {
        "list": "watchlist",
        "shares": None,
        "archived": False,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    append_portfolio_event(
        "add_watchlist",
        ticker,
        {},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.put("/api/tickers/archive", response_model=PortfolioConfig)
def archive_ticker(body: ArchiveTickerRequest) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    effective = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(effective.get("portfolio", []))
    portfolio_set = {row["ticker"] for row in portfolio_rows}
    watchlist_set = {normalize_ticker(t) for t in effective.get("watchlist", [])}

    if ticker not in portfolio_set and ticker not in watchlist_set:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in active tracking")

    previous_list = "portfolio" if ticker in portfolio_set else "watchlist"

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(ticker, {})
    tickers_map[ticker] = {
        "list": current.get("list") or previous_list,
        "shares": current.get("shares"),
        "archived": True,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    append_portfolio_event(
        "archive_ticker",
        ticker,
        {"previous_list": previous_list},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.put("/api/tickers/restore", response_model=PortfolioConfig)
def restore_ticker(body: RestoreTickerRequest) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    target_list = str(body.list).strip().lower()

    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if target_list not in {"portfolio", "watchlist"}:
        raise HTTPException(status_code=400, detail="Restore list must be portfolio or watchlist")

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(ticker, {})

    tickers_map[ticker] = {
        "list": target_list,
        "shares": current.get("shares"),
        "archived": False,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    append_portfolio_event(
        "restore_ticker",
        ticker,
        {"target_list": target_list},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.delete("/api/tickers/{ticker}", response_model=PortfolioConfig)
def remove_ticker(request: Request, ticker: str) -> PortfolioConfig:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        holdings.pop(normalized, None)
        save_user_holdings(current_user_email(request), holdings)
        return PortfolioConfig.model_validate(get_limited_tickers_config(request))

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(normalized, {})

    tickers_map[normalized] = {
        "list": current.get("list"),
        "shares": current.get("shares"),
        "archived": False,
        "removed": True,
    }
    save_tickers_overrides(overrides)

    holdings = load_holdings_overrides()
    if normalized in holdings:
        del holdings[normalized]
        save_holdings_overrides(holdings)

    append_portfolio_event(
        "remove_ticker",
        normalized,
        {},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.get("/api/accounts")
def get_transaction_accounts() -> dict[str, Any]:
    transactions = load_transactions()
    accounts_by_key: dict[str, str] = {}

    for entries in transactions.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            account = str(entry.get("account") or "").strip()
            if account:
                accounts_by_key.setdefault(account.casefold(), account)

    for allocation in load_manual_allocations():
        account = str(allocation.get("account") or "").strip()
        if account:
            accounts_by_key.setdefault(account.casefold(), account)

    accounts = sorted(accounts_by_key.values(), key=str.casefold)
    return {
        "count": len(accounts),
        "accounts": accounts,
    }


@app.get("/api/management")
def get_management_settings() -> dict[str, Any]:
    return build_management_snapshot()


@app.put("/api/management")
def update_management_settings(body: ManagementModesUpdate) -> dict[str, Any]:
    settings = load_management_modes()

    for update in body.updates:
        account = str(update.account or "").strip()
        ticker = normalize_ticker(update.ticker or "")
        mode = str(update.mode or "").strip().lower()

        if not account:
            raise HTTPException(status_code=400, detail="Account is required")
        if mode not in VALID_MANAGEMENT_MODES:
            raise HTTPException(
                status_code=400,
                detail="Mode must be managed, track, or excluded",
            )

        if ticker:
            key = management_position_key(account, ticker)
            account_default = settings["account_defaults"].get(
                account,
                automatic_account_mode(account),
            )
            if mode == account_default:
                settings["position_overrides"].pop(key, None)
            else:
                settings["position_overrides"][key] = mode
        else:
            settings["account_defaults"][account] = mode

    save_management_modes(settings)
    append_portfolio_event(
        "update_management_modes",
        "",
        {"updates": [update.model_dump(mode="json") for update in body.updates]},
    )
    return build_management_snapshot()


@app.put("/api/management/allocation")
def update_manual_allocation(body: ManualAllocationUpdate) -> dict[str, Any]:
    ticker = normalize_ticker(body.ticker)
    account = str(body.account or "").strip()
    mode = str(body.mode or "").strip().lower()
    shares = float(body.shares)

    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if not account or account == UNASSIGNED_ACCOUNT:
        raise HTTPException(status_code=400, detail="Choose a named account")
    if shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")
    if mode not in VALID_MANAGEMENT_MODES:
        raise HTTPException(
            status_code=400,
            detail="Mode must be managed, track, or excluded",
        )

    snapshot = build_management_snapshot()
    existing_allocations = load_manual_allocations()
    existing_shares = sum(
        float(row["shares"])
        for row in snapshot["positions"]
        if row["ticker"] == ticker
        and row["account"].casefold() == account.casefold()
        and row["source"] == "manual_allocation"
    )
    unassigned_shares = sum(
        float(row["shares"])
        for row in snapshot["positions"]
        if row["ticker"] == ticker and row["account"] == UNASSIGNED_ACCOUNT
    )
    if shares > unassigned_shares + existing_shares + 1e-8:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Only {unassigned_shares + existing_shares:.4f} residual "
                f"{ticker} shares are available"
            ),
        )

    allocations = [
        row
        for row in existing_allocations
        if not (
            row["ticker"] == ticker
            and row["account"].casefold() == account.casefold()
        )
    ]
    if shares > 0:
        allocations.append({"ticker": ticker, "account": account, "shares": shares})
    save_manual_allocations(allocations)

    settings = load_management_modes()
    settings["position_overrides"][management_position_key(account, ticker)] = mode
    save_management_modes(settings)

    append_portfolio_event(
        "update_manual_allocation",
        ticker,
        {
            "account": account,
            "shares": shares,
            "mode": mode,
        },
    )
    return build_management_snapshot()


@app.put("/api/accounts/merge")
def merge_accounts(
    body: AccountMergeRequest,
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    if not PRICE_IMPORT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="PRICE_IMPORT_SECRET is not configured on the server",
        )
    if x_import_secret != PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid import secret")

    try:
        result = merge_account_records(
            body.from_account,
            body.to_account,
            body.source_account_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    append_portfolio_event(
        "merge_accounts",
        "",
        result,
    )
    return {
        "status": "ok",
        **result,
    }


@app.post("/api/transactions/import")
def import_transactions(body: TransactionImportRequest) -> dict[str, Any]:
    if not body.files:
        raise HTTPException(status_code=400, detail="Choose at least one CSV file")

    parsed_rows, accounts, skipped = parse_fidelity_transaction_files(
        body.files,
        body.account_mappings,
    )
    transactions = load_transactions()
    known_fingerprints = existing_import_fingerprints(transactions)
    batch_fingerprints: set[str] = set()
    preview_rows: list[dict[str, Any]] = []
    importable_rows: list[dict[str, Any]] = []

    for row in parsed_rows:
        fingerprint = str(row.get("import_fingerprint") or "")
        is_duplicate = fingerprint in known_fingerprints or fingerprint in batch_fingerprints
        batch_fingerprints.add(fingerprint)

        preview_rows.append(
            {
                "ticker": row["ticker"],
                "date": row["date"],
                "type": row["type"],
                "shares": row["shares"],
                "price_per_share": row["price_per_share"],
                "fees": row["fees"],
                "account": row["account"],
                "source_account": row["source_account"],
                "source_file": row["source_file"],
                "status": (
                    "duplicate"
                    if is_duplicate
                    else "imported"
                    if body.commit
                    else "ready"
                ),
            },
        )

        if not is_duplicate:
            importable_rows.append(row)

    if body.commit:
        aliases = load_account_aliases()
        for account in accounts:
            key = str(account.get("key") or "").strip()
            nickname = str(account.get("nickname") or "").strip()
            if key and nickname:
                aliases[key] = nickname
        save_account_aliases(aliases)
        transactions, alias_updates = apply_account_aliases_to_transactions(
            transactions,
            aliases,
        )
        if alias_updates:
            save_transactions(transactions)

    if body.commit and importable_rows:
        for row in importable_rows:
            ticker = row["ticker"]
            stored_row = {key: value for key, value in row.items() if key != "ticker"}
            stored_row["id"] = uuid.uuid4().hex
            transactions.setdefault(ticker, []).append(stored_row)

        save_transactions(transactions)

        effective_config = get_effective_tickers_config()
        configured_tickers = {
            normalize_ticker(row.get("ticker") or "")
            for section in ("portfolio", "watchlist")
            for row in effective_config.get(section, [])
            if isinstance(row, dict)
        }
        missing_tickers = sorted(
            {
                normalize_ticker(row.get("ticker") or "")
                for row in importable_rows
                if normalize_ticker(row.get("ticker") or "") not in configured_tickers
            },
        )
        if missing_tickers:
            overrides = load_tickers_overrides()
            tickers_map = overrides.setdefault("tickers", {})
            for ticker in missing_tickers:
                tickers_map[ticker] = {
                    "list": "watchlist",
                    "shares": None,
                    "archived": False,
                    "removed": False,
                }
            save_tickers_overrides(overrides)

        append_portfolio_event(
            "import_transactions",
            "",
            {
                "imported": len(importable_rows),
                "duplicates_skipped": len(parsed_rows) - len(importable_rows),
                "files": [uploaded.name for uploaded in body.files],
                "accounts": sorted(
                    {
                        str(row.get("account") or "").strip()
                        for row in importable_rows
                        if str(row.get("account") or "").strip()
                    },
                ),
            },
        )

    return {
        "status": "imported" if body.commit else "preview",
        "summary": {
            "parsed": len(parsed_rows),
            "ready": len(importable_rows),
            "duplicates": len(parsed_rows) - len(importable_rows),
            "skipped": len(skipped),
            "imported": len(importable_rows) if body.commit else 0,
        },
        "accounts": accounts,
        "rows": preview_rows,
        "skipped": skipped,
    }


@app.get("/api/transactions/{ticker}")
def get_transactions_for_ticker(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    transactions = load_transactions()
    return {
        "ticker": normalized,
        "transactions": transactions.get(normalized, []),
    }


@app.post("/api/transactions/{ticker}")
def create_transaction_for_ticker(ticker: str, body: TransactionCreate) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    tx = validate_transaction_payload(body)
    tx["id"] = uuid.uuid4().hex

    transactions = load_transactions()
    entries = transactions.setdefault(normalized, [])
    entries.append(tx)
    save_transactions(transactions)

    append_portfolio_event(
        "create_transaction",
        normalized,
        {
            "transaction_id": tx["id"],
            "transaction_type": tx["type"],
            "shares": tx["shares"],
            "date": tx["date"],
            "account": tx.get("account") or "",
        },
    )

    return {
        "ticker": normalized,
        "transaction": tx,
        "transactions": entries,
    }


@app.put("/api/transactions/{ticker}/{transaction_id}")
def update_transaction_for_ticker(
    ticker: str,
    transaction_id: str,
    body: TransactionUpdate,
) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    transactions = load_transactions()
    entries = transactions.get(normalized, [])

    for idx, entry in enumerate(entries):
        if str(entry.get("id")) == str(transaction_id):
            updated = validate_transaction_payload(body)
            updated["id"] = str(transaction_id)
            for key in (
                "source",
                "source_file",
                "source_account",
                "source_account_key",
                "source_amount",
                "settlement_date",
                "import_fingerprint",
            ):
                if key in entry:
                    updated[key] = entry[key]
            entries[idx] = updated
            transactions[normalized] = entries
            save_transactions(transactions)

            append_portfolio_event(
                "update_transaction",
                normalized,
                {
                    "transaction_id": str(transaction_id),
                    "transaction_type": updated["type"],
                    "shares": updated["shares"],
                    "date": updated["date"],
                    "account": updated.get("account") or "",
                },
            )

            return {
                "ticker": normalized,
                "transaction": updated,
                "transactions": entries,
            }

    raise HTTPException(status_code=404, detail="Transaction not found")


@app.delete("/api/transactions/{ticker}/{transaction_id}")
def delete_transaction_for_ticker(ticker: str, transaction_id: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    transactions = load_transactions()
    entries = transactions.get(normalized, [])
    filtered = [entry for entry in entries if str(entry.get("id")) != str(transaction_id)]

    if len(filtered) == len(entries):
        raise HTTPException(status_code=404, detail="Transaction not found")

    transactions[normalized] = filtered
    save_transactions(transactions)

    append_portfolio_event(
        "delete_transaction",
        normalized,
        {
            "transaction_id": str(transaction_id),
        },
    )

    return {
        "ticker": normalized,
        "transactions": filtered,
    }


@app.get("/api/position/{ticker}")
def get_position_summary(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    transactions = load_transactions()
    entries = transactions.get(normalized, [])

    summary = compute_position_summary(entries)

    return {
        "ticker": normalized,
        "summary": summary,
        "transactions": entries,
    }


@app.get("/api/prices/history")
def get_price_history_endpoint(
    ticker: str,
    range: str = "3y",
) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    try:
        return get_price_history_response(normalized, range_key=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/prices/compare")
def get_price_comparison(
    tickers: str,
    range: str = "3y",
) -> dict[str, Any]:
    raw_tickers = [
        normalize_ticker(ticker)
        for ticker in str(tickers).split(",")
        if str(ticker).strip()
    ]

    try:
        return get_price_comparison_response(raw_tickers, range_key=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/portfolio/shares", response_model=PortfolioViewResponse)
def update_portfolio_shares(
    request: Request,
    body: PortfolioSharesUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    ticker = normalize_ticker(body.ticker)
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        if ticker not in holdings:
            raise HTTPException(status_code=404, detail=f"{ticker} not found in your portfolio")
        if body.shares > 0:
            holdings[ticker] = float(body.shares)
        else:
            holdings.pop(ticker, None)
        save_user_holdings(current_user_email(request), holdings)
        payload = build_portfolio_view_for_request(request, force_refresh=force_refresh)
        return PortfolioViewResponse.model_validate(payload)

    tickers_config = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_tickers = {row["ticker"] for row in portfolio_rows}

    if ticker not in portfolio_tickers:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in portfolio")

    overrides = load_holdings_overrides()
    overrides[ticker] = float(body.shares)
    save_holdings_overrides(overrides)

    ticker_overrides = load_tickers_overrides()
    tickers_map = ticker_overrides.setdefault("tickers", {})
    existing = tickers_map.get(ticker)
    if isinstance(existing, dict) and existing.get("list") == "portfolio":
        existing["shares"] = float(body.shares)
        tickers_map[ticker] = existing
        save_tickers_overrides(ticker_overrides)

    append_portfolio_event(
        "update_shares",
        ticker,
        {"shares": float(body.shares)},
    )

    payload = build_portfolio_views(
        get_effective_tickers_config(),
        load_scenario_inputs(),
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.put("/api/portfolio/controls", response_model=PortfolioViewResponse)
def update_portfolio_controls(
    body: PortfolioControlsUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    tickers_config = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_shares_map = {row["ticker"]: row["shares"] for row in portfolio_rows}

    scenario_inputs = load_scenario_inputs()
    previous_by_ticker = {
        str(item.ticker).strip().upper(): scenario_inputs.get(str(item.ticker).strip().upper(), {})
        for item in body.updates
    }
    updates = [item.model_dump(exclude_unset=True) for item in body.updates]
    scenario_inputs = apply_portfolio_controls(
        scenario_inputs,
        updates,
        portfolio_shares_map,
    )
    save_scenario_inputs(scenario_inputs)

    for item in updates:
        ticker = normalize_ticker(item.get("ticker", ""))
        if not ticker:
            continue

        current = scenario_inputs.get(ticker, {})
        previous = previous_by_ticker.get(ticker, {})
        old_control_values = {
            "show_in_holdings": (previous.get("display_rules") or {}).get("show_in_holdings"),
            "include_in_redistribution": (previous.get("redistribution_rules") or {}).get(
                "include_in_redistribution",
            ),
            "eligible_redistribution_shares": (previous.get("redistribution_rules") or {}).get(
                "eligible_redistribution_shares",
            ),
        }
        new_control_values = {
            "show_in_holdings": (current.get("display_rules") or {}).get("show_in_holdings"),
            "include_in_redistribution": (current.get("redistribution_rules") or {}).get(
                "include_in_redistribution",
            ),
            "eligible_redistribution_shares": (current.get("redistribution_rules") or {}).get(
                "eligible_redistribution_shares",
            ),
        }
        changes = build_change_set(
            old_control_values,
            new_control_values,
            CONTROL_CHANGE_LABELS,
        )
        if changes:
            append_portfolio_event(
                "update_controls",
                ticker,
                {"changes": changes},
            )

    payload = build_portfolio_views(
        tickers_config,
        scenario_inputs,
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio/view", response_model=PortfolioViewResponse)
def get_portfolio_view(request: Request, force_refresh: bool = False) -> PortfolioViewResponse:
    if has_full_access(request) and not force_refresh:
        cached = get_cached_response("portfolio_view")
        if cached is not None:
            return PortfolioViewResponse.model_validate(cached)

    payload = build_portfolio_view_for_request(request, force_refresh=force_refresh)
    if has_full_access(request):
        set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio", response_model=ScenarioInputsResponse)
def get_portfolio_scenarios() -> ScenarioInputsResponse:
    raw = load_scenario_inputs()
    scenarios = {
        ticker: serialize_ticker_scenario(state)
        for ticker, state in raw.items()
        if isinstance(state, dict)
    }
    return ScenarioInputsResponse(scenarios=scenarios)


@app.put("/api/portfolio", response_model=ScenarioInputsResponse)
def replace_portfolio_scenarios(body: ScenarioInputsResponse) -> ScenarioInputsResponse:
    payload = {
        ticker: scenario.model_dump(mode="json")
        for ticker, scenario in body.scenarios.items()
    }
    save_scenario_inputs(payload)
    return body


@app.get("/api/config/tickers", response_model=PortfolioConfig)
def get_tickers_config(request: Request) -> PortfolioConfig:
    return PortfolioConfig.model_validate(get_tickers_config_for_request(request))


@app.get("/api/stock/{ticker}", response_model=StockScenarioResponse)
def get_stock_scenario(ticker: str) -> StockScenarioResponse:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    if normalized not in raw or not isinstance(raw[normalized], dict):
        raise HTTPException(status_code=404, detail=f"No scenario found for {normalized}")
    return StockScenarioResponse(
        ticker=normalized,
        scenario=serialize_ticker_scenario(raw[normalized]),
    )


@app.put("/api/stock/{ticker}", response_model=StockScenarioResponse)
def upsert_stock_scenario(ticker: str, body: TickerScenarioInputs) -> StockScenarioResponse:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    existing = raw.get(normalized)
    payload = body.model_dump(mode="json")

    if isinstance(existing, dict):
        for key in ("redistribution_rules", "display_rules", "trade_rules"):
            if isinstance(existing.get(key), dict):
                payload[key] = existing[key]

    raw[normalized] = payload
    save_scenario_inputs(raw)

    changes = build_change_set(
        existing if isinstance(existing, dict) else None,
        payload,
        SCENARIO_CHANGE_LABELS,
    )
    if changes:
        append_portfolio_event(
            "update_assumptions",
            normalized,
            {"changes": changes},
        )
        try:
            snapshot_view = build_portfolio_views(
                get_effective_tickers_config(),
                raw,
                load_settings_dict(),
                management_shares=build_management_snapshot()["shares_by_mode"],
                position_summaries=build_position_summaries(),
                earnings_calendar=load_earnings_calendar(),
                reported_fundamentals=load_reported_fundamentals(),
                force_refresh=False,
            )
            snapshot_row = next(
                (
                    row
                    for section in ("portfolio", "watchlist")
                    for row in snapshot_view.get(section, [])
                    if isinstance(row, dict) and row.get("ticker") == normalized
                ),
                None,
            )
            append_assumption_snapshot(
                normalized,
                payload,
                snapshot_row,
                previous_scenario=existing if isinstance(existing, dict) else None,
                changes=changes,
            )
        except Exception:
            pass

    return StockScenarioResponse(
        ticker=normalized,
        scenario=serialize_ticker_scenario(payload),
    )


@app.delete("/api/stock/{ticker}")
def delete_stock_scenario(ticker: str) -> dict[str, str]:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    if normalized not in raw:
        raise HTTPException(status_code=404, detail=f"No scenario found for {normalized}")
    del raw[normalized]
    save_scenario_inputs(raw)
    return {"status": "deleted", "ticker": normalized}


@app.get("/api/settings", response_model=GlobalSettings)
def get_global_settings() -> GlobalSettings:
    return GlobalSettings.model_validate(load_settings_dict())


@app.put("/api/settings", response_model=GlobalSettings)
def update_global_settings(body: GlobalSettings) -> GlobalSettings:
    payload = body.model_dump(mode="json")
    save_settings_dict(payload)
    return GlobalSettings.model_validate(merge_global_settings(payload))


static_dir = BASE_DIR / "backend" / "static"
assets_dir = static_dir / "assets"
index_file = static_dir / "index.html"

if static_dir.exists():
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{rest_of_path:path}")
    async def serve_spa(rest_of_path: str):
        if index_file.exists():
            return FileResponse(
                str(index_file),
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        raise HTTPException(status_code=404, detail="Frontend not built")
