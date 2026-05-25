from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.price_store import get_price_points_for_ticker
from backend.transactions_service import compute_position_summary


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _sorted_transaction_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [entry for entry in entries if isinstance(entry, dict)]
    return sorted(
        rows,
        key=lambda tx: (
            str(tx.get("date", "")),
            str(tx.get("id", "")),
        ),
    )


def _apply_transaction_to_position(
    position: dict[str, float],
    tx: dict[str, Any],
) -> None:
    tx_type = str(tx.get("type", "")).strip().lower()
    shares = _safe_float(tx.get("shares"), 0.0)
    price_per_share = tx.get("price_per_share")
    price_per_share = (
        None if price_per_share in (None, "") else _safe_float(price_per_share, 0.0)
    )
    fees = _safe_float(tx.get("fees"), 0.0)

    current_shares = position["shares"]
    current_cost_basis = position["cost_basis"]
    avg_cost = (current_cost_basis / current_shares) if current_shares > 0 else 0.0

    if tx_type == "buy":
        if shares > 0 and price_per_share is not None:
            position["shares"] += shares
            position["cost_basis"] += (shares * price_per_share) + fees

    elif tx_type == "transfer_in":
        if shares > 0:
            position["shares"] += shares
            if price_per_share is not None:
                position["cost_basis"] += (shares * price_per_share) + fees
            else:
                position["cost_basis"] += fees

    elif tx_type == "sell":
        if shares > 0 and price_per_share is not None and shares <= position["shares"] + 1e-9:
            cost_removed = shares * avg_cost
            proceeds = (shares * price_per_share) - fees
            position["realized_gain_loss"] += proceeds - cost_removed
            position["shares"] -= shares
            position["cost_basis"] -= cost_removed

    elif tx_type == "transfer_out":
        if shares > 0 and shares <= position["shares"] + 1e-9:
            cost_removed = shares * avg_cost
            position["shares"] -= shares
            position["cost_basis"] -= cost_removed

    elif tx_type == "dividend":
        position["dividend_cash"] += _safe_float(price_per_share, 0.0)

    elif tx_type == "split":
        if shares > 0 and position["shares"] > 0:
            position["shares"] *= shares

    elif tx_type == "adjustment":
        if shares > 0:
            position["shares"] += shares
            if price_per_share is not None:
                position["cost_basis"] += (shares * price_per_share) + fees
            else:
                position["cost_basis"] += fees

    if abs(position["shares"]) < 1e-10:
        position["shares"] = 0.0
    if abs(position["cost_basis"]) < 1e-10:
        position["cost_basis"] = 0.0


