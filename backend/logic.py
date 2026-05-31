from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd

FORECAST_YEARS = 3
QUARTERS_PER_YEAR = 4
SCENARIO_NAMES = ("bear", "base", "bull")

DEFAULT_GROWTH_PE_TABLE = [
    {"growth": 0.0, "pe": 9.0},
    {"growth": 3.0, "pe": 11.0},
    {"growth": 5.0, "pe": 14.0},
    {"growth": 8.0, "pe": 18.0},
    {"growth": 10.0, "pe": 22.0},
    {"growth": 12.0, "pe": 26.0},
    {"growth": 15.0, "pe": 32.0},
    {"growth": 18.0, "pe": 38.0},
    {"growth": 20.0, "pe": 43.0},
    {"growth": 25.0, "pe": 55.0},
    {"growth": 30.0, "pe": 68.0},
]

DEFAULT_GLOBAL_SETTINGS: dict[str, Any] = {
    "target_cagr_threshold_pct": 15.0,
    "strong_buy_base_premium_pct": 15.0,
    "buy_base_premium_pct": 3.0,
    "buy_bear_buffer_pct": 5.0,
    "risk_base_shortfall_pct": 5.0,
    "speculative_bull_premium_pct": 50.0,
    "portfolio_table_height": 800,
    "watchlist_table_height": 650,
    "growth_pe_table": DEFAULT_GROWTH_PE_TABLE,
    "bottom_pinned_tickers": [],
    "buy_hurdle_pct": 15.0,
    "max_position_weight_pct": 10.0,
    "min_position_weight_pct": 0.0,
    "rebalance_band_pct": 0.75,
    "rebalance_step_pct": 25.0,
    "min_trade_dollars": 1000.0,
}

DEFAULT_REDISTRIBUTION_RULES = {
    "include_in_redistribution": False,
    "eligible_redistribution_shares": None,
}

DEFAULT_DISPLAY_RULES = {
    "show_in_holdings": True,
}


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        parsed = float(value)
        if math.isnan(parsed):
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def annualized_return(
    current_price: Any,
    future_price: Any,
    years: Any,
) -> Optional[float]:
    current_price = safe_float(current_price)
    future_price = safe_float(future_price)
    years = safe_float(years)

    if current_price is None or future_price is None or years is None:
        return None
    if current_price <= 0 or future_price <= 0 or years <= 0:
        return None

    return (future_price / current_price) ** (1 / years) - 1


def annual_to_quarterly_growth(annual_growth_pct: Any) -> Optional[float]:
    annual_growth = safe_float(annual_growth_pct)
    if annual_growth is None:
        return None
    annual_decimal = annual_growth / 100.0
    if annual_decimal <= -1.0:
        return -1.0
    return (1.0 + annual_decimal) ** (1.0 / QUARTERS_PER_YEAR) - 1.0


def quarterly_run_rate_to_annual(value: Any) -> Optional[float]:
    value = safe_float(value)
    if value is None:
        return None
    return value * QUARTERS_PER_YEAR


def growth_to_pe_lookup(
    durable_growth_view_pct: Any,
    growth_pe_table: Optional[list[dict[str, Any]]],
) -> Optional[float]:
    g = safe_float(durable_growth_view_pct)
    if g is None:
        return None

    points: list[tuple[float, float]] = []
    for item in growth_pe_table or []:
        if not isinstance(item, dict):
            continue
        growth = safe_float(item.get("growth"))
        pe = safe_float(item.get("pe"))
        if growth is not None and pe is not None:
            points.append((growth, pe))

    if not points:
        points = [(x["growth"], x["pe"]) for x in DEFAULT_GROWTH_PE_TABLE]

    points = sorted(points, key=lambda x: x[0])

    if g <= points[0][0]:
        return points[0][1]
    if g >= points[-1][0]:
        return points[-1][1]

    for i in range(1, len(points)):
        g0, pe0 = points[i - 1]
        g1, pe1 = points[i]
        if g0 <= g <= g1:
            if g1 == g0:
                return pe1
            weight = (g - g0) / (g1 - g0)
            return pe0 + weight * (pe1 - pe0)
    return None


def confidence_flag(
    rev_growth_rates: list[Any],
    net_income_growth_rates: list[Any],
) -> str:
    rev_rates = [safe_float(x) for x in rev_growth_rates]
    ni_rates = [safe_float(x) for x in net_income_growth_rates]

    if any(v is None for v in rev_rates + ni_rates):
        return "Missing assumptions"

    return "OK"


