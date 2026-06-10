from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
PORTFOLIO_EVENTS_FILE = BASE_DIR / "cache" / "portfolio_events.json"
TICKERS_FILE = BASE_DIR / "config" / "tickers.yaml"

PRICE_IMPORT_SECRET = os.getenv("PRICE_IMPORT_SECRET", "")
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
PORTFOLIO_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Stock Monitor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


VALID_TRANSACTION_TYPES = {
    "buy",
    "sell",
    "dividend",
    "split",
    "transfer_in",
    "transfer_out",
    "adjustment",
}


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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tickers")
def get_ticker_registry() -> dict[str, Any]:
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

    return {
        "count": len(events),
        "events": events,
    }


@app.get("/api/performance/portfolio")
def get_portfolio_performance(
    range: str = "3y",
    accounts: str = "",
) -> dict[str, Any]:
    try:
        transactions = load_transactions()
        tickers_config = get_effective_tickers_config()
        account_filter = [
            account.strip()
            for account in str(accounts).split(",")
            if account.strip()
        ]
        return build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=tickers_config,
            range_key=range,
            accounts=account_filter or None,
        )
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

    accounts = sorted(accounts_by_key.values(), key=str.casefold)
    return {
        "count": len(accounts),
        "accounts": accounts,
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
        force_refresh=force_refresh,
    )
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
        force_refresh=force_refresh,
    )
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio/view", response_model=PortfolioViewResponse)
def get_portfolio_view(force_refresh: bool = False) -> PortfolioViewResponse:
    payload = build_portfolio_views(
        get_effective_tickers_config(),
        load_scenario_inputs(),
        load_settings_dict(),
        force_refresh=force_refresh,
    )
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
def get_tickers_config() -> PortfolioConfig:
    return PortfolioConfig.model_validate(get_effective_tickers_config())


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