def _build_position_history(entries: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    rows = _sorted_transaction_rows(entries)
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tx in rows:
        tx_date = str(tx.get("date", "")).strip()
        if tx_date:
            by_date[tx_date].append(tx)

    running = {
        "shares": 0.0,
        "cost_basis": 0.0,
        "realized_gain_loss": 0.0,
        "dividend_cash": 0.0,
    }

    snapshots: dict[str, dict[str, float]] = {}
    for tx_date in sorted(by_date.keys()):
        for tx in by_date[tx_date]:
            _apply_transaction_to_position(running, tx)
        snapshots[tx_date] = {
            "shares": round(running["shares"], 8),
            "cost_basis": round(running["cost_basis"], 8),
            "realized_gain_loss": round(running["realized_gain_loss"], 8),
            "dividend_cash": round(running["dividend_cash"], 8),
        }

    return snapshots


def _latest_snapshot_on_or_before(
    snapshots: dict[str, dict[str, float]],
    current_date: str,
) -> dict[str, float]:
    eligible_dates = [d for d in snapshots.keys() if d <= current_date]
    if not eligible_dates:
        return {
            "shares": 0.0,
            "cost_basis": 0.0,
            "realized_gain_loss": 0.0,
            "dividend_cash": 0.0,
        }
    latest = max(eligible_dates)
    return snapshots[latest]


def _portfolio_tickers(tickers_config: dict[str, Any]) -> list[str]:
    portfolio_items = tickers_config.get("portfolio", []) or []
    tickers: list[str] = []

    for item in portfolio_items:
        if isinstance(item, dict):
            ticker = str(item.get("ticker", "")).strip().upper()
        else:
            ticker = str(item).strip().upper()
        if ticker:
            tickers.append(ticker)

    return sorted(set(tickers))


def build_portfolio_performance(
    transactions_by_ticker: dict[str, list[dict[str, Any]]],
    tickers_config: dict[str, Any],
    range_key: str = "1y",
) -> dict[str, Any]:
    tickers = [
        ticker
        for ticker in _portfolio_tickers(tickers_config)
        if ticker in transactions_by_ticker
    ]

    history_by_ticker: dict[str, list[dict[str, Any]]] = {}
    position_history_by_ticker: dict[str, dict[str, dict[str, float]]] = {}
    all_dates: set[str] = set()

    for ticker in tickers:
        points = get_price_points_for_ticker(ticker, range_key=range_key)
        if not points:
            continue

        history_by_ticker[ticker] = points
        for point in points:
            if point.get("date"):
                all_dates.add(str(point["date"]))

        position_history_by_ticker[ticker] = _build_position_history(
            transactions_by_ticker.get(ticker, []),
        )

    ordered_dates = sorted(all_dates)
    if not ordered_dates:
        return {
            "range": range_key,
            "tickers": tickers,
            "series": [],
            "latest": {
                "market_value": 0.0,
                "cost_basis": 0.0,
                "unrealized_gain_loss": 0.0,
            },
            "positions": {
                ticker: compute_position_summary(transactions_by_ticker.get(ticker, []))
                for ticker in tickers
            },
        }

    close_lookup: dict[str, dict[str, float]] = defaultdict(dict)
    for ticker, points in history_by_ticker.items():
        for point in points:
            if point.get("date") and point.get("close") is not None:
                close_lookup[ticker][str(point["date"])] = _safe_float(point["close"], 0.0)

    series: list[dict[str, Any]] = []

    for current_date in ordered_dates:
        total_market_value = 0.0
        total_cost_basis = 0.0
        holdings: list[dict[str, Any]] = []

        for ticker in tickers:
            close = close_lookup[ticker].get(current_date)
            if close is None:
                continue

            snapshot = _latest_snapshot_on_or_before(
                position_history_by_ticker.get(ticker, {}),
                current_date,
            )
            shares = _safe_float(snapshot.get("shares"), 0.0)
            cost_basis = _safe_float(snapshot.get("cost_basis"), 0.0)

            if shares <= 0:
                continue

            market_value = shares * close
            unrealized = market_value - cost_basis

            total_market_value += market_value
            total_cost_basis += cost_basis

            holdings.append(
                {
                    "ticker": ticker,
                    "shares": round(shares, 8),
                    "close": round(close, 8),
                    "market_value": round(market_value, 8),
                    "cost_basis": round(cost_basis, 8),
                    "unrealized_gain_loss": round(unrealized, 8),
                }
            )

        series.append(
            {
                "date": current_date,
                "market_value": round(total_market_value, 8),
                "cost_basis": round(total_cost_basis, 8),
                "unrealized_gain_loss": round(total_market_value - total_cost_basis, 8),
                "holdings": holdings,
            }
        )

    latest = series[-1] if series else {
        "market_value": 0.0,
        "cost_basis": 0.0,
        "unrealized_gain_loss": 0.0,
    }

    return {
        "range": range_key,
        "tickers": tickers,
        "series": series,
        "latest": {
            "market_value": latest.get("market_value", 0.0),
            "cost_basis": latest.get("cost_basis", 0.0),
            "unrealized_gain_loss": latest.get("unrealized_gain_loss", 0.0),
        },
        "positions": {
            ticker: compute_position_summary(transactions_by_ticker.get(ticker, []))
            for ticker in tickers
        },
    }
