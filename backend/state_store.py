"""Loading and saving app state, plus the short-lived API response cache.

State lives in local JSON files under cache/, or in a single Cloud Storage
bundle when STATE_GCS_BUCKET is set. The module-level caches below are
reassigned with `global`, so other modules must go through the functions here
rather than importing the cache variables directly.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import (
    ACCOUNT_ALIASES_FILE,
    ASSUMPTION_SNAPSHOTS_FILE,
    EARNINGS_CALENDAR_FILE,
    GLOBAL_SETTINGS_FILE,
    HOLDINGS_OVERRIDES_FILE,
    MANAGEMENT_MODES_FILE,
    MANUAL_ALLOCATIONS_FILE,
    PORTFOLIO_EVENTS_FILE,
    REPORTED_FUNDAMENTALS_FILE,
    RESPONSE_CACHE_TTL_SECONDS,
    SCENARIO_STATE_FILE,
    STATE_GCS_BUCKET,
    STATE_GCS_BUNDLE_BLOB,
    STATE_GCS_PREFIX,
    TICKERS_OVERRIDES_FILE,
    TRANSACTIONS_FILE,
    UNASSIGNED_ACCOUNT,
    VALID_MANAGEMENT_MODES,
)
from backend.logic import merge_global_settings
from backend.utils import normalize_ticker


_response_cache: dict[str, tuple[float, Any]] = {}
_response_cache_lock = threading.Lock()
_state_bundle_cache: dict[str, Any] | None = None
_state_bundle_lock = threading.RLock()


def load_json_file(path: Path, default_data: Any) -> Any:
    if STATE_GCS_BUCKET:
        try:
            bundle = load_state_bundle()
            if bundle is not None:
                data = bundle.get(path.name, default_data)
                return data if isinstance(data, type(default_data)) else default_data

            # Compatibility path for buckets not migrated to a bundle yet.
            client = get_state_storage_client()
            blob = client.bucket(STATE_GCS_BUCKET).blob(
                f"{STATE_GCS_PREFIX.rstrip('/')}/{path.name}",
            )
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            return data if isinstance(data, type(default_data)) else default_data
        except Exception:
            pass

    if not path.exists():
        return default_data
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default_data)) else default_data
    except (json.JSONDecodeError, OSError):
        return default_data


def save_json_file(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    invalidate_response_cache()

    if not STATE_GCS_BUCKET:
        return

    save_state_bundle_value(path.name, data)


def get_state_storage_client() -> Any:
    from google.cloud import storage

    # Storage's authorized HTTP session serializes concurrent requests. A client
    # per worker lets the independent state downloads actually run in parallel.
    return storage.Client()


def load_state_bundle(force: bool = False) -> dict[str, Any] | None:
    global _state_bundle_cache
    with _state_bundle_lock:
        if _state_bundle_cache is not None and not force:
            return _state_bundle_cache
        try:
            client = get_state_storage_client()
            blob = client.bucket(STATE_GCS_BUCKET).blob(STATE_GCS_BUNDLE_BLOB)
            payload = json.loads(blob.download_as_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            _state_bundle_cache = payload
            return _state_bundle_cache
        except Exception:
            return None


def save_state_bundle_value(name: str, data: Any) -> None:
    global _state_bundle_cache
    from google.api_core.exceptions import NotFound, PreconditionFailed

    with _state_bundle_lock:
        client = get_state_storage_client()
        blob = client.bucket(STATE_GCS_BUCKET).blob(STATE_GCS_BUNDLE_BLOB)
        for attempt in range(3):
            try:
                try:
                    payload = json.loads(blob.download_as_text(encoding="utf-8"))
                    generation = blob.generation
                except NotFound:
                    payload = {}
                    generation = 0
                if not isinstance(payload, dict):
                    payload = {}
                payload[name] = data
                blob.upload_from_string(
                    json.dumps(payload, indent=2),
                    content_type="application/json",
                    if_generation_match=generation,
                )
                _state_bundle_cache = payload
                return
            except PreconditionFailed:
                if attempt == 2:
                    raise
                blob.reload()


def invalidate_response_cache() -> None:
    with _response_cache_lock:
        _response_cache.clear()


def get_cached_response(key: str) -> Any | None:
    with _response_cache_lock:
        cached = _response_cache.get(key)
        if not cached:
            return None
        cached_at, payload = cached
        if time.monotonic() - cached_at > RESPONSE_CACHE_TTL_SECONDS:
            _response_cache.pop(key, None)
            return None
        return payload


def set_cached_response(key: str, payload: Any) -> Any:
    with _response_cache_lock:
        _response_cache[key] = (time.monotonic(), payload)
    return payload


def load_scenario_inputs() -> dict[str, Any]:
    return load_json_file(SCENARIO_STATE_FILE, {})


def save_scenario_inputs(data: dict[str, Any]) -> None:
    save_json_file(SCENARIO_STATE_FILE, data)


def load_settings_dict() -> dict[str, Any]:
    saved = load_json_file(GLOBAL_SETTINGS_FILE, {})
    return merge_global_settings(saved)


def save_settings_dict(data: dict[str, Any]) -> None:
    save_json_file(GLOBAL_SETTINGS_FILE, data)


def load_holdings_overrides() -> dict[str, float]:
    raw = load_json_file(HOLDINGS_OVERRIDES_FILE, {})
    cleaned: dict[str, float] = {}
    if not isinstance(raw, dict):
        return cleaned

    for ticker, shares in raw.items():
        try:
            cleaned[str(ticker).strip().upper()] = float(shares)
        except (TypeError, ValueError):
            continue
    return cleaned


def save_holdings_overrides(data: dict[str, float]) -> None:
    cleaned = {str(k).strip().upper(): float(v) for k, v in data.items()}
    save_json_file(HOLDINGS_OVERRIDES_FILE, cleaned)


def load_tickers_overrides() -> dict[str, Any]:
    raw = load_json_file(TICKERS_OVERRIDES_FILE, {"tickers": {}})
    if not isinstance(raw, dict):
        return {"tickers": {}}

    tickers = raw.get("tickers", {})
    if not isinstance(tickers, dict):
        tickers = {}

    cleaned: dict[str, Any] = {}
    for ticker, payload in tickers.items():
        normalized = str(ticker).strip().upper()
        if not normalized or not isinstance(payload, dict):
            continue

        list_value = payload.get("list")
        if list_value not in {"portfolio", "watchlist"}:
            list_value = None

        shares = payload.get("shares")
        try:
            shares = None if shares is None else float(shares)
        except (TypeError, ValueError):
            shares = None

        cleaned[normalized] = {
            "list": list_value,
            "shares": shares,
            "archived": bool(payload.get("archived", False)),
            "removed": bool(payload.get("removed", False)),
        }

    return {"tickers": cleaned}


def save_tickers_overrides(data: dict[str, Any]) -> None:
    tickers = data.get("tickers", {}) if isinstance(data, dict) else {}
    cleaned: dict[str, Any] = {}

    if isinstance(tickers, dict):
        for ticker, payload in tickers.items():
            normalized = str(ticker).strip().upper()
            if not normalized or not isinstance(payload, dict):
                continue

            list_value = payload.get("list")
            if list_value not in {"portfolio", "watchlist"}:
                list_value = None

            shares = payload.get("shares")
            try:
                shares = None if shares is None else float(shares)
            except (TypeError, ValueError):
                shares = None

            cleaned[normalized] = {
                "list": list_value,
                "shares": shares,
                "archived": bool(payload.get("archived", False)),
                "removed": bool(payload.get("removed", False)),
            }

    save_json_file(TICKERS_OVERRIDES_FILE, {"tickers": cleaned})


def load_transactions() -> dict[str, list[dict[str, Any]]]:
    raw = load_json_file(TRANSACTIONS_FILE, {})
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in raw.items():
        normalized = normalize_ticker(ticker)
        if not normalized or not isinstance(entries, list):
            continue
        cleaned[normalized] = [entry for entry in entries if isinstance(entry, dict)]
    return cleaned


def save_transactions(data: dict[str, list[dict[str, Any]]]) -> None:
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in data.items():
        normalized = normalize_ticker(ticker)
        if not normalized or not isinstance(entries, list):
            continue
        cleaned[normalized] = [entry for entry in entries if isinstance(entry, dict)]
    save_json_file(TRANSACTIONS_FILE, cleaned)


def load_account_aliases() -> dict[str, str]:
    raw = load_json_file(ACCOUNT_ALIASES_FILE, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


def save_account_aliases(data: dict[str, str]) -> None:
    cleaned = {
        str(key).strip(): str(value).strip()
        for key, value in data.items()
        if str(key).strip() and str(value).strip()
    }
    save_json_file(ACCOUNT_ALIASES_FILE, cleaned)


def load_management_modes() -> dict[str, Any]:
    raw = load_json_file(
        MANAGEMENT_MODES_FILE,
        {"account_defaults": {}, "position_overrides": {}},
    )
    if not isinstance(raw, dict):
        raw = {}

    account_defaults = raw.get("account_defaults", {})
    position_overrides = raw.get("position_overrides", {})

    return {
        "account_defaults": {
            str(account).strip(): str(mode).strip().lower()
            for account, mode in account_defaults.items()
            if str(account).strip() and str(mode).strip().lower() in VALID_MANAGEMENT_MODES
        }
        if isinstance(account_defaults, dict)
        else {},
        "position_overrides": {
            str(key).strip(): str(mode).strip().lower()
            for key, mode in position_overrides.items()
            if str(key).strip() and str(mode).strip().lower() in VALID_MANAGEMENT_MODES
        }
        if isinstance(position_overrides, dict)
        else {},
    }


def save_management_modes(data: dict[str, Any]) -> None:
    save_json_file(MANAGEMENT_MODES_FILE, data)


def load_manual_allocations() -> list[dict[str, Any]]:
    raw = load_json_file(MANUAL_ALLOCATIONS_FILE, [])
    if not isinstance(raw, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ticker = normalize_ticker(row.get("ticker", ""))
        account = str(row.get("account") or "").strip()
        try:
            shares = float(row.get("shares") or 0.0)
        except (TypeError, ValueError):
            continue
        if ticker and account and account != UNASSIGNED_ACCOUNT and shares > 0:
            cleaned.append({"ticker": ticker, "account": account, "shares": shares})
    return cleaned


def save_manual_allocations(rows: list[dict[str, Any]]) -> None:
    save_json_file(MANUAL_ALLOCATIONS_FILE, rows)


def load_portfolio_events() -> list[dict[str, Any]]:
    raw = load_json_file(PORTFOLIO_EVENTS_FILE, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_portfolio_events(events: list[dict[str, Any]]) -> None:
    cleaned = [item for item in events if isinstance(item, dict)][-5000:]
    save_json_file(PORTFOLIO_EVENTS_FILE, cleaned)


def append_portfolio_event(
    event_type: str,
    ticker: str,
    payload: dict[str, Any] | None = None,
) -> None:
    events = load_portfolio_events()
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": str(event_type).strip(),
        "ticker": normalize_ticker(ticker),
        "payload": payload or {},
    }
    events.append(entry)
    save_portfolio_events(events)


def load_assumption_snapshots() -> list[dict[str, Any]]:
    raw = load_json_file(ASSUMPTION_SNAPSHOTS_FILE, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_assumption_snapshots(snapshots: list[dict[str, Any]]) -> None:
    cleaned = [item for item in snapshots if isinstance(item, dict)][-10000:]
    save_json_file(ASSUMPTION_SNAPSHOTS_FILE, cleaned)


def load_reported_fundamentals() -> dict[str, Any]:
    return load_json_file(REPORTED_FUNDAMENTALS_FILE, {})


def save_reported_fundamentals(data: dict[str, Any]) -> None:
    cleaned = {
        normalize_ticker(ticker): payload
        for ticker, payload in data.items()
        if normalize_ticker(ticker) and isinstance(payload, dict)
    }
    save_json_file(REPORTED_FUNDAMENTALS_FILE, dict(sorted(cleaned.items())))


def load_earnings_calendar() -> dict[str, Any]:
    stored = load_json_file(EARNINGS_CALENDAR_FILE, {})
    today = datetime.now(timezone.utc).date()
    current = {}
    for ticker, payload in stored.items():
        if not isinstance(payload, dict):
            continue
        try:
            earnings_date = datetime.fromisoformat(str(payload.get("next_earnings_date"))).date()
        except (TypeError, ValueError):
            continue
        if earnings_date >= today:
            current[ticker] = payload
    return current


def save_earnings_calendar(data: dict[str, Any]) -> None:
    cleaned = {
        normalize_ticker(ticker): payload
        for ticker, payload in data.items()
        if normalize_ticker(ticker) and isinstance(payload, dict)
    }
    save_json_file(EARNINGS_CALENDAR_FILE, dict(sorted(cleaned.items())))
