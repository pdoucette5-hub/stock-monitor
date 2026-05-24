from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.data_ingest import get_price_history
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
from backend.transactions_service import compute_position_summary

BASE_DIR = Path(__file__).resolve().parent.parent

SCENARIO_STATE_FILE = BASE_DIR / "cache" / "scenario_inputs.json"
GLOBAL_SETTINGS_FILE = BASE_DIR / "cache" / "global_settings.json"
HOLDINGS_OVERRIDES_FILE = BASE_DIR / "cache" / "holdings_overrides.json"
TICKERS_OVERRIDES_FILE = BASE_DIR / "cache" / "tickers_overrides.json"
TRANSACTIONS_FILE = BASE_DIR / "cache" / "transactions.json"
PORTFOLIO_EVENTS_FILE = BASE_DIR / "cache" / "portfolio_events.json"
TICKERS_FILE = BASE_DIR / "config" / "tickers.yaml"

SCENARIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
GLOBAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
HOLDINGS_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
TICKERS_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
TRANSACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
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
    notes: str | None = ""


class TransactionUpdate(BaseModel):
    date: str
    type: str
    shares: float
    price_per_share: float | None = None
    fees: float | None = 0.0
    notes: str | None = ""


VALID_TRANSACTION_TYPES = {
    "buy",
    "sell",
    "dividend",
    "split",
    "transfer_in",
    "transfer_out",
    "adjustment",
}


def load_json_file(path: Path, default_data: Any) -> Any:
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


def load_portfolio_events() -> list[dict[str, Any]]:
    raw = load_json_file(PORTFOLIO_EVENTS_FILE, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_portfolio_events(events: list[dict[str, Any]]) -> None:
    cleaned = [item for item in events if isinstance(item, dict)]
    save_json_file(PORTFOLIO_EVENTS_FILE, cleaned)


def append_portfolio_event(event_type: str, ticker: str, payload: dict[str, Any] | None = None) -> None:
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


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


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
    range: str = "1y",
) -> dict[str, Any]:
    try:
        transactions = load_transactions()
        tickers_config = get_effective_tickers_config()
        return build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=tickers_config,
            range_key=range,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    range: str = "1y",
    force_refresh: bool = False,
) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    try:
        return get_price_history(
            normalized,
            range_key=range,
            force_refresh=force_refresh,
            max_age_hours=24,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/prices/compare")
def get_price_comparison(
    tickers: str,
    range: str = "1y",
    force_refresh: bool = False,
) -> dict[str, Any]:
    raw_tickers = [
        normalize_ticker(ticker)
        for ticker in str(tickers).split(",")
        if str(ticker).strip()
    ]
    unique_tickers = []
    seen = set()
    for ticker in raw_tickers:
        if ticker and ticker not in seen:
            unique_tickers.append(ticker)
            seen.add(ticker)

    if not unique_tickers:
        raise HTTPException(status_code=400, detail="At least one ticker is required")

    try:
        results: dict[str, Any] = {}
        common_dates: set[str] | None = None

        for ticker in unique_tickers:
            payload = get_price_history(
                ticker,
                range_key=range,
                force_refresh=force_refresh,
                max_age_hours=24,
            )
            points = payload.get("points", []) or []
            valid_points = [
                {
                    "date": str(point.get("date")),
                    "close": float(point.get("close")),
                }
                for point in points
                if point.get("date") and point.get("close") is not None
            ]

            if not valid_points:
                results[ticker] = []
                common_dates = set() if common_dates is None else common_dates.intersection(set())
                continue

            date_set = {point["date"] for point in valid_points}
            common_dates = date_set if common_dates is None else common_dates.intersection(date_set)
            results[ticker] = valid_points

        common_dates = common_dates or set()

        normalized_series: dict[str, list[dict[str, float | str]]] = {}
        for ticker, points in results.items():
            filtered = [point for point in points if point["date"] in common_dates]
            filtered = sorted(filtered, key=lambda point: point["date"])

            if not filtered:
                normalized_series[ticker] = []
                continue

            base_close = filtered[0]["close"]
            if base_close == 0:
                normalized_series[ticker] = []
                continue

            normalized_series[ticker] = [
                {
                    "date": point["date"],
                    "close": round(point["close"], 8),
                    "normalized": round((point["close"] / base_close) * 100.0, 8),
                }
                for point in filtered
            ]

        return {
            "range": range,
            "tickers": unique_tickers,
            "series": normalized_series,
        }
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
    updates = [item.model_dump(exclude_unset=True) for item in body.updates]
    scenario_inputs = apply_portfolio_controls(
        scenario_inputs,
        updates,
        portfolio_shares_map,
    )
    save_scenario_inputs(scenario_inputs)

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
    raw[normalized] = body.model_dump(mode="json")
    save_scenario_inputs(raw)
    return StockScenarioResponse(ticker=normalized, scenario=body)


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
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Frontend not built")