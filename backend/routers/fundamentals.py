"""Portfolio events, forecast scorecard, reported fundamentals and earnings calendar endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from backend.auth import has_full_access
from backend.config import PRICE_IMPORT_SECRET
from backend.forecast_service import build_forecast_scorecard, forecast_history, record_quarterly_forecasts
from backend.fundamentals_service import (
    get_ttm_eps_history,
    refresh_earnings_calendar,
    refresh_reported_fundamentals,
)
from backend.portfolio_service import normalize_portfolio
from backend.schemas import EarningsCalendarRefreshRequest, FundamentalsRefreshRequest
from backend.state_store import (
    invalidate_response_cache,
    load_earnings_calendar,
    load_portfolio_events,
    load_reported_fundamentals,
)
from backend.tickers_config import (
    get_effective_tickers_config,
    redact_event_for_limited,
)
from backend.utils import normalize_ticker


router = APIRouter()


@router.get("/api/events")
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


@router.get("/api/forecasts")
def get_forecast_scorecard(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Forecast scorecard is private")
    return build_forecast_scorecard()


@router.post("/api/forecasts/quarterly-snapshot")
def save_quarterly_forecasts(x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Cloud Scheduler: save every ticker's forecast at the start of a quarter."""
    if not PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=500, detail="PRICE_IMPORT_SECRET is not configured")
    if x_import_secret != PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid import secret")
    return record_quarterly_forecasts()


@router.get("/api/forecasts/{ticker}/history")
def get_forecast_history(ticker: str, request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Forecast history is private")
    return {"ticker": normalize_ticker(ticker), "forecasts": forecast_history(ticker)}


@router.get("/api/fundamentals/{ticker}/eps-history")
def get_eps_history(ticker: str) -> dict[str, Any]:
    """Trailing-twelve-month EPS after each quarterly report (public SEC data)."""
    try:
        return get_ttm_eps_history(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # SEC unavailable: the chart just leaves the lines off.
        raise HTTPException(status_code=502, detail=f"SEC data unavailable: {exc}") from exc


@router.get("/api/fundamentals/reported")
def get_reported_fundamentals(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Reported fundamentals are private")
    return {"fundamentals": load_reported_fundamentals()}


@router.post("/api/fundamentals/reported/refresh")
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


@router.get("/api/earnings-calendar")
def get_earnings_calendar(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Earnings calendar is private")
    return {"calendar": load_earnings_calendar()}


@router.post("/api/earnings-calendar/refresh")
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
