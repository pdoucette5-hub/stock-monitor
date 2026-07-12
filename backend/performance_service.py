from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.price_store import get_price_points_for_ticker, load_price_history_store
from backend.transactions_service import compute_position_summary

UNASSIGNED_ACCOUNT = "Unassigned"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _transaction_account(tx: dict[str, Any]) -> str:
    account = str(tx.get("account") or "").strip()
    return account or UNASSIGNED_ACCOUNT


def _normalize_account_filter(accounts: list[str] | None) -> set[str]:
    return {
        str(account).strip().lower()
        for account in (accounts or [])
        if str(account).strip()
    }


def _filter_transactions_by_accounts(
    transactions_by_ticker: dict[str, list[dict[str, Any]]],
    accounts: list[str] | None,
) -> dict[str, list[dict[str, Any]]]:
    selected = _normalize_account_filter(accounts)
    if not selected:
        return transactions_by_ticker

    filtered: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions_by_ticker.items():
        rows = [
            entry
            for entry in entries
            if isinstance(entry, dict) and _transaction_account(entry).lower() in selected
        ]
        if rows:
            filtered[ticker] = rows

    return filtered


def _account_summary(
    transactions_by_ticker: dict[str, list[dict[str, Any]]],
    selected_accounts: list[str] | None,
    supplemental_positions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    selected = _normalize_account_filter(selected_accounts)
    counts: dict[str, int] = {}

    for entries in transactions_by_ticker.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            account = _transaction_account(entry)
            counts[account] = counts.get(account, 0) + 1

    for position in supplemental_positions or []:
        account = str(position.get("account") or "").strip()
        if account:
            counts.setdefault(account, 0)

    return [
        {
            "account": account,
            "transaction_count": counts[account],
            "selected": not selected or account.lower() in selected,
        }
        for account in sorted(counts)
    ]


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


def _empty_position_snapshot() -> dict[str, float]:
    return {
        "shares": 0.0,
        "cost_basis": 0.0,
        "realized_gain_loss": 0.0,
        "dividend_cash": 0.0,
    }


def _portfolio_positions(tickers_config: dict[str, Any]) -> dict[str, float]:
    portfolio_items = tickers_config.get("portfolio", []) or []
    positions: dict[str, float] = {}

    for item in portfolio_items:
        if isinstance(item, dict):
            ticker = str(item.get("ticker", "")).strip().upper()
            shares = _safe_float(item.get("shares"), 0.0)
        else:
            ticker = str(item).strip().upper()
            shares = 0.0
        if ticker:
            positions[ticker] = shares

    return positions


def _fallback_position_summary(shares: float) -> dict[str, Any]:
    return {
        "current_shares": round(shares, 8),
        "total_cost_basis": 0.0,
        "average_cost_per_share": 0.0,
        "realized_gain_loss": 0.0,
        "dividend_cash": 0.0,
        "transaction_count": 0,
        "warnings": ["Using current portfolio shares because no transactions are recorded"],
    }


def build_portfolio_performance(
    transactions_by_ticker: dict[str, list[dict[str, Any]]],
    tickers_config: dict[str, Any],
    range_key: str = "3y",
    accounts: list[str] | None = None,
    supplemental_positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    account_rows = _account_summary(
        transactions_by_ticker,
        accounts,
        supplemental_positions,
    )
    filtered_transactions = _filter_transactions_by_accounts(
        transactions_by_ticker,
        accounts,
    )
    portfolio_positions = _portfolio_positions(tickers_config)
    account_filter_active = bool(_normalize_account_filter(accounts))
    selected_accounts = _normalize_account_filter(accounts)
    supplemental_shares: dict[str, float] = {}
    for position in supplemental_positions or []:
        account = str(position.get("account") or "").strip()
        if account_filter_active and account.lower() not in selected_accounts:
            continue
        ticker = str(position.get("ticker") or "").strip().upper()
        shares = max(_safe_float(position.get("shares"), 0.0), 0.0)
        if ticker and shares > 0:
            supplemental_shares[ticker] = supplemental_shares.get(ticker, 0.0) + shares

    transaction_tickers = set(filtered_transactions)
    supplemental_tickers = {
        ticker
        for ticker, shares in supplemental_shares.items()
        if shares > 0
    }
    configured_tickers = {
        ticker
        for ticker, shares in portfolio_positions.items()
        if shares > 0
    }

    if account_filter_active:
        tickers = sorted(transaction_tickers | supplemental_tickers)
    else:
        tickers = sorted(configured_tickers | transaction_tickers | supplemental_tickers)

    history_by_ticker: dict[str, list[dict[str, Any]]] = {}
    position_history_by_ticker: dict[str, dict[str, dict[str, float]]] = {}
    all_dates: set[str] = set()
    price_store = load_price_history_store()

    for ticker in tickers:
        points = get_price_points_for_ticker(
            ticker,
            range_key=range_key,
            store=price_store,
        )
        if not points:
            continue

        history_by_ticker[ticker] = points
        for point in points:
            if point.get("date"):
                all_dates.add(str(point["date"]))

        transaction_rows = filtered_transactions.get(ticker, [])
        supplemental = supplemental_shares.get(ticker, 0.0)
        if transaction_rows:
            position_history = _build_position_history(transaction_rows)
            for snapshot in position_history.values():
                snapshot["shares"] = _safe_float(snapshot.get("shares"), 0.0) + supplemental
            if supplemental > 0:
                first_date = str(points[0]["date"])
                position_history.setdefault(
                    first_date,
                    {
                        "shares": supplemental,
                        "cost_basis": 0.0,
                        "realized_gain_loss": 0.0,
                        "dividend_cash": 0.0,
                    },
                )
            position_history_by_ticker[ticker] = position_history
        else:
            shares = (
                supplemental
                if account_filter_active
                else _safe_float(portfolio_positions.get(ticker), 0.0)
            )
            position_history_by_ticker[ticker] = {
                str(points[0]["date"]): {
                    "shares": shares,
                    "cost_basis": 0.0,
                    "realized_gain_loss": 0.0,
                    "dividend_cash": 0.0,
                },
            }

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
                ticker: (
                    compute_position_summary(filtered_transactions.get(ticker, []))
                    if filtered_transactions.get(ticker)
                    else _fallback_position_summary(
                        _safe_float(portfolio_positions.get(ticker), 0.0),
                    )
                )
                for ticker in tickers
            },
            "accounts": account_rows,
        }

    close_lookup: dict[str, dict[str, float]] = defaultdict(dict)
    for ticker, points in history_by_ticker.items():
        for point in points:
            if point.get("date") and point.get("close") is not None:
                close_lookup[ticker][str(point["date"])] = _safe_float(point["close"], 0.0)

    snapshot_items_by_ticker = {
        ticker: sorted(snapshots.items())
        for ticker, snapshots in position_history_by_ticker.items()
    }
    snapshot_index_by_ticker = {ticker: 0 for ticker in tickers}
    current_snapshot_by_ticker = {ticker: _empty_position_snapshot() for ticker in tickers}
    series: list[dict[str, Any]] = []

    for current_date in ordered_dates:
        total_market_value = 0.0
        total_cost_basis = 0.0

        for ticker in tickers:
            snapshot_items = snapshot_items_by_ticker.get(ticker, [])
            snapshot_index = snapshot_index_by_ticker.get(ticker, 0)
            while (
                snapshot_index < len(snapshot_items)
                and snapshot_items[snapshot_index][0] <= current_date
            ):
                current_snapshot_by_ticker[ticker] = snapshot_items[snapshot_index][1]
                snapshot_index += 1
            snapshot_index_by_ticker[ticker] = snapshot_index

            close = close_lookup[ticker].get(current_date)
            if close is None:
                continue

            snapshot = current_snapshot_by_ticker.get(ticker) or _empty_position_snapshot()
            shares = _safe_float(snapshot.get("shares"), 0.0)
            cost_basis = _safe_float(snapshot.get("cost_basis"), 0.0)

            if shares <= 0:
                continue

            market_value = shares * close

            total_market_value += market_value
            total_cost_basis += cost_basis

        series.append(
            {
                "date": current_date,
                "market_value": round(total_market_value, 8),
                "cost_basis": round(total_cost_basis, 8),
                "unrealized_gain_loss": round(total_market_value - total_cost_basis, 8),
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
            ticker: (
                compute_position_summary(filtered_transactions.get(ticker, []))
                if filtered_transactions.get(ticker)
                else _fallback_position_summary(
                    _safe_float(portfolio_positions.get(ticker), 0.0),
                )
            )
            for ticker in tickers
        },
        "accounts": account_rows,
    }
