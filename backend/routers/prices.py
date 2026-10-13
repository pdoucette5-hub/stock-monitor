"""Price history import, lookup and comparison, and intraday price sampling."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Header

from backend.config import INTRADAY_FINNHUB_API_KEY, PRICE_IMPORT_SECRET
from backend.intraday_store import add_intraday_samples, latest_session_prices
from backend.logic import safe_float
from backend.management_service import build_management_snapshot
from backend.price_store import (
    get_price_comparison_response,
    get_price_history_response,
    get_price_history_status,
    upsert_price_rows,
)
from backend.schemas import PriceImportRequest
from backend.state_store import invalidate_response_cache
from backend.utils import normalize_ticker


router = APIRouter()


@router.post("/api/prices/import")
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
        import_rows = [row.model_dump(mode="json") for row in body.rows]
        # GOOGLEFINANCE intermittently omits VENU. Keep the normal sheet feed for
        # every other ticker, but recover missing VENU closes from Nasdaq.
        try:
            from datetime import date, timedelta
            from backend.venu_price_fallback import fetch_venu_history, fetch_venu_latest_trade

            status = get_price_history_status(["VENU"])["tickers"]["VENU"]
            latest = status["latest_date"]
            since = date.fromisoformat(latest) - timedelta(days=1) if latest else date.today() - timedelta(days=30)
            sheet_dates = set()
            for row in import_rows:
                if str(row.get("ticker", "")).upper() != "VENU":
                    continue
                try:
                    if float(row.get("close")) > 0:
                        sheet_dates.add(str(row.get("date")))
                except (TypeError, ValueError):
                    continue
            import_rows.extend(
                row for row in fetch_venu_history(since)
                if row["date"] not in sheet_dates
            )
            quote = fetch_venu_latest_trade()
            if quote and quote["date"] not in sheet_dates:
                import_rows.append(quote)
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"VENU history fallback unavailable: {exc}")

        result = upsert_price_rows(
            import_rows,
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


@router.get("/api/prices/status")
def get_price_import_status(tickers: str = "") -> dict[str, Any]:
    ticker_list = [
        normalize_ticker(ticker)
        for ticker in str(tickers).split(",")
        if str(ticker).strip()
    ]
    return get_price_history_status(ticker_list or None)


@router.get("/api/prices/history")
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


@router.get("/api/prices/compare")
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
        if str(range).strip().lower() == "1d":
            price_series = latest_session_prices(raw_tickers)
            if any(len(points) >= 2 for points in price_series.values()):
                daily_fallback = get_price_comparison_response(raw_tickers, range_key="1d").get(
                    "price_series",
                    {},
                )
                price_series = {
                    ticker: (
                        points
                        if len(points) >= 2
                        else daily_fallback.get(ticker, [])
                    )
                    for ticker, points in price_series.items()
                }
                return {
                    "range": "1d",
                    "resolution": "5",
                    "tickers": raw_tickers,
                    "series": price_series,
                    "price_series": price_series,
                }
        return get_price_comparison_response(raw_tickers, range_key=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/prices/intraday/sample")
def sample_intraday_prices(
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    if not PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=500, detail="PRICE_IMPORT_SECRET is not configured")
    if x_import_secret != PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid import secret")

    shares_by_mode = build_management_snapshot()["shares_by_mode"]
    tickers = sorted(
        ticker
        for ticker, shares in shares_by_mode.items()
        if float(shares.get("managed", 0.0)) + float(shares.get("track", 0.0)) > 0
    )
    samples: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    def fetch_quote(ticker: str) -> dict[str, Any]:
        if ticker == "VENU":
            from backend.venu_price_fallback import fetch_venu_latest_trade

            quote = fetch_venu_latest_trade()
            if not quote:
                raise ValueError("Nasdaq did not return a VENU trade")
            timestamp = int(datetime.fromisoformat(str(quote["fetched_at"])).timestamp())
            return {"ticker": ticker, "timestamp": timestamp, "close": float(quote["close"])}
        if not INTRADAY_FINNHUB_API_KEY:
            raise ValueError("INTRADAY_FINNHUB_API_KEY is not configured")
        response = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": INTRADAY_FINNHUB_API_KEY},
            timeout=20,
        )
        response.raise_for_status()
        quote = response.json()
        return {
            "ticker": ticker,
            "timestamp": int(quote.get("t") or time.time()),
            "close": safe_float(quote.get("c")) or 0.0,
        }

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as executor:
        futures = {executor.submit(fetch_quote, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                sample = future.result()
                if sample["close"] > 0:
                    samples.append(sample)
            except Exception as exc:
                errors[ticker] = str(exc)

    result = add_intraday_samples(samples)
    return {"status": "ok", **result, "requested": len(tickers), "errors": errors}


@router.post("/api/prices/intraday/refresh")
def refresh_intraday_prices() -> dict[str, Any]:
    return sample_intraday_prices(x_import_secret=PRICE_IMPORT_SECRET)
