from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests


SHEETS_TICKER_PUSH_URL = os.getenv("SHEETS_TICKER_PUSH_URL", "")
SHEETS_TICKER_PUSH_SECRET = os.getenv("SHEETS_TICKER_PUSH_SECRET", "")

GOOGLEFINANCE_SYMBOL_OVERRIDES = {
    "DELL": "NYSE:DELL",
}


def _normalize_ticker(value: Any) -> str:
    return str(value).strip().upper()


def _googlefinance_symbol(ticker: str) -> str:
    normalized = _normalize_ticker(ticker)
    return GOOGLEFINANCE_SYMBOL_OVERRIDES.get(normalized, normalized)


def build_ticker_push_payload(tickers_config: dict[str, Any]) -> dict[str, Any]:
    portfolio_rows: list[dict[str, Any]] = []
    watchlist: list[str] = []

    for item in tickers_config.get("portfolio", []) or []:
        if isinstance(item, dict):
            ticker = _normalize_ticker(item.get("ticker", ""))
            if not ticker:
                continue
            shares = item.get("shares")
            try:
                shares = None if shares is None else float(shares)
            except (TypeError, ValueError):
                shares = None
            portfolio_rows.append({"ticker": ticker, "shares": shares})
        else:
            ticker = _normalize_ticker(item)
            if ticker:
                portfolio_rows.append({"ticker": ticker, "shares": None})

    portfolio_tickers = {row["ticker"] for row in portfolio_rows}
    for item in tickers_config.get("watchlist", []) or []:
        ticker = _normalize_ticker(item)
        if ticker and ticker not in portfolio_tickers:
            watchlist.append(ticker)

    active_tickers = sorted(portfolio_tickers.union(watchlist))
    ticker_rows = [
        {
            "ticker": ticker,
            "googlefinance_symbol": _googlefinance_symbol(ticker),
            "list": "portfolio" if ticker in portfolio_tickers else "watchlist",
            "shares": next(
                (row.get("shares") for row in portfolio_rows if row.get("ticker") == ticker),
                None,
            ),
        }
        for ticker in active_tickers
    ]

    return {
        "source": "stock-monitor",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": active_tickers,
        "ticker_rows": ticker_rows,
        "portfolio": sorted(portfolio_rows, key=lambda row: row["ticker"]),
        "watchlist": sorted(set(watchlist)),
    }


def push_tickers_to_sheet(tickers_config: dict[str, Any]) -> dict[str, Any]:
    if not SHEETS_TICKER_PUSH_URL:
        raise ValueError("SHEETS_TICKER_PUSH_URL is not configured on the server")

    payload = build_ticker_push_payload(tickers_config)
    if SHEETS_TICKER_PUSH_SECRET:
        payload["secret"] = SHEETS_TICKER_PUSH_SECRET

    response = requests.post(
        SHEETS_TICKER_PUSH_URL,
        json=payload,
        timeout=20,
    )
    response.raise_for_status()

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {"text": response.text}

    return {
        "status": "ok",
        "ticker_count": len(payload["tickers"]),
        "tickers": payload["tickers"],
        "sheet_response": response_payload,
    }
