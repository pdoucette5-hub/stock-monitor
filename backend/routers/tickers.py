"""Ticker registry endpoints: portfolio, watchlist, archive, and the Google Sheets sync."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from backend.auth import current_user_email, has_full_access
from backend.models import PortfolioConfig
from backend.portfolio_service import normalize_portfolio
from backend.schemas import (
    ArchiveTickerRequest,
    PortfolioTickerUpsert,
    RestoreTickerRequest,
    WatchlistTickerUpsert,
)
from backend.sheets_service import push_tickers_to_sheet
from backend.state_store import (
    append_portfolio_event,
    load_holdings_overrides,
    load_tickers_overrides,
    save_holdings_overrides,
    save_tickers_overrides,
)
from backend.tickers_config import (
    apply_holdings_overrides_to_config,
    apply_tickers_overrides_to_config,
    get_effective_tickers_config,
    get_limited_tickers_config,
    get_tickers_config_for_request,
    load_tickers_config,
    load_user_holdings,
    redact_tickers_config_for_limited,
    save_user_holdings,
)
from backend.utils import normalize_ticker


router = APIRouter()


@router.get("/api/tickers")
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


@router.get("/api/tickers/shared")
def get_shared_ticker_registry() -> dict[str, Any]:
    return {
        "base": load_tickers_config(),
        "overrides": load_tickers_overrides(),
        "effective": get_effective_tickers_config(),
    }


@router.post("/api/sheets/tickers/sync")
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


@router.put("/api/tickers/portfolio", response_model=PortfolioConfig)
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


@router.put("/api/tickers/watchlist", response_model=PortfolioConfig)
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


@router.put("/api/tickers/archive", response_model=PortfolioConfig)
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


@router.put("/api/tickers/restore", response_model=PortfolioConfig)
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


@router.delete("/api/tickers/{ticker}", response_model=PortfolioConfig)
def remove_ticker(
    request: Request,
    ticker: str,
    background_tasks: BackgroundTasks,
) -> PortfolioConfig:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        holdings.pop(normalized, None)
        save_user_holdings(current_user_email(request), holdings)
        return PortfolioConfig.model_validate(get_limited_tickers_config(request))

    with ThreadPoolExecutor(max_workers=2) as executor:
        overrides_future = executor.submit(load_tickers_overrides)
        holdings_future = executor.submit(load_holdings_overrides)
        overrides = overrides_future.result()
        holdings = holdings_future.result()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(normalized, {})

    tickers_map[normalized] = {
        "list": current.get("list"),
        "shares": current.get("shares"),
        "archived": False,
        "removed": True,
    }
    holdings_changed = normalized in holdings
    if holdings_changed:
        del holdings[normalized]

    with ThreadPoolExecutor(max_workers=2) as executor:
        writes = [executor.submit(save_tickers_overrides, overrides)]
        if holdings_changed:
            writes.append(executor.submit(save_holdings_overrides, holdings))
        for write in writes:
            write.result()

    background_tasks.add_task(
        append_portfolio_event,
        "remove_ticker",
        normalized,
        {},
    )

    effective = apply_tickers_overrides_to_config(load_tickers_config(), overrides)
    effective = apply_holdings_overrides_to_config(effective, holdings)
    return PortfolioConfig.model_validate(effective)


@router.get("/api/config/tickers", response_model=PortfolioConfig)
def get_tickers_config(request: Request) -> PortfolioConfig:
    return PortfolioConfig.model_validate(get_tickers_config_for_request(request))
