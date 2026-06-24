from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

import pandas as pd

from backend.data_ingest import get_live_market_data
from backend.logic import (
    DEFAULT_GROWTH_PE_TABLE,
    SCENARIO_NAMES,
    action_sort_rank,
    build_action_queue,
    build_scenario_matrix,
    classify_action,
    ensure_display_rules,
    ensure_redistribution_rules,
    ensure_scenario_defaults,
    get_default_display_rules,
    get_default_redistribution_rules,
    get_scenario_defaults,
    is_bottom_pinned_ticker,
    safe_float,
    weighted_cagr,
)
from backend.price_store import get_latest_price_for_ticker, load_price_history_store


def _apply_price_store_fallback(market_row: dict[str, Any], ticker: str) -> dict[str, Any]:
    if safe_float(market_row.get("price")) is not None:
        return market_row

    latest_price = get_latest_price_for_ticker(ticker)
    if not latest_price:
        return market_row

    updated = dict(market_row)
    updated["price"] = latest_price["price"]
    updated["price_date"] = latest_price.get("date")
    updated["cache_source"] = latest_price.get("source") or "local-store"

    status = str(updated.get("status") or "")
    if not status or status.startswith("ERROR") or "price" in status.lower():
        updated["status"] = "OK: price from imported sheet data"

    return updated


