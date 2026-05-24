from __future__ import annotations

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
        "shares": shares,
        "market_value": market_value,
        "bear_price_y3": bear_summary.get("price_y3"),
        "base_price_y3": base_summary.get("price_y3"),
        "bull_price_y3": bull_summary.get("price_y3"),
        "bear_cagr_y3": bear_summary.get("cagr_y3"),
        "base_cagr_y3": base_summary.get("cagr_y3"),
        "bull_cagr_y3": bull_summary.get("cagr_y3"),
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
    force_refresh: bool = False,
) -> dict[str, Any]:
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_tickers = [row["ticker"] for row in portfolio_rows]
    portfolio_set = set(portfolio_tickers)
    watchlist = normalize_watchlist(tickers_config.get("watchlist", []), portfolio_set)
    all_tickers = sorted(set(portfolio_tickers).union(watchlist))

    portfolio_shares_map = {row["ticker"]: row["shares"] for row in portfolio_rows}
    bottom_pinned_tickers = settings.get("bottom_pinned_tickers", [])

    market_df = get_live_market_data(
        all_tickers,
        force_refresh=force_refresh,
        max_age_hours=12,
    ).copy()

    summary_rows: list[dict[str, Any]] = []
    for ticker in all_tickers:
        row_match = market_df[market_df["ticker"] == ticker]
        market_row = row_match.iloc[0].to_dict() if not row_match.empty else {"ticker": ticker}
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