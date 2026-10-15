"""Accounts, management modes, transactions and position summary endpoints."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Header

from backend.config import (
    PRICE_IMPORT_SECRET,
    UNASSIGNED_ACCOUNT,
    VALID_MANAGEMENT_MODES,
)
from backend.management_service import (
    apply_account_aliases_to_transactions,
    filter_transactions_by_management_mode,
    automatic_account_mode,
    build_management_snapshot,
    management_position_key,
    merge_account_records,
    transaction_backed_tickers_with_shares,
)
from backend.schemas import (
    AccountMergeRequest,
    ManagementModesUpdate,
    ManualAllocationUpdate,
    TransactionCreate,
    TransactionImportRequest,
    TransactionUpdate,
)
from backend.state_store import (
    append_portfolio_event,
    load_account_aliases,
    load_management_modes,
    load_manual_allocations,
    load_tickers_overrides,
    load_transactions,
    save_account_aliases,
    save_management_modes,
    save_manual_allocations,
    save_tickers_overrides,
    save_transactions,
)
from backend.tickers_config import get_effective_tickers_config
from backend.transaction_import import (
    existing_import_fingerprints,
    parse_fidelity_transaction_files,
    validate_transaction_payload,
)
from backend.transactions_service import compute_position_summary
from backend.utils import normalize_ticker


router = APIRouter()


@router.get("/api/accounts")
def get_transaction_accounts() -> dict[str, Any]:
    transactions = load_transactions()
    accounts_by_key: dict[str, str] = {}

    for entries in transactions.values():
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            account = str(entry.get("account") or "").strip()
            if account:
                accounts_by_key.setdefault(account.casefold(), account)

    for allocation in load_manual_allocations():
        account = str(allocation.get("account") or "").strip()
        if account:
            accounts_by_key.setdefault(account.casefold(), account)

    accounts = sorted(accounts_by_key.values(), key=str.casefold)
    return {
        "count": len(accounts),
        "accounts": accounts,
    }


@router.get("/api/management")
def get_management_settings() -> dict[str, Any]:
    return build_management_snapshot()


@router.put("/api/management")
def update_management_settings(body: ManagementModesUpdate) -> dict[str, Any]:
    settings = load_management_modes()

    for update in body.updates:
        account = str(update.account or "").strip()
        ticker = normalize_ticker(update.ticker or "")
        mode = str(update.mode or "").strip().lower()

        if not account:
            raise HTTPException(status_code=400, detail="Account is required")
        if mode not in VALID_MANAGEMENT_MODES:
            raise HTTPException(
                status_code=400,
                detail="Mode must be managed, track, or excluded",
            )

        if ticker:
            key = management_position_key(account, ticker)
            account_default = settings["account_defaults"].get(
                account,
                automatic_account_mode(account),
            )
            if mode == account_default:
                settings["position_overrides"].pop(key, None)
            else:
                settings["position_overrides"][key] = mode
        else:
            settings["account_defaults"][account] = mode

    save_management_modes(settings)
    append_portfolio_event(
        "update_management_modes",
        "",
        {"updates": [update.model_dump(mode="json") for update in body.updates]},
    )
    return build_management_snapshot()


@router.put("/api/management/allocation")
def update_manual_allocation(body: ManualAllocationUpdate) -> dict[str, Any]:
    ticker = normalize_ticker(body.ticker)
    account = str(body.account or "").strip()
    mode = str(body.mode or "").strip().lower()
    shares = float(body.shares)

    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if not account or account == UNASSIGNED_ACCOUNT:
        raise HTTPException(status_code=400, detail="Choose a named account")
    if shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")
    if mode not in VALID_MANAGEMENT_MODES:
        raise HTTPException(
            status_code=400,
            detail="Mode must be managed, track, or excluded",
        )

    snapshot = build_management_snapshot()
    existing_allocations = load_manual_allocations()
    existing_shares = sum(
        float(row["shares"])
        for row in snapshot["positions"]
        if row["ticker"] == ticker
        and row["account"].casefold() == account.casefold()
        and row["source"] == "manual_allocation"
    )
    unassigned_shares = sum(
        float(row["shares"])
        for row in snapshot["positions"]
        if row["ticker"] == ticker and row["account"] == UNASSIGNED_ACCOUNT
    )
    if shares > unassigned_shares + existing_shares + 1e-8:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Only {unassigned_shares + existing_shares:.4f} residual "
                f"{ticker} shares are available"
            ),
        )

    allocations = [
        row
        for row in existing_allocations
        if not (
            row["ticker"] == ticker
            and row["account"].casefold() == account.casefold()
        )
    ]
    if shares > 0:
        allocations.append({"ticker": ticker, "account": account, "shares": shares})
    save_manual_allocations(allocations)

    settings = load_management_modes()
    settings["position_overrides"][management_position_key(account, ticker)] = mode
    save_management_modes(settings)

    append_portfolio_event(
        "update_manual_allocation",
        ticker,
        {
            "account": account,
            "shares": shares,
            "mode": mode,
        },
    )
    return build_management_snapshot()


@router.put("/api/accounts/merge")
def merge_accounts(
    body: AccountMergeRequest,
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    if not PRICE_IMPORT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="PRICE_IMPORT_SECRET is not configured on the server",
        )
    if x_import_secret != PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid import secret")

    try:
        result = merge_account_records(
            body.from_account,
            body.to_account,
            body.source_account_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    append_portfolio_event(
        "merge_accounts",
        "",
        result,
    )
    return {
        "status": "ok",
        **result,
    }


@router.post("/api/transactions/import")
def import_transactions(body: TransactionImportRequest) -> dict[str, Any]:
    if not body.files:
        raise HTTPException(status_code=400, detail="Choose at least one CSV file")

    parsed_rows, accounts, skipped = parse_fidelity_transaction_files(
        body.files,
        body.account_mappings,
    )
    transactions = load_transactions()
    known_fingerprints = existing_import_fingerprints(transactions)
    batch_fingerprints: set[str] = set()
    preview_rows: list[dict[str, Any]] = []
    importable_rows: list[dict[str, Any]] = []

    for row in parsed_rows:
        fingerprint = str(row.get("import_fingerprint") or "")
        is_duplicate = fingerprint in known_fingerprints or fingerprint in batch_fingerprints
        batch_fingerprints.add(fingerprint)

        preview_rows.append(
            {
                "ticker": row["ticker"],
                "date": row["date"],
                "type": row["type"],
                "shares": row["shares"],
                "price_per_share": row["price_per_share"],
                "fees": row["fees"],
                "account": row["account"],
                "source_account": row["source_account"],
                "source_file": row["source_file"],
                "status": (
                    "duplicate"
                    if is_duplicate
                    else "imported"
                    if body.commit
                    else "ready"
                ),
            },
        )

        if not is_duplicate:
            importable_rows.append(row)

    if body.commit:
        aliases = load_account_aliases()
        for account in accounts:
            key = str(account.get("key") or "").strip()
            nickname = str(account.get("nickname") or "").strip()
            if key and nickname:
                aliases[key] = nickname
        save_account_aliases(aliases)
        transactions, alias_updates = apply_account_aliases_to_transactions(
            transactions,
            aliases,
        )
        if alias_updates:
            save_transactions(transactions)

    if body.commit and importable_rows:
        for row in importable_rows:
            ticker = row["ticker"]
            stored_row = {key: value for key, value in row.items() if key != "ticker"}
            stored_row["id"] = uuid.uuid4().hex
            transactions.setdefault(ticker, []).append(stored_row)

        save_transactions(transactions)

    if body.commit:
        effective_config = get_effective_tickers_config()
        portfolio_tickers = {
            normalize_ticker(row.get("ticker") if isinstance(row, dict) else row)
            for row in effective_config.get("portfolio", [])
        }
        new_portfolio_tickers = sorted(
            ticker
            for ticker in transaction_backed_tickers_with_shares(transactions)
            if ticker not in portfolio_tickers
        )
        if new_portfolio_tickers:
            overrides = load_tickers_overrides()
            tickers_map = overrides.setdefault("tickers", {})
            for ticker in new_portfolio_tickers:
                tickers_map[ticker] = {
                    "list": "portfolio",
                    "shares": None,
                    "archived": False,
                    "removed": False,
                }
            save_tickers_overrides(overrides)

        append_portfolio_event(
            "import_transactions",
            "",
            {
                "imported": len(importable_rows),
                "duplicates_skipped": len(parsed_rows) - len(importable_rows),
                "files": [uploaded.name for uploaded in body.files],
                "accounts": sorted(
                    {
                        str(row.get("account") or "").strip()
                        for row in importable_rows
                        if str(row.get("account") or "").strip()
                    },
                ),
            },
        )

    return {
        "status": "imported" if body.commit else "preview",
        "summary": {
            "parsed": len(parsed_rows),
            "ready": len(importable_rows),
            "duplicates": len(parsed_rows) - len(importable_rows),
            "skipped": len(skipped),
            "imported": len(importable_rows) if body.commit else 0,
        },
        "accounts": accounts,
        "rows": preview_rows,
        "skipped": skipped,
    }


# Transaction types that change how many shares are held.
SHARE_CHANGING_TYPES = {"buy", "sell", "transfer_in", "transfer_out", "adjustment"}

# Money market funds that hold an account's uninvested cash. Buying or selling
# them just moves cash around; their dividends are interest on the cash.
CASH_TICKERS = {"SPAXX", "FDRXX", "FZFXX", "SPRXX", "FCASH", "CORE", "USD", "CASH"}


def _cash_amount(entry: dict[str, Any]) -> float:
    """Cash into (+) or out of (-) the account for one transaction."""
    tx_type = str(entry.get("type", "")).strip().lower()
    source_amount = entry.get("source_amount")
    try:
        if source_amount not in (None, "") and tx_type in {"buy", "sell", "dividend"}:
            return float(source_amount)
    except (TypeError, ValueError):
        pass
    try:
        shares = abs(float(entry.get("shares") or 0))
        price = float(entry.get("price_per_share") or 0)
        fees = float(entry.get("fees") or 0)
    except (TypeError, ValueError):
        return 0.0
    if tx_type == "buy":
        return -(shares * price + fees)
    if tx_type == "sell":
        return shares * price - fees
    if tx_type == "dividend":
        return abs(price)
    return 0.0


def _cash_event_order(entry: dict[str, Any]) -> tuple[str, int]:
    # Within a day, money comes in (sales, dividends) before it is spent.
    return (str(entry.get("date") or "")[:10], 1 if _cash_amount(entry) < 0 else 0)


def cash_balances_through(transactions: dict[str, list[dict[str, Any]]], through: str) -> dict[str, float]:
    """Uninvested cash per account at the end of `through`, from the ledger.

    Sale proceeds and dividends add cash; buys spend it. A buy costing more
    than the cash on hand was paid with new money, so the balance never goes
    below zero. Moves in and out of the money market fund are ignored (they
    are the cash).
    """
    events = []
    for ticker, entries in transactions.items():
        is_cash_fund = normalize_ticker(ticker) in CASH_TICKERS
        for entry in entries:
            tx_type = str(entry.get("type", "")).strip().lower()
            date = str(entry.get("date") or "")[:10]
            if not date or date > through or tx_type not in {"buy", "sell", "dividend"}:
                continue
            if is_cash_fund and tx_type != "dividend":
                continue
            events.append(entry)
    balances: dict[str, float] = {}
    for entry in sorted(events, key=_cash_event_order):
        account = str(entry.get("account") or "").strip() or UNASSIGNED_ACCOUNT
        balances[account] = max(balances.get(account, 0.0) + _cash_amount(entry), 0.0)
    return {account: round(balance, 2) for account, balance in balances.items() if balance > 0.004}


@router.get("/api/transactions/window")
def get_transactions_window(since: str, modes: str = "all") -> dict[str, Any]:
    """Trades, dividends and cash for the Holdings summary.

    - trades: share-changing trades and dividends on or after `since`, per
      ticker, each with its account and cash amount (money market fund
      activity is left out of trades; its dividends are reported as interest).
    - cash_start: uninvested cash per account at the end of `since`.
    `modes` is a comma-separated list of management modes (managed, track,
    excluded) or "all".
    """
    try:
        since_date = datetime.date.fromisoformat(str(since).strip()[:10]).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="since must be a date (YYYY-MM-DD)") from exc
    requested = [mode.strip().lower() for mode in str(modes or "all").split(",") if mode.strip()]
    for mode in requested:
        if mode != "all" and mode not in VALID_MANAGEMENT_MODES:
            raise HTTPException(status_code=400, detail="Mode must be all, managed, track, or excluded")

    transactions = load_transactions()
    if requested and "all" not in requested:
        selected: dict[str, list[dict[str, Any]]] = {}
        for mode in requested:
            for ticker, entries in filter_transactions_by_management_mode(transactions, mode).items():
                selected.setdefault(ticker, []).extend(entries)
        transactions = selected
    # Drop duplicates (a transaction can only match one mode, but be safe).
    deduped: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        seen: set[str] = set()
        for entry in entries:
            key = str(entry.get("id") or id(entry))
            if key not in seen:
                seen.add(key)
                deduped.setdefault(normalize_ticker(ticker), []).append(entry)
    transactions = deduped

    trades: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        is_cash_fund = ticker in CASH_TICKERS
        for entry in entries:
            tx_type = str(entry.get("type", "")).strip().lower()
            tx_date = str(entry.get("date") or "")[:10]
            if tx_date < since_date:
                continue
            if tx_type == "dividend":
                kind = "interest" if is_cash_fund else "dividend"
            elif tx_type in SHARE_CHANGING_TYPES and not is_cash_fund:
                kind = tx_type
            else:
                continue
            trades.setdefault(ticker, []).append({
                "date": tx_date,
                "type": kind,
                "shares": entry.get("shares"),
                "price_per_share": entry.get("price_per_share"),
                "account": str(entry.get("account") or "").strip() or UNASSIGNED_ACCOUNT,
                "cash": round(_cash_amount(entry), 2),
            })
    for entries in trades.values():
        entries.sort(key=lambda row: row["date"])
    return {
        "since": since_date,
        "trades": trades,
        "cash_start": cash_balances_through(transactions, since_date),
        "cash_tickers": sorted(CASH_TICKERS),
    }


@router.get("/api/transactions/{ticker}")
def get_transactions_for_ticker(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    transactions = load_transactions()
    return {
        "ticker": normalized,
        "transactions": transactions.get(normalized, []),
    }


@router.post("/api/transactions/{ticker}")
def create_transaction_for_ticker(ticker: str, body: TransactionCreate) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    tx = validate_transaction_payload(body)
    tx["id"] = uuid.uuid4().hex

    transactions = load_transactions()
    entries = transactions.setdefault(normalized, [])
    entries.append(tx)
    save_transactions(transactions)

    append_portfolio_event(
        "create_transaction",
        normalized,
        {
            "transaction_id": tx["id"],
            "transaction_type": tx["type"],
            "shares": tx["shares"],
            "date": tx["date"],
            "account": tx.get("account") or "",
        },
    )

    return {
        "ticker": normalized,
        "transaction": tx,
        "transactions": entries,
    }


@router.put("/api/transactions/{ticker}/{transaction_id}")
def update_transaction_for_ticker(
    ticker: str,
    transaction_id: str,
    body: TransactionUpdate,
) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    transactions = load_transactions()
    entries = transactions.get(normalized, [])

    for idx, entry in enumerate(entries):
        if str(entry.get("id")) == str(transaction_id):
            updated = validate_transaction_payload(body)
            updated["id"] = str(transaction_id)
            for key in (
                "source",
                "source_file",
                "source_account",
                "source_account_key",
                "source_amount",
                "settlement_date",
                "import_fingerprint",
            ):
                if key in entry:
                    updated[key] = entry[key]
            entries[idx] = updated
            transactions[normalized] = entries
            save_transactions(transactions)

            append_portfolio_event(
                "update_transaction",
                normalized,
                {
                    "transaction_id": str(transaction_id),
                    "transaction_type": updated["type"],
                    "shares": updated["shares"],
                    "date": updated["date"],
                    "account": updated.get("account") or "",
                },
            )

            return {
                "ticker": normalized,
                "transaction": updated,
                "transactions": entries,
            }

    raise HTTPException(status_code=404, detail="Transaction not found")


@router.delete("/api/transactions/{ticker}/{transaction_id}")
def delete_transaction_for_ticker(ticker: str, transaction_id: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    transactions = load_transactions()
    entries = transactions.get(normalized, [])
    filtered = [entry for entry in entries if str(entry.get("id")) != str(transaction_id)]

    if len(filtered) == len(entries):
        raise HTTPException(status_code=404, detail="Transaction not found")

    transactions[normalized] = filtered
    save_transactions(transactions)

    append_portfolio_event(
        "delete_transaction",
        normalized,
        {
            "transaction_id": str(transaction_id),
        },
    )

    return {
        "ticker": normalized,
        "transactions": filtered,
    }


@router.get("/api/position/{ticker}")
def get_position_summary(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    transactions = load_transactions()
    entries = transactions.get(normalized, [])

    summary = compute_position_summary(entries)

    return {
        "ticker": normalized,
        "summary": summary,
        "transactions": entries,
    }