def normalize_portfolio(portfolio_raw: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in portfolio_raw or []:
        if isinstance(item, str):
            normalized.append({"ticker": item.strip().upper(), "shares": None})
        elif isinstance(item, dict):
            normalized.append(
                {
                    "ticker": str(item.get("ticker", "")).strip().upper(),
                    "shares": safe_float(item.get("shares")),
                },
            )
    return [row for row in normalized if row["ticker"]]


def normalize_watchlist(watchlist_raw: Any, portfolio_set: set[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for item in watchlist_raw or []:
        ticker = str(item).strip().upper()
        if not ticker or ticker in portfolio_set or ticker in seen:
            continue
        seen.add(ticker)
        items.append(ticker)
    return items


def prepare_ticker_state(
    state: Optional[dict[str, Any]],
    market_row: dict[str, Any],
) -> dict[str, Any]:
    fetched_ttm_revenue = safe_float(market_row.get("ttm_revenue"))
    fetched_ttm_net_income = safe_float(market_row.get("net_income_ttm"))
    fetched_shares = safe_float(market_row.get("shares_outstanding"))
    fetched_revenue_growth = safe_float(market_row.get("revenue_growth_pct"))
    fetched_net_income_growth = safe_float(market_row.get("net_income_growth_pct"))

    latest_quarter_revenue = (
        fetched_ttm_revenue / 4.0 if fetched_ttm_revenue is not None else None
    )
    latest_quarter_net_income = (
        fetched_ttm_net_income / 4.0 if fetched_ttm_net_income is not None else None
    )

    scenario_defaults = get_scenario_defaults(
        fetched_revenue_growth,
        fetched_net_income_growth,
    )

    if not state:
        state = {
            "latest_quarter_revenue": latest_quarter_revenue,
            "latest_quarter_net_income": latest_quarter_net_income,
            "shares_outstanding": fetched_shares,
            "notes": "",
            "redistribution_rules": get_default_redistribution_rules(),
            "display_rules": get_default_display_rules(),
            "bull": scenario_defaults["bull"],
            "base": scenario_defaults["base"],
            "bear": scenario_defaults["bear"],
        }

    if safe_float(state.get("latest_quarter_revenue")) is None and latest_quarter_revenue is not None:
        state["latest_quarter_revenue"] = latest_quarter_revenue
    if safe_float(state.get("latest_quarter_net_income")) is None and latest_quarter_net_income is not None:
        state["latest_quarter_net_income"] = latest_quarter_net_income
    if safe_float(state.get("shares_outstanding")) is None and fetched_shares is not None:
        state["shares_outstanding"] = fetched_shares

    if safe_float(state.get("latest_quarter_revenue")) is None and safe_float(
        state.get("current_revenue"),
    ) is not None:
        state["latest_quarter_revenue"] = safe_float(state.get("current_revenue")) / 4.0
    if safe_float(state.get("latest_quarter_net_income")) is None and safe_float(
        state.get("current_net_income"),
    ) is not None:
        state["latest_quarter_net_income"] = safe_float(state.get("current_net_income")) / 4.0

    state.pop("current_revenue", None)
    state.pop("current_net_income", None)
    state["redistribution_rules"] = ensure_redistribution_rules(
        state.get("redistribution_rules"),
    )
    state["display_rules"] = ensure_display_rules(state.get("display_rules"))

    for scenario_name in SCENARIO_NAMES:
        state[scenario_name] = ensure_scenario_defaults(
            state.get(scenario_name),
            scenario_defaults[scenario_name],
        )

    return state


def _build_summary_row(
    ticker: str,
    market_row: dict[str, Any],
    state: dict[str, Any],
    settings: dict[str, Any],
    portfolio_shares: Optional[float],
    bottom_pinned_tickers: list[str],
) -> dict[str, Any]:
    growth_pe_table = settings.get("growth_pe_table", DEFAULT_GROWTH_PE_TABLE)
    current_price = safe_float(market_row.get("price"))

    scenario_summaries: dict[str, dict[str, Any]] = {}
    for scenario_name in SCENARIO_NAMES:
        scenario = state[scenario_name]
        _, scenario_summary = build_scenario_matrix(
            current_price=current_price,
            latest_quarter_revenue=state.get("latest_quarter_revenue"),
            latest_quarter_net_income=state.get("latest_quarter_net_income"),
            shares_outstanding=state.get("shares_outstanding"),
            rev_growth_rates=scenario["rev_growth_rates"],
            net_income_growth_rates=scenario["net_income_growth_rates"],
            durable_growth_view=scenario["durable_growth_view"],
            growth_weight_pct=scenario["growth_weight_pct"],
            growth_pe_table=growth_pe_table,
        )
        scenario_summaries[scenario_name] = scenario_summary or {}

    bear_summary = scenario_summaries["bear"]
    base_summary = scenario_summaries["base"]
    bull_summary = scenario_summaries["bull"]

    action = classify_action(
        bear_cagr=bear_summary.get("cagr_y3"),
        base_cagr=base_summary.get("cagr_y3"),
        bull_cagr=bull_summary.get("cagr_y3"),
        threshold_pct=settings.get("target_cagr_threshold_pct"),
        strong_buy_base_premium_pct=settings.get("strong_buy_base_premium_pct"),
        buy_base_premium_pct=settings.get("buy_base_premium_pct"),
        buy_bear_buffer_pct=settings.get("buy_bear_buffer_pct"),
        risk_base_shortfall_pct=settings.get("risk_base_shortfall_pct"),
        speculative_bull_premium_pct=settings.get("speculative_bull_premium_pct"),
    )

    confidence_values = [
        bear_summary.get("confidence_flag"),
        base_summary.get("confidence_flag"),
        bull_summary.get("confidence_flag"),
    ]
    confidence = (
        "OK"
        if all(x == "OK" for x in confidence_values)
        else "Review Assumptions"
    )

    redistribution_rules = ensure_redistribution_rules(state.get("redistribution_rules"))
    display_rules = ensure_display_rules(state.get("display_rules"))
    owned_shares = safe_float(portfolio_shares, 0.0)
    eligible_shares = safe_float(
        redistribution_rules.get("eligible_redistribution_shares"),
        owned_shares,
    )
    eligible_shares = min(max(eligible_shares or 0.0, 0.0), owned_shares or 0.0)

    shares = portfolio_shares
    market_value = None
    if shares is not None and current_price is not None:
        market_value = shares * current_price

    return {
        "ticker": ticker,
        "price": current_price,
        "price_date": market_row.get("price_date"),
        "ttm_revenue": market_row.get("ttm_revenue"),
        "net_income_ttm": market_row.get("net_income_ttm"),
        "prior_ttm_revenue": market_row.get("prior_ttm_revenue"),
        "prior_net_income_ttm": market_row.get("prior_net_income_ttm"),
        "revenue_growth_pct": market_row.get("revenue_growth_pct"),
        "net_income_growth_pct": market_row.get("net_income_growth_pct"),
        "shares": shares,
        "market_value": market_value,
        "bear_price_y3": bear_summary.get("price_y3"),
        "base_price_y3": base_summary.get("price_y3"),
        "bull_price_y3": bull_summary.get("price_y3"),
        "bear_cagr_y3": bear_summary.get("cagr_y3"),
        "base_cagr_y3": base_summary.get("cagr_y3"),
        "bull_cagr_y3": bull_summary.get("cagr_y3"),
        "current_pe": base_summary.get("current_pe"),
        "blended_future_pe": base_summary.get("blended_future_pe"),
        "weighted_cagr_y3": weighted_cagr(
            bear_summary.get("cagr_y3"),
            base_summary.get("cagr_y3"),
            bull_summary.get("cagr_y3"),
        ),
        "confidence": confidence,
        "action": action,
        "action_rank": action_sort_rank(action),
        "bottom_pinned": is_bottom_pinned_ticker(ticker, bottom_pinned_tickers),
        "status": market_row.get("status"),
        "cache_source": market_row.get("cache_source"),
        "show_in_holdings": display_rules["show_in_holdings"],
        "include_in_redistribution": redistribution_rules["include_in_redistribution"],
        "eligible_redistribution_shares": eligible_shares,
        "locked_shares": max((owned_shares or 0.0) - (eligible_shares or 0.0), 0.0),
    }


def _price_return_from_history(
    ticker: str,
    current_price: float | None,
    price_history: dict[str, list[dict[str, Any]]] | None,
) -> tuple[float | None, float | None, str | None]:
    if current_price is None or not price_history:
        return None, None, None

    rows = price_history.get(ticker, [])
    if len(rows) < 2:
        return None, None, None

    try:
        latest_date = pd.to_datetime(rows[-1]["date"]).date()
    except Exception:
        return None, None, None

    target_date = latest_date - timedelta(days=365)
    try:
        first_date = pd.to_datetime(rows[0]["date"]).date()
    except Exception:
        return None, None, None
    if first_date > latest_date - timedelta(days=300):
        return None, None, None

    prior_row = rows[0]
    for row in rows:
        try:
            row_date = pd.to_datetime(row.get("date")).date()
        except Exception:
            continue
        if row_date <= target_date:
            prior_row = row
        else:
            break

    try:
        prior_price = float(prior_row["close"])
    except (KeyError, TypeError, ValueError):
        return None, None, None

    if prior_price <= 0:
        return None, None, None

    return (current_price / prior_price) - 1.0, prior_price, prior_row.get("date")


def _positive_growth(current: Any, prior: Any) -> float | None:
    current_value = safe_float(current)
    prior_value = safe_float(prior)
    if current_value is None or prior_value is None or current_value <= 0 or prior_value <= 0:
        return None
    return (current_value / prior_value) - 1.0


def _stock_detail_projection_growth(state: dict[str, Any], key: str) -> float | None:
    # Match the one-year price return window with the base scenario's year-one growth.
    scenario = state.get("base")
    if not isinstance(scenario, dict):
        return None
    values = scenario.get(key)
    if not isinstance(values, list) or not values:
        return None
    value = safe_float(values[0])
    return None if value is None else value / 100.0


def _compression_opportunity_score(
    earnings_growth: float | None,
    revenue_growth: float | None,
    price_return: float | None,
    current_pe: float | None,
    prior_pe: float | None,
) -> float | None:
    if earnings_growth is None or price_return is None:
        return None

    raw_gap = earnings_growth - price_return
    if raw_gap <= 0:
        return raw_gap

    quality_multiplier = 1.0
    if revenue_growth is None:
        quality_multiplier *= 0.8
    elif revenue_growth < 0:
        quality_multiplier *= 0.45
    elif revenue_growth < 0.05:
        quality_multiplier *= 0.75

    if current_pe is None or current_pe <= 0:
        quality_multiplier *= 0.7

    starting_multiple = prior_pe if prior_pe is not None else current_pe
    starting_multiple_penalty = 0.0
    if starting_multiple is not None:
        starting_multiple_penalty = min(
            0.75,
            max(0.0, (starting_multiple - 40.0) / 80.0),
        )

    return raw_gap * quality_multiplier * (1.0 - starting_multiple_penalty)


def _sort_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df = df.sort_values(
        by=["bottom_pinned", "action_rank", "weighted_cagr_y3"],
        ascending=[True, True, False],
        na_position="last",
    )
    return df.to_dict(orient="records")


def _dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    cleaned = df.where(pd.notna(df), None)
    return cleaned.to_dict(orient="records")


def apply_portfolio_controls(
    scenario_inputs: dict[str, Any],
    updates: list[dict[str, Any]],
    portfolio_shares_map: dict[str, Optional[float]],
) -> dict[str, Any]:
    for item in updates:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker or ticker not in scenario_inputs or not isinstance(
            scenario_inputs[ticker],
            dict,
        ):
            continue

        state = scenario_inputs[ticker]

        if item.get("show_in_holdings") is not None:
            display_rules = ensure_display_rules(state.get("display_rules"))
            display_rules["show_in_holdings"] = bool(item["show_in_holdings"])
            state["display_rules"] = display_rules

        if (
            item.get("include_in_redistribution") is not None
            or item.get("eligible_redistribution_shares") is not None
        ):
            rules = ensure_redistribution_rules(state.get("redistribution_rules"))
            owned = safe_float(portfolio_shares_map.get(ticker), 0.0) or 0.0

            if item.get("include_in_redistribution") is not None:
                rules["include_in_redistribution"] = bool(item["include_in_redistribution"])

            if item.get("eligible_redistribution_shares") is not None:
                eligible = safe_float(item["eligible_redistribution_shares"], owned)
                rules["eligible_redistribution_shares"] = min(
                    max(eligible or 0.0, 0.0),
                    owned,
                )
            elif rules["include_in_redistribution"] and rules[
                "eligible_redistribution_shares"
            ] is None:
                rules["eligible_redistribution_shares"] = owned

            state["redistribution_rules"] = ensure_redistribution_rules(rules)

    return scenario_inputs


def build_portfolio_views(
    tickers_config: dict[str, Any],
    scenario_inputs: dict[str, Any],
    settings: dict[str, Any],
    management_shares: dict[str, dict[str, float]] | None = None,
    position_summaries: dict[str, dict[str, Any]] | None = None,
    price_history: dict[str, list[dict[str, Any]]] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_tickers = [row["ticker"] for row in portfolio_rows]
    portfolio_set = set(portfolio_tickers)
    watchlist = normalize_watchlist(tickers_config.get("watchlist", []), portfolio_set)
    all_tickers = sorted(set(portfolio_tickers).union(watchlist))

    portfolio_shares_map = {row["ticker"]: row["shares"] for row in portfolio_rows}
    bottom_pinned_tickers = settings.get("bottom_pinned_tickers", [])
    price_history = price_history if price_history is not None else load_price_history_store()

    market_df = get_live_market_data(
        all_tickers,
        force_refresh=force_refresh,
        max_age_hours=12,
    ).copy()

    summary_rows: list[dict[str, Any]] = []
    for ticker in all_tickers:
        row_match = market_df[market_df["ticker"] == ticker]
        market_row = row_match.iloc[0].to_dict() if not row_match.empty else {"ticker": ticker}
        market_row = _apply_price_store_fallback(market_row, ticker)
        raw_state = scenario_inputs.get(ticker)
        state = prepare_ticker_state(
            raw_state if isinstance(raw_state, dict) else None,
            market_row,
        )
        summary_rows.append(
            _build_summary_row(
                ticker,
                market_row,
                state,
                settings,
                portfolio_shares_map.get(ticker),
                bottom_pinned_tickers,
            ),
        )

    summary_df = pd.DataFrame(summary_rows)

    if summary_df.empty:
        failed_df = pd.DataFrame(columns=["ticker", "status", "cache_source"])
    else:
        failed_df = summary_df[
            ~summary_df["status"].astype(str).str.startswith("OK")
        ].copy()

    portfolio_summary_rows = [
        row
        for row in summary_rows
        if row["ticker"] in portfolio_set
    ]
    for row in portfolio_summary_rows:
        row["shares"] = portfolio_shares_map.get(row["ticker"])
        mode_shares = (management_shares or {}).get(row["ticker"], {})
        managed_shares = min(
            max(safe_float(mode_shares.get("managed"), row["shares"]) or 0.0, 0.0),
            max(safe_float(row["shares"], 0.0) or 0.0, 0.0),
        )
        track_shares = max(safe_float(mode_shares.get("track"), 0.0) or 0.0, 0.0)
        excluded_shares = max(
            safe_float(mode_shares.get("excluded"), 0.0) or 0.0,
            0.0,
        )
        row["managed_shares"] = managed_shares
        row["track_shares"] = track_shares
        row["excluded_shares"] = excluded_shares
        position_summary = (position_summaries or {}).get(row["ticker"], {})
        row["total_cost_basis"] = safe_float(
            position_summary.get("total_cost_basis"),
            None,
        )
        row["average_cost_per_share"] = safe_float(
            position_summary.get("average_cost_per_share"),
            None,
        )
        price_return_1y, prior_price_1y, prior_price_date_1y = _price_return_from_history(
            row["ticker"],
            safe_float(row.get("price")),
            price_history,
        )
        earnings_growth = _positive_growth(
            row.get("net_income_ttm"),
            row.get("prior_net_income_ttm"),
        )
        earnings_growth_source = "actual"
        if earnings_growth is None:
            earnings_growth = _stock_detail_projection_growth(
                state,
                "net_income_growth_rates",
            )
            earnings_growth_source = (
                "stock_detail_projection" if earnings_growth is not None else None
            )
        revenue_growth = _positive_growth(
            row.get("ttm_revenue"),
            row.get("prior_ttm_revenue"),
        )
        revenue_growth_source = "actual"
        if revenue_growth is None:
            revenue_growth = _stock_detail_projection_growth(
                state,
                "rev_growth_rates",
            )
            revenue_growth_source = (
                "stock_detail_projection" if revenue_growth is not None else None
            )
        multiple_change = None
        if (
            price_return_1y is not None
            and earnings_growth is not None
            and earnings_growth > -1
        ):
            multiple_change = ((1 + price_return_1y) / (1 + earnings_growth)) - 1

        prior_pe = None
        current_pe = safe_float(row.get("current_pe"))
        if current_pe is not None and multiple_change is not None and multiple_change > -1:
            prior_pe = current_pe / (1 + multiple_change)

        row["prior_price_1y"] = prior_price_1y
        row["prior_price_date_1y"] = prior_price_date_1y
        row["price_return_1y"] = price_return_1y
        row["earnings_growth_1y"] = earnings_growth
        row["earnings_growth_source"] = earnings_growth_source
        row["revenue_growth_1y"] = revenue_growth
        row["revenue_growth_source"] = revenue_growth_source
        row["multiple_change_1y"] = multiple_change
        row["prior_pe_1y"] = prior_pe
        row["compression_opportunity_score"] = _compression_opportunity_score(
            earnings_growth,
            revenue_growth,
            price_return_1y,
            current_pe,
            prior_pe,
        )
        row["management_mode"] = (
            "managed"
            if managed_shares > 0 and track_shares <= 0 and excluded_shares <= 0
            else "track"
            if track_shares > 0 and managed_shares <= 0 and excluded_shares <= 0
            else "excluded"
            if excluded_shares > 0 and managed_shares <= 0 and track_shares <= 0
            else "mixed"
        )

        configured_eligible = safe_float(
            row.get("eligible_redistribution_shares"),
            row["shares"],
        )
        row["eligible_redistribution_shares"] = min(
            max(configured_eligible or 0.0, 0.0),
            managed_shares,
        )
        row["include_in_redistribution"] = bool(
            row.get("include_in_redistribution", False) and managed_shares > 0,
        )
        row["locked_shares"] = max(
            (safe_float(row["shares"], 0.0) or 0.0)
            - row["eligible_redistribution_shares"],
            0.0,
        )

    watchlist_summary_rows = [
        row for row in summary_rows if row["ticker"] in watchlist
    ]

    portfolio_sorted = _sort_summary_rows(portfolio_summary_rows)
    watchlist_sorted = _sort_summary_rows(watchlist_summary_rows)

    action_queue_rows: list[dict[str, Any]] = []
    action_queue_summary: dict[str, Any] = {
        "total_portfolio_value": 0.0,
        "redistribution_pool_value": 0.0,
        "total_buy_dollars": 0.0,
        "total_trim_dollars": 0.0,
        "review_count": 0,
        "action_count": 0,
    }

    if portfolio_sorted:
        portfolio_df = pd.DataFrame(portfolio_sorted)
        action_queue_df, action_queue_summary = build_action_queue(
            portfolio_df,
            settings,
        )
        action_queue_rows = _dataframe_records(action_queue_df)

    data_issues = [
        {
            "ticker": row["ticker"],
            "status": row.get("status"),
            "cache_source": row.get("cache_source"),
        }
        for row in failed_df.to_dict(orient="records")
    ]

    return {
        "portfolio": portfolio_sorted,
        "watchlist": watchlist_sorted,
        "action_queue": action_queue_rows,
        "action_queue_summary": action_queue_summary,
        "metrics": {
            "portfolio_positions": len(portfolio_tickers),
            "watchlist_names": len(watchlist),
            "tracked_names": len(summary_rows),
            "data_issues": len(data_issues),
        },
        "data_issues": data_issues,
    }
