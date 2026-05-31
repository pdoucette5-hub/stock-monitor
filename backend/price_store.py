from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
PRICE_HISTORY_FILE = BASE_DIR / "cache" / "price_history.json"
PRICE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
PRICE_HISTORY_GCS_BUCKET = os.getenv("PRICE_HISTORY_GCS_BUCKET", "")
PRICE_HISTORY_GCS_BLOB = os.getenv(
    "PRICE_HISTORY_GCS_BLOB",
    "stock-monitor/price_history.json",
)

RANGE_DAYS = {
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 366,
    "3y": 365 * 3 + 1,
    "5y": 365 * 5 + 2,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(str(value)).date()


def _normalize_date(value: Any) -> str:
    if value is None:
        raise ValueError("date is required")
    raw = str(value).strip()
    if not raw:
        raise ValueError("date is required")
    return _parse_date(raw).isoformat()


def _safe_float(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("close is required")
    return float(value)


def _load_price_history_raw() -> Any:
    if PRICE_HISTORY_GCS_BUCKET:
        try:
            from google.cloud import storage

            client = storage.Client()
            blob = client.bucket(PRICE_HISTORY_GCS_BUCKET).blob(PRICE_HISTORY_GCS_BLOB)
            if blob.exists():
                return json.loads(blob.download_as_text(encoding="utf-8"))
        except Exception:
            pass

    if PRICE_HISTORY_FILE.exists():
        try:
            with open(PRICE_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    return {}


def _save_price_history_raw(data: dict[str, list[dict[str, Any]]]) -> None:
    with open(PRICE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    if not PRICE_HISTORY_GCS_BUCKET:
        return

    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(PRICE_HISTORY_GCS_BUCKET).blob(PRICE_HISTORY_GCS_BLOB)
    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json",
    )


def load_price_history_store() -> dict[str, list[dict[str, Any]]]:
    raw = _load_price_history_raw()

    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, list[dict[str, Any]]] = {}
    for ticker, rows in raw.items():
        normalized = normalize_ticker(ticker)
        if not normalized or not isinstance(rows, list):
            continue

        valid_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                row_date = _normalize_date(row.get("date"))
                close = float(row.get("close"))
            except Exception:
                continue

            valid_rows.append(
                {
                    "date": row_date,
                    "close": close,
                    "source": str(row.get("source") or "unknown"),
                    "fetched_at": str(row.get("fetched_at") or ""),
                }
            )

        valid_rows.sort(key=lambda item: item["date"])
        cleaned[normalized] = valid_rows

    return cleaned


def save_price_history_store(data: dict[str, list[dict[str, Any]]]) -> None:
    cleaned: dict[str, list[dict[str, Any]]] = {}

    for ticker, rows in data.items():
        normalized = normalize_ticker(ticker)
        if not normalized or not isinstance(rows, list):
            continue

        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                row_date = _normalize_date(row.get("date"))
                close = float(row.get("close"))
            except Exception:
                continue

            normalized_rows.append(
                {
                    "date": row_date,
                    "close": close,
                    "source": str(row.get("source") or "unknown"),
                    "fetched_at": str(row.get("fetched_at") or _utc_now_iso()),
                }
            )

        normalized_rows.sort(key=lambda item: item["date"])
        cleaned[normalized] = normalized_rows

    _save_price_history_raw(cleaned)


def upsert_price_rows(rows: list[dict[str, Any]], default_source: str = "googlefinance") -> dict[str, Any]:
    store = load_price_history_store()
    inserted = 0
    updated = 0
    touched_tickers: set[str] = set()

    for raw in rows:
        if not isinstance(raw, dict):
            continue

        ticker = normalize_ticker(raw.get("ticker", ""))
        if not ticker:
            continue

        row_date = _normalize_date(raw.get("date"))
        close = _safe_float(raw.get("close"))
        source = str(raw.get("source") or default_source)
        fetched_at = str(raw.get("fetched_at") or _utc_now_iso())

        ticker_rows = store.setdefault(ticker, [])
        existing_idx = next(
            (idx for idx, item in enumerate(ticker_rows) if item.get("date") == row_date),
            None,
        )

        payload = {
            "date": row_date,
            "close": close,
            "source": source,
            "fetched_at": fetched_at,
        }

        if existing_idx is None:
            ticker_rows.append(payload)
            inserted += 1
        else:
            ticker_rows[existing_idx] = payload
            updated += 1

        ticker_rows.sort(key=lambda item: item["date"])
        touched_tickers.add(ticker)

    save_price_history_store(store)

    return {
        "inserted": inserted,
        "updated": updated,
        "tickers": sorted(touched_tickers),
        "row_count": inserted + updated,
    }


def _filter_rows_for_range(rows: list[dict[str, Any]], range_key: str) -> list[dict[str, Any]]:
    if range_key not in RANGE_DAYS:
        raise ValueError(
            f"Unsupported range '{range_key}'. Use one of: {', '.join(sorted(RANGE_DAYS))}"
        )

    if not rows:
        return []

    latest_date = _parse_date(rows[-1]["date"])
    cutoff = latest_date - timedelta(days=RANGE_DAYS[range_key])

    return [row for row in rows if _parse_date(row["date"]) >= cutoff]


def get_price_points_for_ticker(ticker: str, range_key: str = "3y") -> list[dict[str, Any]]:
    store = load_price_history_store()
    rows = store.get(normalize_ticker(ticker), [])
    return _filter_rows_for_range(rows, range_key)


def get_latest_price_for_ticker(ticker: str) -> dict[str, Any] | None:
    store = load_price_history_store()
    rows = store.get(normalize_ticker(ticker), [])
    if not rows:
        return None

    latest = rows[-1]
    try:
        close = float(latest["close"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "date": latest.get("date"),
        "price": close,
        "source": latest.get("source") or "local-store",
        "fetched_at": latest.get("fetched_at") or "",
    }


def get_price_history_response(ticker: str, range_key: str = "3y") -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    points = get_price_points_for_ticker(normalized, range_key)

    return {
        "ticker": normalized,
        "range": range_key,
        "resolution": "D",
        "points": points,
        "cache_source": "local-store",
    }


def get_price_history_status(tickers: list[str] | None = None) -> dict[str, Any]:
    store = load_price_history_store()
    requested = [normalize_ticker(ticker) for ticker in tickers or [] if normalize_ticker(ticker)]
    selected = requested or sorted(store)

    ticker_status: dict[str, dict[str, Any]] = {}
    for ticker in selected:
        rows = store.get(ticker, [])
        ticker_status[ticker] = {
            "row_count": len(rows),
            "first_date": rows[0]["date"] if rows else None,
            "latest_date": rows[-1]["date"] if rows else None,
        }

    return {
        "ticker_count": len(ticker_status),
        "tickers": ticker_status,
    }


def get_price_comparison_response(tickers: list[str], range_key: str = "3y") -> dict[str, Any]:
    normalized_tickers: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        normalized = normalize_ticker(ticker)
        if normalized and normalized not in seen:
            normalized_tickers.append(normalized)
            seen.add(normalized)

    if not normalized_tickers:
        raise ValueError("At least one ticker is required")

    results: dict[str, list[dict[str, Any]]] = {}
    common_dates: set[str] | None = None

    for ticker in normalized_tickers:
        points = get_price_points_for_ticker(ticker, range_key)
        valid_points = [
            {
                "date": str(point["date"]),
                "close": float(point["close"]),
            }
            for point in points
            if point.get("date") and point.get("close") is not None
        ]

        if not valid_points:
            results[ticker] = []
            common_dates = set() if common_dates is None else common_dates.intersection(set())
            continue

        date_set = {point["date"] for point in valid_points}
        common_dates = date_set if common_dates is None else common_dates.intersection(date_set)
        results[ticker] = valid_points

    common_dates = common_dates or set()

    normalized_series: dict[str, list[dict[str, float | str]]] = {}
    for ticker, points in results.items():
        filtered = [point for point in points if point["date"] in common_dates]
        filtered.sort(key=lambda point: point["date"])

        if not filtered:
            normalized_series[ticker] = []
            continue

        base_close = filtered[0]["close"]
        if base_close == 0:
            normalized_series[ticker] = []
            continue

        normalized_series[ticker] = [
            {
                "date": point["date"],
                "close": round(point["close"], 8),
                "normalized": round((point["close"] / base_close) * 100.0, 8),
            }
            for point in filtered
        ]

    return {
        "range": range_key,
        "tickers": normalized_tickers,
        "series": normalized_series,
    }