def classify_action(
    bear_cagr: Any,
    base_cagr: Any,
    bull_cagr: Any,
    threshold_pct: Any,
    strong_buy_base_premium_pct: Any,
    buy_base_premium_pct: Any,
    buy_bear_buffer_pct: Any,
    risk_base_shortfall_pct: Any,
    speculative_bull_premium_pct: Any,
) -> str:
    bear = safe_float(bear_cagr)
    base = safe_float(base_cagr)
    bull = safe_float(bull_cagr)
    threshold = safe_float(threshold_pct)

    if bear is None or base is None or bull is None or threshold is None:
        return "Needs Input"

    threshold_dec = threshold / 100.0
    strong_buy_base_cutoff = (threshold + safe_float(strong_buy_base_premium_pct, 15.0)) / 100.0
    buy_base_cutoff = (threshold + safe_float(buy_base_premium_pct, 3.0)) / 100.0
    buy_bear_cutoff = (threshold - safe_float(buy_bear_buffer_pct, 5.0)) / 100.0
    risk_base_cutoff = (threshold - safe_float(risk_base_shortfall_pct, 5.0)) / 100.0
    speculative_bull_cutoff = (threshold + safe_float(speculative_bull_premium_pct, 50.0)) / 100.0

    if bear >= threshold_dec and base >= strong_buy_base_cutoff:
        return "Strong Buy"
    if base >= buy_base_cutoff and bear >= buy_bear_cutoff:
        return "Buy"
    if bull >= speculative_bull_cutoff and base > 0:
        return "Speculative Buy"
    if bear > 0 and base > threshold_dec:
        return "Hold / Watch"
    if bear < 0 and base <= risk_base_cutoff:
        return "High Risk / Review"
    if bear < 0 and base < threshold_dec:
        return "Consider Trim"
    return "Hold / Watch"


def weighted_cagr(bear_cagr: Any, base_cagr: Any, bull_cagr: Any) -> Optional[float]:
    bear = safe_float(bear_cagr)
    base = safe_float(base_cagr)
    bull = safe_float(bull_cagr)
    if bear is None or base is None or bull is None:
        return None
    return 0.10 * bear + 0.80 * base + 0.10 * bull


def action_sort_rank(val: str) -> int:
    rank_map = {
        "Strong Buy": 0,
        "Buy": 1,
        "Speculative Buy": 2,
        "Hold / Watch": 3,
        "High Risk / Review": 4,
        "Consider Trim": 5,
        "Needs Input": 6,
    }
    return rank_map.get(val, 999)


def is_bottom_pinned_ticker(ticker: str, bottom_pinned_tickers: Optional[list[str]]) -> bool:
    ticker = str(ticker).strip().upper()
    return ticker in {str(x).strip().upper() for x in (bottom_pinned_tickers or [])}


def get_default_redistribution_rules() -> dict[str, Any]:
    return dict(DEFAULT_REDISTRIBUTION_RULES)


def get_default_display_rules() -> dict[str, Any]:
    return dict(DEFAULT_DISPLAY_RULES)


def ensure_display_rules(existing_rules: Any) -> dict[str, Any]:
    rules = get_default_display_rules()
    if isinstance(existing_rules, dict):
        rules.update(existing_rules)
    rules["show_in_holdings"] = bool(rules.get("show_in_holdings", True))
    return rules


def ensure_redistribution_rules(existing_rules: Any) -> dict[str, Any]:
    rules = get_default_redistribution_rules()
    if isinstance(existing_rules, dict):
        rules.update(existing_rules)
    rules["include_in_redistribution"] = bool(rules.get("include_in_redistribution", False))
    eligible = safe_float(rules.get("eligible_redistribution_shares"))
    if eligible is not None and eligible < 0:
        eligible = 0.0
    rules["eligible_redistribution_shares"] = eligible
    return rules


