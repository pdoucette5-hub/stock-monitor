"""Parsing Fidelity transaction exports and validating transaction payloads."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from backend.config import VALID_TRANSACTION_TYPES
from backend.schemas import TransactionCreate, TransactionImportFile, TransactionUpdate
from backend.state_store import load_account_aliases
from backend.utils import normalize_ticker


def parse_import_float(value: Any, default: float = 0.0) -> float:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def normalize_import_date(value: Any) -> str:
    text = str(value or "").strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def transaction_import_fingerprint(parts: list[Any]) -> str:
    normalized = "|".join(str(part or "").strip().casefold() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_fidelity_transaction_files(
    files: list[TransactionImportFile],
    account_mappings: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    aliases = load_account_aliases()
    aliases.update(
        {
            str(key).strip(): str(value).strip()
            for key, value in account_mappings.items()
            if str(key).strip() and str(value).strip()
        },
    )

    rows: list[dict[str, Any]] = []
    accounts: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for uploaded in files:
        reader = csv.reader(io.StringIO(uploaded.content.lstrip("\ufeff")))
        raw_rows = list(reader)
        header_index = next(
            (
                idx
                for idx, row in enumerate(raw_rows)
                if row and str(row[0]).strip() == "Run Date"
            ),
            None,
        )

        if header_index is None:
            skipped.append(
                {
                    "file": uploaded.name,
                    "reason": "Unsupported CSV: Run Date header was not found",
                },
            )
            continue

        header = [str(value).strip() for value in raw_rows[header_index]]
        for row_number, raw_row in enumerate(raw_rows[header_index + 1 :], header_index + 2):
            if not raw_row or not any(str(value).strip() for value in raw_row):
                continue

            padded = list(raw_row) + [""] * max(0, len(header) - len(raw_row))
            record = dict(zip(header, padded))
            action = str(record.get("Action") or "").strip()
            run_date = str(record.get("Run Date") or "").strip()

            if not action and not normalize_import_date(run_date):
                continue

            action_upper = action.upper()
            if action_upper.startswith("YOU BOUGHT") or action_upper.startswith("REINVESTMENT"):
                tx_type = "buy"
            elif action_upper.startswith("YOU SOLD"):
                tx_type = "sell"
            elif any(
                marker in action_upper
                for marker in (
                    "DIVIDEND RECEIVED",
                    "LONG-TERM CAP GAIN",
                    "SHORT-TERM CAP GAIN",
                )
            ):
                tx_type = "dividend"
            else:
                skipped.append(
                    {
                        "file": uploaded.name,
                        "row": row_number,
                        "reason": "Not a supported trade or distribution transaction",
                        "action": action,
                    },
                )
                continue

            ticker = normalize_ticker(record.get("Symbol") or "")
            date = normalize_import_date(record.get("Run Date"))
            shares = abs(parse_import_float(record.get("Quantity")))
            price = abs(parse_import_float(record.get("Price ($)")))
            fees = abs(parse_import_float(record.get("Commission ($)"))) + abs(
                parse_import_float(record.get("Fees ($)")),
            )
            amount = parse_import_float(record.get("Amount ($)"))
            account_name = str(record.get("Account") or "").strip()
            account_number = str(record.get("Account Number") or "").strip()
            account_key = account_number or account_name.casefold()
            account = aliases.get(account_key) or account_name or account_number

            valid_amounts = (
                abs(amount) > 0
                if tx_type == "dividend"
                else shares > 0 and price > 0
            )
            if not ticker or not date or not valid_amounts or not account_key:
                skipped.append(
                    {
                        "file": uploaded.name,
                        "row": row_number,
                        "reason": "Missing ticker, date, transaction value, or account",
                        "action": action,
                    },
                )
                continue

            accounts.setdefault(
                account_key,
                {
                    "key": account_key,
                    "source_name": account_name or "Unnamed account",
                    "account_number_last4": account_number[-4:] if account_number else "",
                    "nickname": account,
                },
            )

            fingerprint = transaction_import_fingerprint(
                [
                    "fidelity",
                    account_key,
                    date,
                    action,
                    ticker,
                    record.get("Price ($)"),
                    record.get("Quantity"),
                    record.get("Commission ($)"),
                    record.get("Fees ($)"),
                    record.get("Amount ($)"),
                    record.get("Settlement Date"),
                ],
            )

            rows.append(
                {
                    "ticker": ticker,
                    "date": date,
                    "type": tx_type,
                    "shares": 0.0 if tx_type == "dividend" else shares,
                    "price_per_share": abs(amount) if tx_type == "dividend" else price,
                    "fees": fees,
                    "account": account,
                    "notes": str(record.get("Description") or action).strip(),
                    "source": "fidelity-csv",
                    "source_file": uploaded.name,
                    "source_account": account_name,
                    "source_account_key": account_key,
                    "source_amount": amount,
                    "settlement_date": normalize_import_date(record.get("Settlement Date")),
                    "import_fingerprint": fingerprint,
                },
            )

    return rows, list(accounts.values()), skipped


def existing_import_fingerprints(
    transactions: dict[str, list[dict[str, Any]]],
) -> set[str]:
    return {
        str(entry.get("import_fingerprint"))
        for entries in transactions.values()
        for entry in entries
        if isinstance(entry, dict) and entry.get("import_fingerprint")
    }


def validate_transaction_payload(payload: TransactionCreate | TransactionUpdate) -> dict[str, Any]:
    tx_type = str(payload.type).strip().lower()
    if tx_type not in VALID_TRANSACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transaction type. Must be one of: {', '.join(sorted(VALID_TRANSACTION_TYPES))}",
        )

    if payload.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    price_per_share = payload.price_per_share
    if price_per_share is not None and price_per_share < 0:
        raise HTTPException(status_code=400, detail="Price per share must be non-negative")

    fees = payload.fees if payload.fees is not None else 0.0
    if fees < 0:
        raise HTTPException(status_code=400, detail="Fees must be non-negative")

    return {
        "date": str(payload.date).strip(),
        "type": tx_type,
        "shares": float(payload.shares),
        "price_per_share": None if price_per_share is None else float(price_per_share),
        "fees": float(fees),
        "account": str(payload.account or "").strip(),
        "notes": str(payload.notes or "").strip(),
    }
