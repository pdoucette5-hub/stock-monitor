"""Portfolio view, shares and controls, stock scenarios, and global settings."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import portfolio_service
from backend.auth import current_user_email, has_full_access
from backend.config import CONTROL_CHANGE_LABELS, SCENARIO_CHANGE_LABELS
from backend.forecast_service import append_assumption_snapshot, build_change_set
from backend.logic import merge_global_settings
from backend.management_service import (
    build_management_snapshot,
    build_position_summaries,
)
from backend.models import (
    GlobalSettings,
    PortfolioControlsUpdate,
    PortfolioViewResponse,
    ScenarioInputsResponse,
    StockScenarioResponse,
    TickerScenarioInputs,
)
from backend.portfolio_service import (
    apply_portfolio_controls,
    build_portfolio_views,
    normalize_portfolio,
)
from backend.price_store import load_price_history_store
from backend.schemas import PortfolioSharesUpdate
from backend.state_store import (
    append_portfolio_event,
    get_cached_response,
    load_earnings_calendar,
    load_holdings_overrides,
    load_management_modes,
    load_manual_allocations,
    load_reported_fundamentals,
    load_scenario_inputs,
    load_settings_dict,
    load_state_bundle,
    load_tickers_overrides,
    load_transactions,
    save_holdings_overrides,
    save_scenario_inputs,
    save_settings_dict,
    save_tickers_overrides,
    set_cached_response,
)
from backend.tickers_config import (
    get_effective_tickers_config,
    get_tickers_config_for_request,
    load_user_holdings,
    save_user_holdings,
    serialize_ticker_scenario,
)
from backend.utils import normalize_ticker


router = APIRouter()


def build_portfolio_view_for_request(
    request: Request,
    force_refresh: bool = False,
) -> dict[str, Any]:
    request_started = time.monotonic()
    full_access = has_full_access(request)
    load_state_bundle()
    bundle_loaded_at = time.monotonic()

    # Initialize cloud credentials and load the state bundle before reading the
    # separate price-history object. Concurrent first-use clients can contend on
    # credential initialization in Cloud Run and turn two small downloads into a
    # 20+ second wait.
    state = {
        "config": get_tickers_config_for_request(request),
        "scenarios": load_scenario_inputs(),
        "settings": load_settings_dict(),
        "earnings": load_earnings_calendar(),
        "fundamentals": load_reported_fundamentals(),
    }
    if full_access:
        state.update(
            transactions=load_transactions(),
            management_modes=load_management_modes(),
            manual_allocations=load_manual_allocations(),
        )
    state_loaded_at = time.monotonic()
    state["price_history"] = load_price_history_store()
    prices_loaded_at = time.monotonic()

    position_summaries = None
    management_shares = None
    positions_done_at = management_done_at = prices_loaded_at
    if full_access:
        position_summaries = build_position_summaries(state["transactions"])
        positions_done_at = time.monotonic()
        management_shares = build_management_snapshot(
            transactions=state["transactions"],
            settings=state["management_modes"],
            manual_allocations=state["manual_allocations"],
            config=state["config"],
        )["shares_by_mode"]
        management_done_at = time.monotonic()

    payload = build_portfolio_views(
        state["config"],
        state["scenarios"],
        state["settings"],
        management_shares=management_shares,
        position_summaries=position_summaries,
        earnings_calendar=state["earnings"],
        reported_fundamentals=state["fundamentals"],
        price_history=state["price_history"],
        force_refresh=force_refresh,
    )
    finished_at = time.monotonic()
    print(
        "portfolio_view_timing",
        json.dumps(
            {
                "bundle": round(bundle_loaded_at - request_started, 3),
                "state": round(state_loaded_at - bundle_loaded_at, 3),
                "prices": round(prices_loaded_at - state_loaded_at, 3),
                "build": round(finished_at - prices_loaded_at, 3),
                "total": round(finished_at - request_started, 3),
                "detail": {
                    "positions": round(positions_done_at - prices_loaded_at, 3),
                    "management": round(management_done_at - positions_done_at, 3),
                    "transactions": len(state.get("transactions") or []),
                    **portfolio_service.LAST_BUILD_TIMING,
                },
            },
        ),
        flush=True,
    )
    return payload


@router.put("/api/portfolio/shares", response_model=PortfolioViewResponse)
def update_portfolio_shares(
    request: Request,
    body: PortfolioSharesUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    ticker = normalize_ticker(body.ticker)
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        if ticker not in holdings:
            raise HTTPException(status_code=404, detail=f"{ticker} not found in your portfolio")
        if body.shares > 0:
            holdings[ticker] = float(body.shares)
        else:
            holdings.pop(ticker, None)
        save_user_holdings(current_user_email(request), holdings)
        payload = build_portfolio_view_for_request(request, force_refresh=force_refresh)
        return PortfolioViewResponse.model_validate(payload)

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
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@router.put("/api/portfolio/controls", response_model=PortfolioViewResponse)
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
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@router.get("/api/portfolio/view", response_model=PortfolioViewResponse)
def get_portfolio_view(request: Request, force_refresh: bool = False) -> PortfolioViewResponse:
    if has_full_access(request) and not force_refresh:
        cached = get_cached_response("portfolio_view")
        if cached is not None:
            return PortfolioViewResponse.model_validate(cached)

    payload = build_portfolio_view_for_request(request, force_refresh=force_refresh)
    if has_full_access(request):
        set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@router.get("/api/portfolio", response_model=ScenarioInputsResponse)
def get_portfolio_scenarios() -> ScenarioInputsResponse:
    raw = load_scenario_inputs()
    scenarios = {
        ticker: serialize_ticker_scenario(state)
        for ticker, state in raw.items()
        if isinstance(state, dict)
    }
    return ScenarioInputsResponse(scenarios=scenarios)


@router.put("/api/portfolio", response_model=ScenarioInputsResponse)
def replace_portfolio_scenarios(body: ScenarioInputsResponse) -> ScenarioInputsResponse:
    payload = {
        ticker: scenario.model_dump(mode="json")
        for ticker, scenario in body.scenarios.items()
    }
    save_scenario_inputs(payload)
    return body


@router.get("/api/stock/{ticker}", response_model=StockScenarioResponse)
def get_stock_scenario(ticker: str) -> StockScenarioResponse:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    if normalized not in raw or not isinstance(raw[normalized], dict):
        raise HTTPException(status_code=404, detail=f"No scenario found for {normalized}")
    return StockScenarioResponse(
        ticker=normalized,
        scenario=serialize_ticker_scenario(raw[normalized]),
    )


@router.put("/api/stock/{ticker}", response_model=StockScenarioResponse)
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
        try:
            snapshot_view = build_portfolio_views(
                get_effective_tickers_config(),
                raw,
                load_settings_dict(),
                management_shares=build_management_snapshot()["shares_by_mode"],
                position_summaries=build_position_summaries(),
                earnings_calendar=load_earnings_calendar(),
                reported_fundamentals=load_reported_fundamentals(),
                force_refresh=False,
            )
            snapshot_row = next(
                (
                    row
                    for section in ("portfolio", "watchlist")
                    for row in snapshot_view.get(section, [])
                    if isinstance(row, dict) and row.get("ticker") == normalized
                ),
                None,
            )
            append_assumption_snapshot(
                normalized,
                payload,
                snapshot_row,
                previous_scenario=existing if isinstance(existing, dict) else None,
                changes=changes,
            )
        except Exception:
            pass

    return StockScenarioResponse(
        ticker=normalized,
        scenario=serialize_ticker_scenario(payload),
    )


@router.delete("/api/stock/{ticker}")
def delete_stock_scenario(ticker: str) -> dict[str, str]:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    if normalized not in raw:
        raise HTTPException(status_code=404, detail=f"No scenario found for {normalized}")
    del raw[normalized]
    save_scenario_inputs(raw)
    return {"status": "deleted", "ticker": normalized}


@router.get("/api/settings", response_model=GlobalSettings)
def get_global_settings() -> GlobalSettings:
    return GlobalSettings.model_validate(load_settings_dict())


@router.put("/api/settings", response_model=GlobalSettings)
def update_global_settings(body: GlobalSettings) -> GlobalSettings:
    payload = body.model_dump(mode="json")
    save_settings_dict(payload)
    return GlobalSettings.model_validate(merge_global_settings(payload))
