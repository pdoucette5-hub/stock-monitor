"""Portfolio performance and the compensation tracker."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.config import (
    MATTHEW_COMPENSATION_ACCOUNTS,
    MATTHEW_COMPENSATION_ACCOUNT_KEYS,
    VALID_MANAGEMENT_MODES,
)
from backend.logic import safe_float
from backend.management_service import (
    apply_management_shares_to_config,
    build_management_snapshot,
    filter_transactions_by_management_mode,
)
from backend.performance_service import build_portfolio_performance
from backend.price_store import get_price_points_for_ticker
from backend.state_store import (
    get_cached_response,
    load_transactions,
    set_cached_response,
)
from backend.tickers_config import get_effective_tickers_config
from backend.utils import normalize_ticker


router = APIRouter()


@router.get("/api/performance/portfolio")
def get_portfolio_performance(
    range: str = "3y",
    accounts: str = "",
    mode: str = "all",
    force_refresh: bool = False,
) -> dict[str, Any]:
    try:
        cache_key = (
            f"performance:v2:{str(range).strip().lower()}:"
            f"{str(accounts).strip().casefold()}:{str(mode).strip().lower()}"
        )
        if not force_refresh:
            cached = get_cached_response(cache_key)
            if cached is not None:
                return cached

        transactions = load_transactions()
        tickers_config = get_effective_tickers_config()
        management = build_management_snapshot()
        normalized_mode = str(mode or "all").strip().lower()
        if normalized_mode != "all" and normalized_mode not in VALID_MANAGEMENT_MODES:
            raise ValueError("Mode must be all, managed, track, or excluded")

        if normalized_mode in VALID_MANAGEMENT_MODES:
            transactions = filter_transactions_by_management_mode(
                transactions,
                normalized_mode,
            )
            tickers_config = apply_management_shares_to_config(
                tickers_config,
                management["shares_by_mode"],
                normalized_mode,
            )
        account_filter = [
            account.strip()
            for account in str(accounts).split(",")
            if account.strip()
        ]
        payload = build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=tickers_config,
            range_key=range,
            accounts=account_filter or None,
            supplemental_positions=[
                position
                for position in management["positions"]
                if position["source"] != "transactions"
                and (
                    normalized_mode == "all"
                    or position["mode"] == normalized_mode
                )
            ],
        )
        return set_cached_response(cache_key, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def transaction_cash_flow_amount(tx: dict[str, Any]) -> tuple[float, float]:
    tx_type = str(tx.get("type", "")).strip().lower()
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0

    if tx_type in {"buy", "transfer_in", "adjustment"}:
        if shares <= 0 or price_per_share is None:
            return 0.0, 0.0
        return (shares * price_per_share) + fees, 0.0

    if tx_type in {"sell", "transfer_out"}:
        if shares <= 0 or price_per_share is None:
            return 0.0, 0.0
        return -max((shares * price_per_share) - fees, 0.0), 0.0

    if tx_type == "dividend":
        return 0.0, max(price_per_share or 0.0, 0.0)

    return 0.0, 0.0


def benchmark_close_on_or_after(
    benchmark_points: list[dict[str, Any]],
    flow_date: str,
) -> float | None:
    for point in benchmark_points:
        point_date = str(point.get("date") or "")
        close = safe_float(point.get("close"))
        if point_date >= flow_date and close is not None and close > 0:
            return close
    return None


def benchmark_close_on_or_before(
    benchmark_lookup: dict[str, float],
    sorted_dates: list[str],
    target_date: str,
) -> float | None:
    for point_date in reversed(sorted_dates):
        if point_date <= target_date:
            return benchmark_lookup.get(point_date)
    return None


def filter_transactions_by_accounts(
    transactions: dict[str, list[dict[str, Any]]],
    accounts: set[str],
) -> dict[str, list[dict[str, Any]]]:
    selected = {account.casefold() for account in accounts}
    filtered: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        rows = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("account") or "").strip().casefold() in selected
        ]
        if rows:
            filtered[ticker] = rows
    return filtered


def sorted_transaction_rows_with_ticker(
    transactions: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ticker, entries in transactions.items():
        for entry in entries:
            if isinstance(entry, dict):
                rows.append({**entry, "ticker": ticker})
    return sorted(
        rows,
        key=lambda tx: (
            str(tx.get("date") or ""),
            str(tx.get("id") or ""),
            str(tx.get("ticker") or ""),
        ),
    )


def transaction_trade_amount(tx: dict[str, Any]) -> float:
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0
    if shares <= 0 or price_per_share is None:
        return 0.0
    return (shares * price_per_share) + fees


def transaction_sell_proceeds(tx: dict[str, Any]) -> float:
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0
    if shares <= 0 or price_per_share is None:
        return 0.0
    return max((shares * price_per_share) - fees, 0.0)


def compensation_same_day_priority(tx: dict[str, Any]) -> int:
    tx_type = str(tx.get("type") or "").strip().lower()
    if tx_type in {"sell", "dividend", "transfer_out"}:
        return 0
    if tx_type in {"buy", "transfer_in", "adjustment"}:
        return 1
    return 2


@router.get("/api/compensation")
def get_compensation_tracker(
    range: str = "1y",
    benchmark: str = "SPY",
    share_pct: float = 0.33,
) -> dict[str, Any]:
    try:
        normalized_benchmark = normalize_ticker(benchmark) or "SPY"
        payout_share = max(0.0, min(float(share_pct), 1.0))
        transactions = filter_transactions_by_accounts(
            load_transactions(),
            MATTHEW_COMPENSATION_ACCOUNTS,
        )
        management = build_management_snapshot()
        performance = build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=get_effective_tickers_config(),
            range_key=range,
            accounts=sorted(MATTHEW_COMPENSATION_ACCOUNTS),
            supplemental_positions=[
                position
                for position in management["positions"]
                if position["source"] != "transactions"
                and str(position.get("account") or "").strip().casefold()
                in MATTHEW_COMPENSATION_ACCOUNT_KEYS
            ],
        )
        portfolio_series = performance.get("series")
        if not isinstance(portfolio_series, list):
            portfolio_series = []
        valid_portfolio = [
            row
            for row in portfolio_series
            if isinstance(row, dict) and safe_float(row.get("market_value")) is not None
        ]
        benchmark_points = [
            row
            for row in get_price_points_for_ticker(normalized_benchmark, range_key=range)
            if isinstance(row, dict) and safe_float(row.get("close")) is not None
        ]

        if len(valid_portfolio) < 2:
            raise ValueError("Need at least two portfolio performance points")
        if len(benchmark_points) < 2:
            raise ValueError(f"Need at least two price points for {normalized_benchmark}")

        portfolio_dates = [str(row.get("date") or "") for row in valid_portfolio]
        benchmark_dates = [str(row.get("date") or "") for row in benchmark_points]
        first_positive_portfolio_date = next(
            (
                str(row.get("date") or "")
                for row in valid_portfolio
                if (safe_float(row.get("market_value")) or 0.0) > 0
            ),
            "",
        )
        if not first_positive_portfolio_date:
            raise ValueError("Need a positive recorded portfolio value")
        start_date = max(first_positive_portfolio_date, benchmark_dates[0])
        end_date = min(portfolio_dates[-1], benchmark_dates[-1])
        if start_date >= end_date:
            raise ValueError("Need overlapping portfolio and benchmark history")

        window_portfolio = [
            row
            for row in valid_portfolio
            if start_date <= str(row.get("date") or "") <= end_date
        ]
        window_benchmark = [
            row
            for row in benchmark_points
            if start_date <= str(row.get("date") or "") <= end_date
        ]
        if len(window_portfolio) < 2 or len(window_benchmark) < 2:
            raise ValueError("Need at least two overlapping performance points")

        start_portfolio = window_portfolio[0]
        end_portfolio = window_portfolio[-1]
        start_value = safe_float(start_portfolio.get("market_value")) or 0.0
        end_value = safe_float(end_portfolio.get("market_value")) or 0.0
        end_cost_basis = safe_float(end_portfolio.get("cost_basis")) or 0.0
        if start_value <= 0:
            raise ValueError("Starting portfolio value must be positive")
        if end_cost_basis <= 0:
            raise ValueError("Current cost basis must be positive")

        benchmark_lookup = {
            str(row.get("date") or ""): safe_float(row.get("close")) or 0.0
            for row in window_benchmark
            if row.get("date") and safe_float(row.get("close")) is not None
        }
        benchmark_dates_sorted = sorted(benchmark_lookup)
        start_portfolio_date = str(start_portfolio.get("date") or start_date)
        end_portfolio_date = str(end_portfolio.get("date") or end_date)
        start_close = benchmark_close_on_or_before(
            benchmark_lookup,
            benchmark_dates_sorted,
            start_portfolio_date,
        ) or 0.0
        benchmark_as_of = next(
            (
                point_date
                for point_date in reversed(benchmark_dates_sorted)
                if point_date <= end_portfolio_date
            ),
            "",
        )
        end_close = benchmark_close_on_or_before(
            benchmark_lookup,
            benchmark_dates_sorted,
            end_portfolio_date,
        ) or 0.0
        if start_close <= 0:
            raise ValueError(f"Starting {normalized_benchmark} price must be positive")
        if end_close <= 0:
            raise ValueError(f"Ending {normalized_benchmark} price must be positive")

        # Performance already derives contributed capital from the complete
        # transaction ledger, accounting for sale proceeds and dividends that
        # are reinvested. Rebuilding that path from the selected window's
        # opening market value changes the basis whenever the range changes.
        benchmark_series: list[dict[str, Any]] = []
        for row in window_portfolio:
            row_date = str(row.get("date") or "")
            if not row_date:
                continue
            capital_basis = safe_float(row.get("capital_basis"))
            benchmark_value_for_date = safe_float(row.get("benchmark_value"))
            if capital_basis is None or benchmark_value_for_date is None:
                continue
            cost_basis = safe_float(row.get("cost_basis")) or 0.0
            market_value = safe_float(row.get("market_value")) or 0.0
            benchmark_series.append(
                {
                    "date": row_date,
                    "market_value": round(market_value, 8),
                    "cost_basis": round(cost_basis, 8),
                    "capital_basis": round(capital_basis, 8),
                    "benchmark_value": round(benchmark_value_for_date, 8),
                }
            )
        if len(benchmark_series) < 2:
            raise ValueError("Need at least two benchmark-equivalent points")

        invested_capital = benchmark_series[-1]["capital_basis"]
        if invested_capital <= 0:
            raise ValueError("Current capital basis must be positive")

        distributions = 0.0
        for ticker, entries in transactions.items():
            for tx in entries:
                if not isinstance(tx, dict):
                    continue
                tx_date = str(tx.get("date") or "").strip()
                if not tx_date or not (start_portfolio_date < tx_date <= end_portfolio_date):
                    continue
                _, distribution = transaction_cash_flow_amount(tx)
                if distribution > 0:
                    distributions += distribution

        actual_terminal_value = end_value + distributions
        spy_window_return = (end_close / start_close) - 1.0
        benchmark_value = benchmark_series[-1]["benchmark_value"]
        actual_gain = actual_terminal_value - invested_capital
        benchmark_gain = benchmark_value - invested_capital
        excess_gain = actual_terminal_value - benchmark_value
        portfolio_return = actual_gain / invested_capital if invested_capital > 0 else 0.0
        benchmark_return = benchmark_gain / invested_capital if invested_capital > 0 else 0.0
        excess_return = portfolio_return - benchmark_return
        payout_base = max(excess_gain, 0.0)
        payout = payout_base * payout_share

        return {
            "range": range,
            "benchmark": normalized_benchmark,
            "mode": "matthew",
            "accounts": sorted(MATTHEW_COMPENSATION_ACCOUNTS),
            "share_pct": payout_share,
            "start_date": start_portfolio_date,
            "end_date": end_portfolio_date,
            "portfolio_start_value": start_value,
            "portfolio_end_value": end_value,
            "cost_basis": end_cost_basis,
            "invested_capital": invested_capital,
            "portfolio_return": portfolio_return,
            "actual_gain": actual_gain,
            "distributions": distributions,
            "actual_terminal_value": actual_terminal_value,
            "benchmark_start_price": start_close,
            "benchmark_end_price": end_close,
            "benchmark_as_of": benchmark_as_of,
            "spy_window_return": spy_window_return,
            "benchmark_return": benchmark_return,
            "benchmark_equivalent_value": benchmark_value,
            "benchmark_gain": benchmark_gain,
            "excess_return": excess_return,
            "excess_gain": excess_gain,
            "payout_base": payout_base,
            "payout": payout,
            "series": benchmark_series,
            "portfolio_series_points": len(window_portfolio),
            "benchmark_points": len(window_benchmark),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
