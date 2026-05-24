from __future__ import annotations

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_position_summary(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Simple average-cost position math.

    Supported reasonably well:
    - buy
    - sell
    - transfer_in
    - transfer_out

    Supported in a simplified way:
    - dividend      -> uses price_per_share as cash amount if provided
    - split         -> uses shares as split multiplier (e.g. 2.0 for 2-for-1)
    - adjustment    -> positive share adjustment, optional price_per_share

    Returns a summary dict for use by the API/UI.
    """
    rows = [tx for tx in transactions if isinstance(tx, dict)]

    # Stable sort by date, then id for deterministic replay.
    rows = sorted(
        rows,
        key=lambda tx: (
            str(tx.get("date", "")),
            str(tx.get("id", "")),
        ),
    )

    current_shares = 0.0
    total_cost_basis = 0.0
    realized_gain_loss = 0.0
    dividend_cash = 0.0
    warnings: list[str] = []

    for tx in rows:
        tx_type = str(tx.get("type", "")).strip().lower()
        shares = _safe_float(tx.get("shares"), 0.0)
        price_per_share = tx.get("price_per_share")
        price_per_share = None if price_per_share in (None, "") else _safe_float(price_per_share, 0.0)
        fees = _safe_float(tx.get("fees"), 0.0)

        avg_cost = (total_cost_basis / current_shares) if current_shares > 0 else 0.0

        if tx_type == "buy":
            if shares <= 0:
                continue
            if price_per_share is None:
                warnings.append(f"Buy transaction {tx.get('id')} missing price_per_share")
                continue
            current_shares += shares
            total_cost_basis += (shares * price_per_share) + fees

        elif tx_type == "transfer_in":
            if shares <= 0:
                continue
            current_shares += shares
            if price_per_share is not None:
                total_cost_basis += (shares * price_per_share) + fees
            else:
                total_cost_basis += fees

        elif tx_type == "sell":
            if shares <= 0:
                continue
            if price_per_share is None:
                warnings.append(f"Sell transaction {tx.get('id')} missing price_per_share")
                continue
            if shares > current_shares + 1e-9:
                warnings.append(f"Sell transaction {tx.get('id')} exceeds current shares")
                continue

            proceeds = (shares * price_per_share) - fees
            cost_removed = shares * avg_cost
            realized_gain_loss += proceeds - cost_removed

            current_shares -= shares
            total_cost_basis -= cost_removed

        elif tx_type == "transfer_out":
            if shares <= 0:
                continue
            if shares > current_shares + 1e-9:
                warnings.append(f"Transfer out transaction {tx.get('id')} exceeds current shares")
                continue

            cost_removed = shares * avg_cost
            current_shares -= shares
            total_cost_basis -= cost_removed

        elif tx_type == "dividend":
            # Simplified: treat price_per_share as the cash amount if provided.
            # If you later want per-share dividend math, we can refine this.
            dividend_cash += _safe_float(price_per_share, 0.0)

        elif tx_type == "split":
            # Simplified: use `shares` as the split multiplier.
            # Example: 2.0 means a 2-for-1 split.
            if shares > 0 and current_shares > 0:
                current_shares *= shares
                # total_cost_basis unchanged

        elif tx_type == "adjustment":
            # Simplified positive-share adjustment.
            if shares <= 0:
                continue
            current_shares += shares
            if price_per_share is not None:
                total_cost_basis += (shares * price_per_share) + fees
            else:
                total_cost_basis += fees

        else:
            warnings.append(f"Unsupported transaction type: {tx_type}")

        # Clamp tiny negative float drift
        if abs(current_shares) < 1e-10:
            current_shares = 0.0
        if abs(total_cost_basis) < 1e-10:
            total_cost_basis = 0.0

    average_cost_per_share = (
        total_cost_basis / current_shares if current_shares > 0 else 0.0
    )

    return {
        "current_shares": round(current_shares, 8),
        "total_cost_basis": round(total_cost_basis, 8),
        "average_cost_per_share": round(average_cost_per_share, 8),
        "realized_gain_loss": round(realized_gain_loss, 8),
        "dividend_cash": round(dividend_cash, 8),
        "transaction_count": len(rows),
        "warnings": warnings,
    }