def ensure_scenario_defaults(existing: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    scenario = dict(existing or {})
    scenario["rev_growth_rates"] = list(
        scenario.get("rev_growth_rates", defaults["rev_growth_rates"]),
    )[:FORECAST_YEARS]
    scenario["net_income_growth_rates"] = list(
        scenario.get("net_income_growth_rates", defaults["net_income_growth_rates"]),
    )[:FORECAST_YEARS]

    while len(scenario["rev_growth_rates"]) < FORECAST_YEARS:
        scenario["rev_growth_rates"].append(defaults["rev_growth_rates"][-1])
    while len(scenario["net_income_growth_rates"]) < FORECAST_YEARS:
        scenario["net_income_growth_rates"].append(defaults["net_income_growth_rates"][-1])

    scenario["durable_growth_view"] = safe_float(
        scenario.get("durable_growth_view", scenario.get("terminal_growth")),
        defaults["durable_growth_view"],
    )
    scenario["growth_weight_pct"] = safe_float(
        scenario.get("growth_weight_pct"),
        defaults["growth_weight_pct"],
    )
    scenario.pop("terminal_growth", None)
    return scenario


def get_scenario_defaults(
    fetched_revenue_growth: Optional[float],
    fetched_net_income_growth: Optional[float],
) -> dict[str, dict[str, Any]]:
    base_rev_growth = fetched_revenue_growth if fetched_revenue_growth is not None else 8.00
    base_net_income_growth = (
        fetched_net_income_growth if fetched_net_income_growth is not None else 10.00
    )
    bull_rev_growth = base_rev_growth * 1.20
    bull_net_income_growth = base_net_income_growth * 1.20
    bear_rev_growth = base_rev_growth * 0.80
    bear_net_income_growth = base_net_income_growth * 0.80

    return {
        "bull": {
            "rev_growth_rates": [bull_rev_growth] * FORECAST_YEARS,
            "net_income_growth_rates": [bull_net_income_growth] * FORECAST_YEARS,
            "durable_growth_view": 20.00,
            "growth_weight_pct": 75.00,
        },
        "base": {
            "rev_growth_rates": [base_rev_growth] * FORECAST_YEARS,
            "net_income_growth_rates": [base_net_income_growth] * FORECAST_YEARS,
            "durable_growth_view": 12.00,
            "growth_weight_pct": 60.00,
        },
        "bear": {
            "rev_growth_rates": [bear_rev_growth] * FORECAST_YEARS,
            "net_income_growth_rates": [bear_net_income_growth] * FORECAST_YEARS,
            "durable_growth_view": 5.00,
            "growth_weight_pct": 35.00,
        },
    }


def build_scenario_matrix(
    current_price: Any,
    latest_quarter_revenue: Any,
    latest_quarter_net_income: Any,
    shares_outstanding: Any,
    rev_growth_rates: list[Any],
    net_income_growth_rates: list[Any],
    durable_growth_view: Any,
    growth_weight_pct: Any,
    growth_pe_table: Optional[list[dict[str, Any]]],
) -> tuple[Optional[pd.DataFrame], Optional[dict[str, Any]]]:
    current_price = safe_float(current_price)
    latest_quarter_revenue = safe_float(latest_quarter_revenue)
    latest_quarter_net_income = safe_float(latest_quarter_net_income)
    shares_outstanding = safe_float(shares_outstanding)

    if (
        latest_quarter_revenue is None
        or latest_quarter_net_income is None
        or shares_outstanding is None
    ):
        return None, None
    if latest_quarter_revenue <= 0 or shares_outstanding <= 0:
        return None, None

    current_annual_revenue = quarterly_run_rate_to_annual(latest_quarter_revenue)
    current_annual_net_income = quarterly_run_rate_to_annual(latest_quarter_net_income)
    current_margin = (
        ((current_annual_net_income / current_annual_revenue) * 100.0)
        if current_annual_revenue
        else None
    )
    current_eps = (
        current_annual_net_income / shares_outstanding if shares_outstanding else None
    )

    revenue_by_year = [current_annual_revenue]
    net_income_by_year = [current_annual_net_income]
    margin_by_year = [current_margin]
    eps_by_year = [current_eps]

    quarter_revenue = latest_quarter_revenue
    quarter_net_income = latest_quarter_net_income

    for year_idx in range(FORECAST_YEARS):
        rev_q_growth = annual_to_quarterly_growth(rev_growth_rates[year_idx])
        ni_q_growth = annual_to_quarterly_growth(net_income_growth_rates[year_idx])

        projected_rev_quarters: list[float] = []
        projected_ni_quarters: list[float] = []
        for _ in range(QUARTERS_PER_YEAR):
            quarter_revenue = quarter_revenue * (1 + rev_q_growth)
            quarter_net_income = quarter_net_income * (1 + ni_q_growth)
            projected_rev_quarters.append(quarter_revenue)
            projected_ni_quarters.append(quarter_net_income)

        annual_revenue = sum(projected_rev_quarters)
        annual_net_income = sum(projected_ni_quarters)
        annual_margin = (
            ((annual_net_income / annual_revenue) * 100.0) if annual_revenue else None
        )
        annual_eps = annual_net_income / shares_outstanding if shares_outstanding else None

        revenue_by_year.append(annual_revenue)
        net_income_by_year.append(annual_net_income)
        margin_by_year.append(annual_margin)
        eps_by_year.append(annual_eps)

    current_pe = None
    if current_price is not None and current_eps is not None and current_eps > 0:
        current_pe = current_price / current_eps

    growth_implied_pe = growth_to_pe_lookup(durable_growth_view, growth_pe_table)

    weight_pct = safe_float(growth_weight_pct, 0.0)
    weight_pct = min(max(weight_pct or 0.0, 0.0), 100.0)
    growth_weight = weight_pct / 100.0
    current_pe_weight = 1.0 - growth_weight

    blended_future_pe = None
    if growth_implied_pe is not None and current_pe is not None:
        blended_future_pe = growth_weight * growth_implied_pe + current_pe_weight * current_pe
    elif growth_implied_pe is not None:
        blended_future_pe = growth_implied_pe
    elif current_pe is not None:
        blended_future_pe = current_pe

    price_y3 = (
        eps_by_year[-1] * blended_future_pe
        if eps_by_year[-1] is not None and blended_future_pe is not None
        else None
    )
    cagr_y3 = annualized_return(current_price, price_y3, FORECAST_YEARS)

    df = pd.DataFrame(
        {
            "Current": [
                revenue_by_year[0],
                None,
                net_income_by_year[0],
                None,
                margin_by_year[0],
                eps_by_year[0],
                current_pe,
                None,
                None,
                None,
                None,
                None,
                None,
            ],
            "Year 1": [
                revenue_by_year[1],
                safe_float(rev_growth_rates[0]),
                net_income_by_year[1],
                safe_float(net_income_growth_rates[0]),
                margin_by_year[1],
                eps_by_year[1],
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ],
            "Year 2": [
                revenue_by_year[2],
                safe_float(rev_growth_rates[1]),
                net_income_by_year[2],
                safe_float(net_income_growth_rates[1]),
                margin_by_year[2],
                eps_by_year[2],
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ],
            "Year 3": [
                revenue_by_year[3],
                safe_float(rev_growth_rates[2]),
                net_income_by_year[3],
                safe_float(net_income_growth_rates[2]),
                margin_by_year[3],
                eps_by_year[3],
                None,
                durable_growth_view,
                growth_weight_pct,
                growth_implied_pe,
                current_pe,
                blended_future_pe,
                price_y3,
            ],
        },
        index=[
            "Revenue",
            "Rev Growth",
            "Net Income",
            "Net Inc. / EPS Growth",
            "Net Margin",
            "EPS",
            "Current PE",
            "Durable Growth View",
            "Weight on Growth View",
            "Growth-Implied Future PE",
            "Current PE Anchor",
            "Blended Future PE",
            "Price (Y3)",
        ],
    )
    cagr_row = pd.DataFrame(
        {"Current": [None], "Year 1": [None], "Year 2": [None], "Year 3": [cagr_y3]},
        index=["CAGR (3Y)"],
    )
    df = pd.concat([df, cagr_row])

    summary = {
        "price_y3": price_y3,
        "cagr_y3": cagr_y3,
        "growth_implied_pe": growth_implied_pe,
        "current_pe": current_pe,
        "blended_future_pe": blended_future_pe,
        "confidence_flag": confidence_flag(rev_growth_rates, net_income_growth_rates),
        "annualized_revenue": current_annual_revenue,
        "annualized_net_income": current_annual_net_income,
        "annualized_eps": current_eps,
    }
    return df, summary


def compute_target_weights(
    portfolio_summary: pd.DataFrame,
    settings: dict[str, Any],
) -> pd.DataFrame:
    df = portfolio_summary.copy()
    max_weight = safe_float(settings.get("max_position_weight_pct"), 10.0) / 100.0
    min_weight = safe_float(settings.get("min_position_weight_pct"), 0.0) / 100.0
    buy_hurdle = safe_float(settings.get("buy_hurdle_pct"), 15.0) / 100.0

    base_scores = {
        "Strong Buy": 5.0,
        "Buy": 3.0,
        "Speculative Buy": 2.0,
        "Hold / Watch": 1.0,
        "High Risk / Review": 0.5,
        "Consider Trim": 0.25,
        "Needs Input": 0.0,
    }

    include_mask = df["include_in_redistribution"].fillna(False).astype(bool)
    scores: list[float] = []
    for _, row in df.iterrows():
        action = row.get("action")
        wcagr = safe_float(row.get("weighted_cagr_y3"))
        confidence = str(row.get("confidence", ""))
        include = bool(row.get("include_in_redistribution", False))

        if not include or confidence != "OK" or wcagr is None:
            scores.append(0.0)
            continue

        base_score = base_scores.get(action, 0.0)
        if wcagr < buy_hurdle and action in {"Strong Buy", "Buy", "Speculative Buy"}:
            base_score = max(base_score - 1.0, 0.0)
        rank_boost = max(wcagr, 0.0) * 2.0
        scores.append(max(base_score + rank_boost, 0.0))

    df["target_score"] = scores
    df["target_weight"] = 0.0

    total_score = df.loc[include_mask, "target_score"].sum()
    if total_score > 0:
        df.loc[include_mask, "target_weight"] = (
            df.loc[include_mask, "target_score"] / total_score
        )
        df.loc[include_mask, "target_weight"] = df.loc[include_mask, "target_weight"].clip(
            lower=min_weight,
            upper=max_weight,
        )
        clipped_sum = df.loc[include_mask, "target_weight"].sum()
        if clipped_sum > 0:
            df.loc[include_mask, "target_weight"] = (
                df.loc[include_mask, "target_weight"] / clipped_sum
            )

    df.loc[~include_mask, "target_weight"] = 0.0
    return df


def build_action_queue(
    portfolio_summary: pd.DataFrame,
    settings: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = portfolio_summary.copy()
    if df.empty:
        return df, {}

    df["market_value"] = df["market_value"].fillna(0.0)
    total_portfolio_value = df["market_value"].sum()

    if total_portfolio_value <= 0:
        return pd.DataFrame(), {
            "total_portfolio_value": 0.0,
            "redistribution_pool_value": 0.0,
            "total_buy_dollars": 0.0,
            "total_trim_dollars": 0.0,
            "review_count": 0,
            "action_count": 0,
        }

    df["current_weight"] = df["market_value"] / total_portfolio_value
    df["eligible_redistribution_shares"] = df.apply(
        lambda r: min(
            max(
                safe_float(r["eligible_redistribution_shares"], safe_float(r["shares"], 0.0)),
                0.0,
            ),
            max(safe_float(r["shares"], 0.0), 0.0),
        ),
        axis=1,
    )
    df["eligible_redistribution_value"] = df.apply(
        lambda r: safe_float(r["eligible_redistribution_shares"], 0.0)
        * safe_float(r["price"], 0.0),
        axis=1,
    )

    included_mask = df["include_in_redistribution"].fillna(False).astype(bool)
    redistribution_pool_value = df.loc[included_mask, "eligible_redistribution_value"].sum()

    df = compute_target_weights(df, settings)

    rebalance_band = safe_float(settings.get("rebalance_band_pct"), 0.75) / 100.0
    rebalance_step = safe_float(settings.get("rebalance_step_pct"), 25.0) / 100.0
    rebalance_step = min(max(rebalance_step, 0.0), 1.0)
    min_trade_dollars = safe_float(settings.get("min_trade_dollars"), 1000.0)

    df["current_eligible_weight"] = 0.0
    if redistribution_pool_value > 0:
        df.loc[included_mask, "current_eligible_weight"] = (
            df.loc[included_mask, "eligible_redistribution_value"] / redistribution_pool_value
        )

    df["target_eligible_value"] = df["target_weight"] * redistribution_pool_value
    df["dollar_trade_unconstrained"] = (
        df["target_eligible_value"] - df["eligible_redistribution_value"]
    )
    df["dollar_trade_step"] = df["dollar_trade_unconstrained"] * rebalance_step
    df["shares_trade_unconstrained"] = df.apply(
        lambda r: (r["dollar_trade_step"] / r["price"])
        if safe_float(r["price"]) not in (None, 0)
        else None,
        axis=1,
    )

    action_list: list[str] = []
    reason_list: list[str] = []
    shares_trade_list: list[float] = []
    dollar_trade_list: list[float] = []
    target_weight_effective_list: list[Optional[float]] = []

    for _, row in df.iterrows():
        include = bool(row.get("include_in_redistribution", False))
        confidence = str(row.get("confidence", ""))
        wcagr = safe_float(row.get("weighted_cagr_y3"))
        price = safe_float(row.get("price"))
        eligible_shares = safe_float(row.get("eligible_redistribution_shares"), 0.0)
        current_eligible_weight = safe_float(row.get("current_eligible_weight"), 0.0)
        target_weight = safe_float(row.get("target_weight"), 0.0)
        desired_shares_trade = safe_float(row.get("shares_trade_unconstrained"), 0.0)

        if not include:
            action_list.append("Excluded")
            reason_list.append("Not included in redistribution")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(None)
            continue

        if confidence != "OK":
            action_list.append("Review")
            reason_list.append("Confidence not OK")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        if price is None or wcagr is None or redistribution_pool_value <= 0:
            action_list.append("Review")
            reason_list.append("Missing valuation inputs")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        if abs(target_weight - current_eligible_weight) < rebalance_band:
            action_list.append("Hold")
            reason_list.append("Within rebalance band")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        constrained_shares_trade = desired_shares_trade
        capped_reason = None
        if constrained_shares_trade < -eligible_shares:
            constrained_shares_trade = -eligible_shares
            capped_reason = "Limited by eligible redistribution shares"

        constrained_dollars = constrained_shares_trade * price
        effective_weight = current_eligible_weight + (
            constrained_dollars / redistribution_pool_value
            if redistribution_pool_value > 0
            else 0.0
        )

        if abs(constrained_dollars) < min_trade_dollars:
            action_list.append("Hold")
            reason_list.append(
                "Below minimum trade size"
                if not capped_reason
                else f"{capped_reason}; below minimum trade size",
            )
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        if constrained_shares_trade > 0:
            action = "Add"
            reason = f"Below target redistribution weight; moving {rebalance_step * 100:.0f}% of gap"
        elif constrained_shares_trade < 0:
            action = "Trim"
            reason = f"Above target redistribution weight; moving {rebalance_step * 100:.0f}% of gap"
        else:
            action = "Hold"
            reason = "No action"

        if capped_reason:
            reason = f"{reason}; {capped_reason}"

        action_list.append(action)
        reason_list.append(reason)
        shares_trade_list.append(constrained_shares_trade)
        dollar_trade_list.append(constrained_dollars)
        target_weight_effective_list.append(effective_weight)

    df["recommended_action"] = action_list
    df["reason"] = reason_list
    df["shares_trade"] = shares_trade_list
    df["dollar_trade"] = dollar_trade_list
    df["target_weight_effective"] = target_weight_effective_list

    action_rank_map = {"Add": 0, "Trim": 1, "Review": 2, "Excluded": 3, "Hold": 4}
    df["recommended_action_rank"] = df["recommended_action"].map(action_rank_map).fillna(99)

    action_queue = df[
        [
            "ticker",
            "price",
            "shares",
            "market_value",
            "current_weight",
            "eligible_redistribution_shares",
            "eligible_redistribution_value",
            "current_eligible_weight",
            "target_weight",
            "target_weight_effective",
            "dollar_trade",
            "shares_trade",
            "weighted_cagr_y3",
            "recommended_action",
            "recommended_action_rank",
            "reason",
            "action",
            "confidence",
            "include_in_redistribution",
        ]
    ].copy()

    action_queue = action_queue.sort_values(
        by=["recommended_action_rank", "weighted_cagr_y3", "dollar_trade"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns=["recommended_action_rank"])

    summary = {
        "total_portfolio_value": total_portfolio_value,
        "redistribution_pool_value": redistribution_pool_value,
        "total_buy_dollars": action_queue[action_queue["recommended_action"] == "Add"][
            "dollar_trade"
        ].sum(),
        "total_trim_dollars": -action_queue[action_queue["recommended_action"] == "Trim"][
            "dollar_trade"
        ].sum(),
        "review_count": int((action_queue["recommended_action"] == "Review").sum()),
        "action_count": int(
            (action_queue["recommended_action"].isin(["Add", "Trim"])).sum(),
        ),
        "rebalance_step_pct": rebalance_step * 100,
    }
    return action_queue, summary


def merge_global_settings(saved: Any) -> dict[str, Any]:
    settings = dict(DEFAULT_GLOBAL_SETTINGS)
    if isinstance(saved, dict):
        settings.update(saved)

    if not isinstance(settings.get("growth_pe_table"), list):
        settings["growth_pe_table"] = DEFAULT_GROWTH_PE_TABLE

    pinned = settings.get("bottom_pinned_tickers", [])
    if not isinstance(pinned, list):
        pinned = []
    settings["bottom_pinned_tickers"] = sorted(
        {str(x).strip().upper() for x in pinned if str(x).strip()},
    )
    return settings
