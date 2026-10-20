from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent.parent
INTRADAY_FILE = BASE_DIR / "cache" / "intraday_prices.json"
INTRADAY_GCS_BUCKET = os.getenv("PRICE_HISTORY_GCS_BUCKET", "")
INTRADAY_GCS_BLOB = os.getenv(
    "INTRADAY_PRICE_GCS_BLOB",
    "stock-monitor/intraday_prices.json",
)


def _load_raw() -> Any:
    if INTRADAY_GCS_BUCKET:
        try:
            from google.cloud import storage

            blob = storage.Client().bucket(INTRADAY_GCS_BUCKET).blob(INTRADAY_GCS_BLOB)
            if blob.exists():
                return json.loads(blob.download_as_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        return json.loads(INTRADAY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_intraday_prices() -> dict[str, list[dict[str, Any]]]:
    raw = _load_raw()
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    for ticker, rows in raw.items():
        valid = []
        for row in rows if isinstance(rows, list) else []:
            try:
                timestamp = int(row.get("timestamp"))
                close = float(row.get("close"))
            except (AttributeError, TypeError, ValueError):
                continue
            if timestamp >= cutoff and close > 0:
                valid.append({"timestamp": timestamp, "date": str(row.get("date") or ""), "close": close})
        cleaned[str(ticker).strip().upper()] = sorted(valid, key=lambda row: row["timestamp"])
    return cleaned


def save_intraday_prices(store: dict[str, list[dict[str, Any]]]) -> None:
    payload = json.dumps(store, indent=2)
    INTRADAY_FILE.parent.mkdir(parents=True, exist_ok=True)
    INTRADAY_FILE.write_text(payload, encoding="utf-8")
    if INTRADAY_GCS_BUCKET:
        from google.cloud import storage

        blob = storage.Client().bucket(INTRADAY_GCS_BUCKET).blob(INTRADAY_GCS_BLOB)
        blob.upload_from_string(payload, content_type="application/json")


def add_intraday_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    store = load_intraday_prices()
    added = 0
    for sample in samples:
        ticker = str(sample.get("ticker") or "").strip().upper()
        try:
            timestamp = int(sample.get("timestamp"))
            close = float(sample.get("close"))
        except (TypeError, ValueError):
            continue
        if not ticker or timestamp <= 0 or close <= 0:
            continue
        rows = store.setdefault(ticker, [])
        bucket = timestamp // 300
        payload = {
            "timestamp": timestamp,
            "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(),
            "close": close,
        }
        existing = next((index for index, row in enumerate(rows) if int(row["timestamp"]) // 300 == bucket), None)
        if existing is None:
            rows.append(payload)
            added += 1
        else:
            rows[existing] = payload
        rows.sort(key=lambda row: row["timestamp"])
    save_intraday_prices(store)
    return {"sample_count": added, "ticker_count": len({row.get("ticker") for row in samples})}


MARKET_TZ = ZoneInfo("America/New_York")


def _in_regular_session(timestamp: int) -> bool:
    """9:30 to 4:00 New York time. VENU's Nasdaq price includes pre-market
    and after-hours trades; the other quotes do not, so those are left out to
    keep Today to the regular session for every stock."""
    local = datetime.fromtimestamp(int(timestamp), tz=MARKET_TZ)
    minutes = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def latest_session_prices(tickers: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Intraday samples from the most recent session in the store.

    A ticker whose samples are all from an earlier day gets an empty list (so
    callers fall back to daily closes) instead of an old day's samples being
    shown as today. Only samples from the regular session are used.
    """
    store = {
        ticker: [row for row in rows if _in_regular_session(row["timestamp"])]
        for ticker, rows in load_intraday_prices().items()
    }
    session_date = max(
        (str(row.get("date") or "") for rows in store.values() for row in rows),
        default="",
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for ticker in tickers:
        normalized = str(ticker).strip().upper()
        rows = store.get(normalized, [])
        result[normalized] = [row for row in rows if session_date and str(row.get("date") or "") == session_date]
    return result
