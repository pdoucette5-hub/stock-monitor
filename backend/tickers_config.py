"""The effective ticker configuration for a request.

Combines config/tickers.yaml with saved overrides and holdings, builds the
per-user view for limited (view-only) accounts, and redacts private portfolio
data from responses sent to those accounts."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException, Request

from backend.auth import current_user_email, has_full_access
from backend.config import BASE_DIR, TICKERS_FILE
from backend.models import TickerScenarioInputs
from backend.portfolio_service import normalize_portfolio
from backend.state_store import (
    load_holdings_overrides,
    load_json_file,
    load_scenario_inputs,
    load_tickers_overrides,
    save_json_file,
)
from backend.utils import normalize_ticker


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
    with ThreadPoolExecutor(max_workers=2) as executor:
        tickers_future = executor.submit(load_tickers_overrides)
        holdings_future = executor.submit(load_holdings_overrides)
        tickers_overrides = tickers_future.result()
        holdings_overrides = holdings_future.result()
    with_ticker_overrides = apply_tickers_overrides_to_config(base, tickers_overrides)
    return apply_holdings_overrides_to_config(with_ticker_overrides, holdings_overrides)


def serialize_ticker_scenario(raw: dict[str, Any]) -> TickerScenarioInputs:
    return TickerScenarioInputs.model_validate(raw)


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
