"""Accounts, position summaries, and per-position management modes.

Handles account aliases and merging, whether each account/ticker position is
managed, tracked or excluded, and the management snapshot shown in settings."""

from __future__ import annotations

from typing import Any

from backend.config import UNASSIGNED_ACCOUNT, VALID_MANAGEMENT_MODES
from backend.logic import safe_float
from backend.portfolio_service import normalize_portfolio
from backend.state_store import (
    load_account_aliases,
    load_management_modes,
    load_manual_allocations,
    load_transactions,
    save_account_aliases,
    save_management_modes,
    save_manual_allocations,
    save_transactions,
)
from backend.tickers_config import get_effective_tickers_config
from backend.transactions_service import compute_position_summary
from backend.utils import normalize_ticker


def build_position_summaries(
    transactions: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    transactions = transactions if transactions is not None else load_transactions()
    return {
        ticker: compute_position_summary(entries)
        for ticker, entries in transactions.items()
    }


def transaction_backed_tickers_with_shares(
    transactions: dict[str, list[dict[str, Any]]],
) -> set[str]:
    tickers: set[str] = set()
    for ticker, entries in transactions.items():
        if not entries:
            continue
        summary = compute_position_summary(entries)
        if safe_float(summary.get("current_shares"), 0.0) > 0:
            tickers.add(ticker)
    return tickers


def account_matches(
    value: str | None,
    from_account: str,
    source_account_key: str | None = None,
    source_account_keys: set[str] | None = None,
) -> bool:
    account = str(value or "").strip()
    if account.casefold() == from_account.casefold():
        return True

    key = str(source_account_key or "").strip()
    return bool(key and source_account_keys and key in source_account_keys)


def merge_account_records(
    from_account: str,
    to_account: str,
    source_account_keys: list[str] | None = None,
) -> dict[str, Any]:
    from_account = str(from_account or "").strip()
    to_account = str(to_account or "").strip()
    source_keys = {
        str(key).strip()
        for key in (source_account_keys or [])
        if str(key).strip()
    }

    if not from_account or not to_account:
        raise ValueError("Both source and target accounts are required")
    if from_account.casefold() == to_account.casefold():
        raise ValueError("Source and target accounts must be different")

    aliases = load_account_aliases()
    aliases[from_account.casefold()] = to_account
    for key in source_keys:
        aliases[key] = to_account
    save_account_aliases(aliases)

    transactions = load_transactions()
    transaction_updates = 0
    source_key_updates = 0
    for entries in transactions.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_key = str(entry.get("source_account_key") or "").strip()
            if account_matches(
                entry.get("account"),
                from_account,
                source_key,
                source_keys,
            ):
                if entry.get("account") != to_account:
                    entry["account"] = to_account
                    transaction_updates += 1
                if source_key in source_keys:
                    source_key_updates += 1
    if transaction_updates:
        save_transactions(transactions)

    allocations = load_manual_allocations()
    allocation_updates = 0
    for allocation in allocations:
        if account_matches(allocation.get("account"), from_account):
            allocation["account"] = to_account
            allocation_updates += 1
    if allocation_updates:
        save_manual_allocations(allocations)

    settings = load_management_modes()
    account_defaults = settings.get("account_defaults", {})
    if from_account in account_defaults:
        account_defaults.setdefault(to_account, account_defaults[from_account])
        account_defaults.pop(from_account, None)

    position_overrides = settings.get("position_overrides", {})
    updated_overrides: dict[str, str] = {}
    mode_updates = 0
    for key, mode in position_overrides.items():
        account_part, sep, ticker_part = str(key).partition("|")
        if sep and account_part.casefold() == from_account.casefold():
            new_key = management_position_key(to_account, ticker_part)
            updated_overrides.setdefault(new_key, mode)
            mode_updates += 1
        else:
            updated_overrides[key] = mode
    settings["account_defaults"] = account_defaults
    settings["position_overrides"] = updated_overrides
    if mode_updates or allocation_updates or transaction_updates:
        save_management_modes(settings)

    return {
        "from_account": from_account,
        "to_account": to_account,
        "source_account_keys": sorted(source_keys),
        "transactions_updated": transaction_updates,
        "source_key_matches": source_key_updates,
        "manual_allocations_updated": allocation_updates,
        "management_settings_updated": mode_updates,
    }


def apply_account_aliases_to_transactions(
    transactions: dict[str, list[dict[str, Any]]],
    aliases: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    updates = 0
    for entries in transactions.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            source_key = str(entry.get("source_account_key") or "").strip()
            account_name = str(entry.get("account") or "").strip()
            source_name = str(entry.get("source_account") or "").strip()
            alias = (
                aliases.get(source_key)
                or aliases.get(account_name.casefold())
                or aliases.get(source_name.casefold())
            )
            if alias and entry.get("account") != alias:
                entry["account"] = alias
                updates += 1

    return transactions, updates


def management_position_key(account: str, ticker: str) -> str:
    return f"{str(account).strip().casefold()}|{normalize_ticker(ticker)}"


def automatic_account_mode(account: str) -> str:
    return "track" if str(account).strip() == UNASSIGNED_ACCOUNT else "managed"


def get_management_mode(
    settings: dict[str, Any],
    account: str,
    ticker: str,
) -> str:
    override = settings["position_overrides"].get(
        management_position_key(account, ticker),
    )
    if override in VALID_MANAGEMENT_MODES:
        return override
    return settings["account_defaults"].get(account, automatic_account_mode(account))


def get_management_mode_source(
    settings: dict[str, Any],
    account: str,
    ticker: str,
) -> str:
    if management_position_key(account, ticker) in settings["position_overrides"]:
        return "ticker_override"
    if account in settings["account_defaults"]:
        return "account_default"
    return "automatic"


def build_management_snapshot(
    transactions: dict[str, list[dict[str, Any]]] | None = None,
    settings: dict[str, Any] | None = None,
    manual_allocations: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    transactions = transactions if transactions is not None else load_transactions()
    settings = settings if settings is not None else load_management_modes()
    manual_allocations = (
        manual_allocations if manual_allocations is not None else load_manual_allocations()
    )
    config = config if config is not None else get_effective_tickers_config()
    configured_shares = {
        row["ticker"]: float(row.get("shares") or 0.0)
        for row in normalize_portfolio(config.get("portfolio", []))
    }

    positions: list[dict[str, Any]] = []
    transaction_shares_by_ticker: dict[str, float] = {}
    transaction_backed_tickers = {
        ticker
        for ticker, entries in transactions.items()
        if isinstance(entries, list) and entries
    }
    accounts: set[str] = set()

    for ticker, entries in transactions.items():
        entries_by_account: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            account = str(entry.get("account") or "").strip() or UNASSIGNED_ACCOUNT
            entries_by_account.setdefault(account, []).append(entry)
            accounts.add(account)

        for account, account_entries in entries_by_account.items():
            summary = compute_position_summary(account_entries)
            shares = max(float(summary.get("current_shares") or 0.0), 0.0)
            if shares <= 0:
                continue
            transaction_shares_by_ticker[ticker] = (
                transaction_shares_by_ticker.get(ticker, 0.0) + shares
            )
            positions.append(
                {
                    "account": account,
                    "ticker": ticker,
                    "shares": shares,
                    "mode": get_management_mode(settings, account, ticker),
                    "mode_source": get_management_mode_source(settings, account, ticker),
                    "source": "transactions",
                },
            )

    allocations_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for allocation in manual_allocations:
        allocations_by_ticker.setdefault(allocation["ticker"], []).append(allocation)

    accounts.add(UNASSIGNED_ACCOUNT)
    for ticker, total_shares in configured_shares.items():
        if ticker in transaction_backed_tickers:
            continue
        residual = max(
            total_shares - transaction_shares_by_ticker.get(ticker, 0.0),
            0.0,
        )
        remaining = residual
        for allocation in allocations_by_ticker.get(ticker, []):
            allocated_shares = min(float(allocation["shares"]), remaining)
            if allocated_shares <= 0:
                continue
            account = allocation["account"]
            accounts.add(account)
            positions.append(
                {
                    "account": account,
                    "ticker": ticker,
                    "shares": allocated_shares,
                    "mode": get_management_mode(settings, account, ticker),
                    "mode_source": get_management_mode_source(settings, account, ticker),
                    "source": "manual_allocation",
                },
            )
            remaining -= allocated_shares

        if remaining > 0:
            positions.append(
                {
                    "account": UNASSIGNED_ACCOUNT,
                    "ticker": ticker,
                    "shares": remaining,
                    "mode": get_management_mode(settings, UNASSIGNED_ACCOUNT, ticker),
                    "mode_source": get_management_mode_source(
                        settings,
                        UNASSIGNED_ACCOUNT,
                        ticker,
                    ),
                    "source": "configured_residual",
                },
            )

    shares_by_mode: dict[str, dict[str, float]] = {}
    for position in positions:
        ticker = position["ticker"]
        mode = position["mode"]
        bucket = shares_by_mode.setdefault(
            ticker,
            {"managed": 0.0, "track": 0.0, "excluded": 0.0},
        )
        bucket[mode] += float(position["shares"])

    account_rows = [
        {
            "account": account,
            "default_mode": settings["account_defaults"].get(
                account,
                automatic_account_mode(account),
            ),
            "default_source": (
                "saved" if account in settings["account_defaults"] else "automatic"
            ),
            "position_count": sum(1 for row in positions if row["account"] == account),
        }
        for account in sorted(accounts, key=str.casefold)
    ]

    return {
        "accounts": account_rows,
        "positions": sorted(
            positions,
            key=lambda row: (str(row["account"]).casefold(), row["ticker"]),
        ),
        "shares_by_mode": shares_by_mode,
        "manual_allocations": manual_allocations,
    }


def filter_transactions_by_management_mode(
    transactions: dict[str, list[dict[str, Any]]],
    mode: str,
) -> dict[str, list[dict[str, Any]]]:
    if mode not in VALID_MANAGEMENT_MODES:
        return transactions

    settings = load_management_modes()
    filtered: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        rows = []
        for entry in entries:
            account = str(entry.get("account") or "").strip() or UNASSIGNED_ACCOUNT
            if get_management_mode(settings, account, ticker) == mode:
                rows.append(entry)
        if rows:
            filtered[ticker] = rows
    return filtered


def apply_management_shares_to_config(
    config: dict[str, Any],
    shares_by_mode: dict[str, dict[str, float]],
    mode: str,
) -> dict[str, Any]:
    if mode not in VALID_MANAGEMENT_MODES:
        return config

    portfolio = []
    for item in normalize_portfolio(config.get("portfolio", [])):
        ticker = item["ticker"]
        shares = float(shares_by_mode.get(ticker, {}).get(mode, 0.0))
        if shares > 0:
            portfolio.append({"ticker": ticker, "shares": shares})

    return {
        "portfolio": portfolio,
        "watchlist": config.get("watchlist", []) or [],
    }
