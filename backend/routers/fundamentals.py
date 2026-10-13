"""Portfolio events, forecast scorecard, reported fundamentals and earnings calendar endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.auth import has_full_access
from backend.forecast_service import build_forecast_scorecard
from backend.fundamentals_service import (
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
