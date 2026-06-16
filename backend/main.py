from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
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
    "/api/performance/portfolio",
    "/api/portfolio/controls",
    "/api/portfolio/shares",
    "/api/sheets/tickers/sync",
    "/api/tickers/portfolio",
}

FULL_ACCESS_ONLY_PREFIXES = (
    "/api/position/",
    "/api/transactions",
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

    if role != "full" and (
        request.url.path in FULL_ACCESS_ONLY_EXACT_PATHS
        or any(request.url.path.startswith(prefix) for prefix in FULL_ACCESS_ONLY_PREFIXES)
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


SCENARIO_CHANGE_LABELS = {
    "latest_quarter_revenue": "Latest quarter revenue",
    "latest_quarter_net_income": "Latest quarter net income",
    "shares_outstanding": "Shares outstanding",
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

    for ticker, transaction_total in list(transaction_shares_by_ticker.items()):
        configured_total = configured_shares.get(ticker)
        if configured_total is None or transaction_total <= configured_total or transaction_total <= 0:
            continue
        scale = configured_total / transaction_total
        for position in positions:
            if position["ticker"] == ticker and position["source"] == "transactions":
                position["shares"] = float(position["shares"]) * scale
        transaction_shares_by_ticker[ticker] = configured_total

    allocations_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for allocation in manual_allocations:
        allocations_by_ticker.setdefault(allocation["ticker"], []).append(allocation)

    accounts.add(UNASSIGNED_ACCOUNT)
    for ticker, total_shares in configured_shares.items():
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
    payload = {
        "base": load_tickers_config(),
        "overrides": load_tickers_overrides(),
        "effective": get_effective_tickers_config(),
    }
    return payload if has_full_access(request) else redact_tickers_config_for_limited(payload)


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
def add_portfolio_ticker(body: PortfolioTickerUpsert) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

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
def remove_ticker(ticker: str) -> PortfolioConfig:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

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
    body: PortfolioSharesUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    ticker = normalize_ticker(body.ticker)
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

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
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio/view", response_model=PortfolioViewResponse)
def get_portfolio_view(request: Request, force_refresh: bool = False) -> PortfolioViewResponse:
    if not force_refresh:
        cached = get_cached_response("portfolio_view")
        if cached is not None:
            if not has_full_access(request):
                return PortfolioViewResponse.model_validate(
                    redact_portfolio_view_for_limited(cached),
                )
            return PortfolioViewResponse.model_validate(cached)

    payload = build_portfolio_views(
        get_effective_tickers_config(),
        load_scenario_inputs(),
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    if not has_full_access(request):
        payload = redact_portfolio_view_for_limited(payload)
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
    payload = get_effective_tickers_config()
    if not has_full_access(request):
        payload = redact_tickers_config_for_limited(payload)
    return PortfolioConfig.model_validate(payload)


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
