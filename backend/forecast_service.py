"""Assumption snapshots, the forecast scorecard, and change-set helpers.

Each saved assumption snapshot records a ticker's growth assumptions at a point
in time; the scorecard compares those forecasts with reported actuals."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.logic import safe_float
from backend.management_service import (
    build_management_snapshot,
    build_position_summaries,
)
from backend.portfolio_service import build_portfolio_views, normalize_portfolio
from backend.state_store import (
    load_assumption_snapshots,
    load_earnings_calendar,
    load_reported_fundamentals,
    load_scenario_inputs,
    load_settings_dict,
    save_assumption_snapshots,
)
from backend.tickers_config import get_effective_tickers_config
from backend.utils import normalize_ticker


def _scenario_copy(state: dict[str, Any], scenario_name: str) -> dict[str, Any]:
    scenario = state.get(scenario_name)
    return json.loads(json.dumps(scenario)) if isinstance(scenario, dict) else {}


def _first_growth_rate(state: dict[str, Any], scenario_name: str, field: str) -> float | None:
    scenario = state.get(scenario_name)
    if not isinstance(scenario, dict):
        return None
    values = scenario.get(field)
    if not isinstance(values, list) or not values:
        return None
    value = safe_float(values[0])
    return None if value is None else value / 100.0


def _snapshot_change_summary(snapshot: dict[str, Any]) -> list[str]:
    changes = snapshot.get("changes")
    if not isinstance(changes, list):
        return []
    summary: list[str] = []
    for change in changes[:4]:
        if not isinstance(change, dict):
            continue
        label = str(change.get("label") or change.get("field") or "").strip()
        if label:
            summary.append(label)
    return summary


def _growth_error(
    actual_start: Any,
    actual_current: Any,
    annual_assumption: float | None,
    elapsed_days: int,
) -> dict[str, Any]:
    start = safe_float(actual_start)
    current = safe_float(actual_current)
    if start is None or current is None or start <= 0 or current <= 0:
        return {
            "actual_growth": None,
            "expected_growth_to_date": None,
            "error": None,
            "status": "needs actuals",
        }

    actual_growth = (current / start) - 1.0
    expected_growth = None
    error = None
    status = "baseline only"
    if annual_assumption is not None and elapsed_days > 0:
        expected_growth = ((1.0 + annual_assumption) ** (elapsed_days / 365.0)) - 1.0
        error = actual_growth - expected_growth
        if error > 0.05:
            status = "ahead"
        elif error < -0.05:
            status = "behind"
        else:
            status = "tracking"

    return {
        "actual_growth": actual_growth,
        "expected_growth_to_date": expected_growth,
        "error": error,
        "status": status,
    }


def append_assumption_snapshot(
    ticker: str,
    scenario: dict[str, Any],
    row_context: dict[str, Any] | None,
    previous_scenario: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
) -> None:
    normalized = normalize_ticker(ticker)
    if not normalized:
        return

    context = row_context or {}
    snapshots = load_assumption_snapshots()
    previous = previous_scenario if isinstance(previous_scenario, dict) else {}
    snapshots.append(
        {
            "id": uuid.uuid4().hex,
            "ticker": normalized,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "revision_type": "update" if previous else "initial",
            "changes": changes if isinstance(changes, list) else [],
            "latest_quarter_revenue": safe_float(scenario.get("latest_quarter_revenue")),
            "latest_quarter_net_income": safe_float(scenario.get("latest_quarter_net_income")),
            "shares_outstanding": safe_float(scenario.get("shares_outstanding")),
            "previous_latest_quarter_revenue": safe_float(previous.get("latest_quarter_revenue")),
            "previous_latest_quarter_net_income": safe_float(previous.get("latest_quarter_net_income")),
            "previous_shares_outstanding": safe_float(previous.get("shares_outstanding")),
            "price": safe_float(context.get("price")),
            "current_pe": safe_float(context.get("current_pe")),
            "bear_cagr_y3": safe_float(context.get("bear_cagr_y3")),
            "base_cagr_y3": safe_float(context.get("base_cagr_y3")),
            "bull_cagr_y3": safe_float(context.get("bull_cagr_y3")),
            "weighted_cagr_y3": safe_float(context.get("weighted_cagr_y3")),
            "action": context.get("action"),
            "action_rank": context.get("action_rank"),
            "compression_opportunity_score": safe_float(
                context.get("compression_opportunity_score"),
            ),
            "bear": _scenario_copy(scenario, "bear"),
            "base": _scenario_copy(scenario, "base"),
            "bull": _scenario_copy(scenario, "bull"),
            "previous_bear": _scenario_copy(previous, "bear"),
            "previous_base": _scenario_copy(previous, "base"),
            "previous_bull": _scenario_copy(previous, "bull"),
        },
    )
    save_assumption_snapshots(snapshots)


def _current_assumption_snapshot(
    ticker: str,
    scenario: dict[str, Any],
    row_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = row_context or {}
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"current-{normalize_ticker(ticker)}",
        "ticker": normalize_ticker(ticker),
        "timestamp": now,
        "latest_quarter_revenue": safe_float(scenario.get("latest_quarter_revenue")),
        "latest_quarter_net_income": safe_float(scenario.get("latest_quarter_net_income")),
        "shares_outstanding": safe_float(scenario.get("shares_outstanding")),
        "price": safe_float(context.get("price")),
        "current_pe": safe_float(context.get("current_pe")),
        "bear_cagr_y3": safe_float(context.get("bear_cagr_y3")),
        "base_cagr_y3": safe_float(context.get("base_cagr_y3")),
        "bull_cagr_y3": safe_float(context.get("bull_cagr_y3")),
        "weighted_cagr_y3": safe_float(context.get("weighted_cagr_y3")),
        "action": context.get("action"),
        "action_rank": context.get("action_rank"),
        "compression_opportunity_score": safe_float(
            context.get("compression_opportunity_score"),
        ),
        "bear": _scenario_copy(scenario, "bear"),
        "base": _scenario_copy(scenario, "base"),
        "bull": _scenario_copy(scenario, "bull"),
        "is_current_assumption": True,
    }


def build_forecast_scorecard() -> dict[str, Any]:
    snapshots = sorted(
        load_assumption_snapshots(),
        key=lambda item: str(item.get("timestamp") or ""),
        reverse=True,
    )
    scenario_inputs = load_scenario_inputs()
    tickers_config = get_effective_tickers_config()
    portfolio_tickers = {
        row["ticker"]
        for row in normalize_portfolio(tickers_config.get("portfolio", []))
        if row.get("ticker")
    }
    portfolio_view = build_portfolio_views(
        tickers_config,
        scenario_inputs,
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=False,
    )
    reported_fundamentals = load_reported_fundamentals()
    row_by_ticker = {
        row.get("ticker"): row
        for row in portfolio_view.get("portfolio", [])
        if isinstance(row, dict) and row.get("ticker")
    }

    latest_by_ticker: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        ticker = normalize_ticker(snapshot.get("ticker", ""))
        if ticker and ticker in portfolio_tickers and ticker not in latest_by_ticker:
            latest_by_ticker[ticker] = snapshot

    for ticker, scenario in scenario_inputs.items():
        normalized = normalize_ticker(ticker)
        if (
            not normalized
            or normalized not in portfolio_tickers
            or normalized in latest_by_ticker
            or not isinstance(scenario, dict)
        ):
            continue
        latest_by_ticker[normalized] = _current_assumption_snapshot(
            normalized,
            scenario,
            row_by_ticker.get(normalized),
        )

    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for ticker, snapshot in latest_by_ticker.items():
        current_state = scenario_inputs.get(ticker, {})
        if not isinstance(current_state, dict):
            current_state = {}
        current_row = row_by_ticker.get(ticker, {})
        reported = reported_fundamentals.get(ticker)
        if not isinstance(reported, dict):
            reported = {}
        reported_revenue = safe_float(reported.get("latest_quarter_revenue"))
        reported_net_income = safe_float(reported.get("latest_quarter_net_income"))
        reported_shares = safe_float(reported.get("shares_outstanding"))
        preference = current_state.get("actuals_source_preference")
        if preference not in {"manual", "reported"}:
            preference = "manual"
        use_reported = preference == "reported" and bool(reported)
        current_revenue = current_state.get("latest_quarter_revenue")
        current_net_income = current_state.get("latest_quarter_net_income")
        current_shares = current_state.get("shares_outstanding")
        reported_fields_used = []
        if use_reported:
            if reported_revenue is not None:
                current_revenue = reported_revenue
                reported_fields_used.append("latest_quarter_revenue")
            if reported_net_income is not None:
                current_net_income = reported_net_income
                reported_fields_used.append("latest_quarter_net_income")
            if reported_shares is not None:
                current_shares = reported_shares
                reported_fields_used.append("shares_outstanding")
        actuals_source = "stock-detail"
        if use_reported and reported_fields_used:
            actuals_source = (
                "sec-companyfacts"
                if len(reported_fields_used) >= 2
                else "mixed"
            )
        try:
            snapshot_time = datetime.fromisoformat(str(snapshot.get("timestamp")))
            if snapshot_time.tzinfo is None:
                snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
        except ValueError:
            snapshot_time = now
        elapsed_days = max(0, (now - snapshot_time).days)

        base_rev_assumption = _first_growth_rate(snapshot, "base", "rev_growth_rates")
        base_earnings_assumption = _first_growth_rate(
            snapshot,
            "base",
            "net_income_growth_rates",
        )
        revenue_score = _growth_error(
            snapshot.get("latest_quarter_revenue"),
            current_revenue,
            base_rev_assumption,
            elapsed_days,
        )
        earnings_score = _growth_error(
            snapshot.get("latest_quarter_net_income"),
            current_net_income,
            base_earnings_assumption,
            elapsed_days,
        )

        start_price = safe_float(snapshot.get("price"))
        current_price = safe_float(current_row.get("price"))
        price_return = None
        cagr_error = None
        if start_price is not None and current_price is not None and start_price > 0:
            price_return = (current_price / start_price) - 1.0
            base_cagr = safe_float(snapshot.get("base_cagr_y3"))
            if base_cagr is not None and elapsed_days > 0:
                expected_price_return = ((1.0 + base_cagr) ** (elapsed_days / 365.0)) - 1.0
                cagr_error = price_return - expected_price_return

        rows.append(
            {
                "ticker": ticker,
                "snapshot_id": snapshot.get("id"),
                "snapshot_timestamp": snapshot.get("timestamp"),
                "is_current_assumption": bool(snapshot.get("is_current_assumption")),
                "revision_type": snapshot.get("revision_type") or (
                    "current" if snapshot.get("is_current_assumption") else "saved"
                ),
                "change_summary": _snapshot_change_summary(snapshot),
                "elapsed_days": elapsed_days,
                "start_price": start_price,
                "current_price": current_price,
                "price_return": price_return,
                "base_cagr_y3_at_snapshot": safe_float(snapshot.get("base_cagr_y3")),
                "cagr_error_to_date": cagr_error,
                "base_revenue_growth_y1": base_rev_assumption,
                "base_earnings_growth_y1": base_earnings_assumption,
                "actuals_source_preference": preference,
                "actuals_source": actuals_source,
                "actuals_reported_fields_used": reported_fields_used,
                "reported_period_end": reported.get("period_end"),
                "reported_filed_date": reported.get("filed_date"),
                "reported_confidence": reported.get("confidence"),
                "reported_missing": reported.get("missing", []),
                "current_latest_quarter_revenue": safe_float(current_revenue),
                "current_latest_quarter_net_income": safe_float(current_net_income),
                "current_shares_outstanding": safe_float(current_shares),
                "revenue_actual_growth": revenue_score["actual_growth"],
                "revenue_expected_growth_to_date": revenue_score["expected_growth_to_date"],
                "revenue_error": revenue_score["error"],
                "revenue_status": revenue_score["status"],
                "earnings_actual_growth": earnings_score["actual_growth"],
                "earnings_expected_growth_to_date": earnings_score[
                    "expected_growth_to_date"
                ],
                "earnings_error": earnings_score["error"],
                "earnings_status": earnings_score["status"],
                "action_at_snapshot": snapshot.get("action"),
                "action_rank_at_snapshot": snapshot.get("action_rank"),
                "current_action": current_row.get("action"),
                "current_base_cagr_y3": safe_float(current_row.get("base_cagr_y3")),
            },
        )

    rows.sort(
        key=lambda row: (
            row.get("revenue_error") is None and row.get("earnings_error") is None,
            str(row.get("ticker") or ""),
        ),
    )

    history_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        ticker = normalize_ticker(snapshot.get("ticker", ""))
        if not ticker or ticker not in portfolio_tickers:
            continue
        current_state = scenario_inputs.get(ticker, {})
        if not isinstance(current_state, dict):
            current_state = {}
        current_row = row_by_ticker.get(ticker, {})
        reported = reported_fundamentals.get(ticker)
        if not isinstance(reported, dict):
            reported = {}
        preference = current_state.get("actuals_source_preference")
        if preference not in {"manual", "reported"}:
            preference = "manual"
        current_revenue = current_state.get("latest_quarter_revenue")
        current_net_income = current_state.get("latest_quarter_net_income")
        if preference == "reported":
            current_revenue = (
                safe_float(reported.get("latest_quarter_revenue"))
                if safe_float(reported.get("latest_quarter_revenue")) is not None
                else current_revenue
            )
            current_net_income = (
                safe_float(reported.get("latest_quarter_net_income"))
                if safe_float(reported.get("latest_quarter_net_income")) is not None
                else current_net_income
            )
        try:
            snapshot_time = datetime.fromisoformat(str(snapshot.get("timestamp")))
            if snapshot_time.tzinfo is None:
                snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
        except ValueError:
            snapshot_time = now
        elapsed_days = max(0, (now - snapshot_time).days)
        base_rev_assumption = _first_growth_rate(snapshot, "base", "rev_growth_rates")
        base_earnings_assumption = _first_growth_rate(
            snapshot,
            "base",
            "net_income_growth_rates",
        )
        revenue_score = _growth_error(
            snapshot.get("latest_quarter_revenue"),
            current_revenue,
            base_rev_assumption,
            elapsed_days,
        )
        earnings_score = _growth_error(
            snapshot.get("latest_quarter_net_income"),
            current_net_income,
            base_earnings_assumption,
            elapsed_days,
        )
        start_price = safe_float(snapshot.get("price"))
        current_price = safe_float(current_row.get("price"))
        price_return = None
        if start_price is not None and current_price is not None and start_price > 0:
            price_return = (current_price / start_price) - 1.0
        previous_base_rev = _first_growth_rate(snapshot, "previous_base", "rev_growth_rates")
        previous_base_earnings = _first_growth_rate(
            snapshot,
            "previous_base",
            "net_income_growth_rates",
        )
        history_rows.append(
            {
                "ticker": ticker,
                "snapshot_id": snapshot.get("id"),
                "snapshot_timestamp": snapshot.get("timestamp"),
                "revision_type": snapshot.get("revision_type") or "saved",
                "change_summary": _snapshot_change_summary(snapshot),
                "elapsed_days": elapsed_days,
                "base_revenue_growth_y1": base_rev_assumption,
                "previous_base_revenue_growth_y1": previous_base_rev,
                "base_earnings_growth_y1": base_earnings_assumption,
                "previous_base_earnings_growth_y1": previous_base_earnings,
                "base_cagr_y3_at_snapshot": safe_float(snapshot.get("base_cagr_y3")),
                "action_at_snapshot": snapshot.get("action"),
                "start_price": start_price,
                "current_price": current_price,
                "price_return": price_return,
                "revenue_actual_growth": revenue_score["actual_growth"],
                "revenue_expected_growth_to_date": revenue_score["expected_growth_to_date"],
                "revenue_error": revenue_score["error"],
                "revenue_status": revenue_score["status"],
                "earnings_actual_growth": earnings_score["actual_growth"],
                "earnings_expected_growth_to_date": earnings_score["expected_growth_to_date"],
                "earnings_error": earnings_score["error"],
                "earnings_status": earnings_score["status"],
            },
        )
    history_rows.sort(
        key=lambda row: (
            str(row.get("ticker") or ""),
            str(row.get("snapshot_timestamp") or ""),
        ),
        reverse=True,
    )
    return {
        "rows": rows,
        "history": history_rows[:500],
        "snapshots": snapshots[:500],
        "snapshot_count": len(snapshots),
    }


def values_are_equal(previous: Any, current: Any) -> bool:
    if previous is None and current in ("", None):
        return True
    if current is None and previous in ("", None):
        return True

    try:
        return abs(float(previous) - float(current)) < 0.000001
    except (TypeError, ValueError):
        return previous == current


def flatten_change_values(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, nested_value in value.items():
            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_change_values(nested_value, nested_prefix))
        return flattened

    if isinstance(value, list):
        flattened = {}
        for idx, nested_value in enumerate(value):
            nested_prefix = f"{prefix}.{idx}" if prefix else str(idx)
            flattened.update(flatten_change_values(nested_value, nested_prefix))
        return flattened

    return {prefix: value}


def build_change_set(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    labels: dict[str, str],
) -> list[dict[str, Any]]:
    previous_values = flatten_change_values(previous or {})
    current_values = flatten_change_values(current)
    changes: list[dict[str, Any]] = []

    for field in labels:
        old_value = previous_values.get(field)
        new_value = current_values.get(field)
        if values_are_equal(old_value, new_value):
            continue
        changes.append(
            {
                "field": field,
                "label": labels[field],
                "old": old_value,
                "new": new_value,
            },
        )

    return changes
