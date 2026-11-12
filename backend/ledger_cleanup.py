"""Clean-ups applied to the transaction ledger as it is loaded."""

from __future__ import annotations

from itertools import combinations
from typing import Any

SHARES_TOLERANCE = 1e-6
AMOUNT_TOLERANCE = 0.02


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def remove_split_duplicates(
    transactions: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Drop combined Fidelity rows that a later export lists as split rows.

    Fidelity's history sometimes shows a trade as one row (401.175 shares)
    and, in a later export, as whole and fractional rows (401 + 0.175). The
    import skips exact repeats only, so both versions were kept and the trade
    counted twice: the shares and the cash. A combined row is dropped when
    two or three rows from one other file, same account, date, type and
    price, add up to it exactly. Returns the rows removed (the ledger is
    changed in place).
    """
    removed: list[dict[str, Any]] = []
    for ticker, entries in transactions.items():
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for entry in entries:
            if entry.get("source") != "fidelity-csv" or entry.get("type") not in {"buy", "sell"}:
                continue
            price = _number(entry.get("price_per_share"))
            key = (entry.get("account"), str(entry.get("date") or "")[:10], entry.get("type"), round(price or 0.0, 4))
            groups.setdefault(key, []).append(entry)
        drop: set[int] = set()
        for rows in groups.values():
            if len(rows) < 3:
                continue
            for combined in rows:
                combined_shares = _number(combined.get("shares"))
                if combined_shares is None:
                    continue
                others = [
                    row for row in rows
                    if row is not combined and id(row) not in drop and row.get("source_file") != combined.get("source_file")
                ]
                match = None
                for size in (2, 3):
                    for parts in combinations(others, size):
                        if len({row.get("source_file") for row in parts}) != 1:
                            continue
                        shares = sum(_number(row.get("shares")) or 0.0 for row in parts)
                        if abs(shares - combined_shares) > SHARES_TOLERANCE:
                            continue
                        combined_amount = _number(combined.get("source_amount"))
                        amounts = [_number(row.get("source_amount")) for row in parts]
                        if combined_amount is not None and None not in amounts and abs(sum(amounts) - combined_amount) > AMOUNT_TOLERANCE:
                            continue
                        match = parts
                        break
                    if match:
                        break
                if match:
                    drop.add(id(combined))
                    removed.append({**combined, "ticker": ticker})
        if drop:
            transactions[ticker] = [entry for entry in entries if id(entry) not in drop]
    return removed
