"""Accounts, management modes, transactions and position summary endpoints."""

from __future__ import annotations

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
