"""Recover VENU prices when GOOGLEFINANCE omits this NYSE American ticker."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests


NASDAQ_HISTORY_URL = "https://api.nasdaq.com/api/quote/VENU/historical"
NASDAQ_INFO_URL = "https://api.nasdaq.com/api/quote/VENU/info"
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def fetch_venu_history(since: date, today: date | None = None) -> list[dict[str, Any]]:
    # The history endpoint only serves completed US trading days.
    market_today = today or datetime.now(ZoneInfo("America/New_York")).date()
    through = market_today - timedelta(days=1)
    if since >= through:
        return []

    response = requests.get(
        NASDAQ_HISTORY_URL,
        params={
            "assetclass": "stocks",
            "limit": 500,
            "fromdate": (since + timedelta(days=1)).isoformat(),
            "todate": market_today.isoformat(),
        },
        headers=NASDAQ_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status", {}).get("rCode") != 200:
        raise ValueError("Nasdaq did not return VENU history")
    rows = (payload.get("data") or {}).get("tradesTable") or {}
    result = []
    for row in rows.get("rows") or []:
        try:
            row_date = datetime.strptime(row["date"], "%m/%d/%Y").date()
            close = float(str(row["close"]).replace("$", "").replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        if since < row_date <= through and close > 0:
            result.append({"ticker": "VENU", "date": row_date.isoformat(), "close": close, "source": "nasdaq-history-fallback"})
    return sorted(result, key=lambda item: item["date"])


def fetch_venu_latest_trade() -> dict[str, Any] | None:
    response = requests.get(
        NASDAQ_INFO_URL,
        params={"assetclass": "stocks"},
        headers=NASDAQ_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    if data.get("symbol") != "VENU":
        raise ValueError("Nasdaq did not return a VENU quote")
    quote = data.get("primaryData") or {}
    try:
        traded_at = datetime.strptime(quote["lastTradeTimestamp"], "%b %d, %Y %I:%M %p ET")
        price = float(str(quote["lastSalePrice"]).replace("$", "").replace(",", ""))
    except (KeyError, TypeError, ValueError):
        return None
    market_today = datetime.now(ZoneInfo("America/New_York")).date()
    if traded_at.date() != market_today or price <= 0:
        return None
    return {
        "ticker": "VENU",
        "date": market_today.isoformat(),
        "close": price,
        "source": "nasdaq-last-trade",
        "fetched_at": traded_at.replace(tzinfo=ZoneInfo("America/New_York")).isoformat(),
    }
