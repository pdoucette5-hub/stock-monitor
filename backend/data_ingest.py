import os
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

PRICE_HISTORY_CACHE_DIR = CACHE_DIR / "price_history"
PRICE_HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE = "https://finnhub.io/api/v1"


DEBUG_FIELDS = {
    "revenue_quarters_used": None,
    "prior_revenue_quarters_used": None,
    "ni_quarters_used": None,
    "prior_ni_quarters_used": None,
}


PRICE_RANGE_CONFIG = {
    "1m": {"days": 31, "resolution": "D"},
    "3m": {"days": 92, "resolution": "D"},
    "6m": {"days": 183, "resolution": "D"},
    "1y": {"days": 366, "resolution": "W"},
    "3y": {"days": 365 * 3 + 1, "resolution": "W"},
    "5y": {"days": 365 * 5 + 2, "resolution": "M"},
}


def safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}_market_data.json"


def _history_cache_path(ticker: str, range_key: str) -> Path:
    return PRICE_HISTORY_CACHE_DIR / f"{ticker.upper()}_{range_key.lower()}_history.json"


def _cache_fetched_at(ticker: str) -> float | None:
    """Unix time the cached market data for `ticker` was fetched, if known.

    Rows served from the cache carry this as `cache_fetched_at`, so callers can
    prefer a newer price (such as the imported sheet prices) over a cached quote
    that may be months old.
    """
    try:
        with open(_cache_path(ticker), "r") as f:
            fetched_at = json.load(f).get("fetched_at")
        return float(fetched_at) if fetched_at is not None else None
    except Exception:
        return None


def _read_cache(ticker: str, max_age_hours: int):
    path = _cache_path(ticker)
    if not path.exists():
        return None

    try:
        with open(path, "r") as f:
            payload = json.load(f)
    except Exception:
        return None

    fetched_at = payload.get("fetched_at")
    if fetched_at is None:
        return None

    age_seconds = time.time() - fetched_at
    if age_seconds > max_age_hours * 3600:
        return None

    data = payload.get("data")
    if isinstance(data, dict):
        for key, default in DEBUG_FIELDS.items():
            data.setdefault(key, default)
    return data


def _write_cache(ticker: str, data: dict):
    path = _cache_path(ticker)
    with open(path, "w") as f:
        json.dump(
            {
                "fetched_at": time.time(),
                "data": data,
            },
            f,
            indent=2,
        )


def _read_history_cache(ticker: str, range_key: str, max_age_hours: int):
    path = _history_cache_path(ticker, range_key)
    if not path.exists():
        return None

    try:
        with open(path, "r") as f:
            payload = json.load(f)
    except Exception:
        return None

    fetched_at = payload.get("fetched_at")
    if fetched_at is None:
        return None

    age_seconds = time.time() - fetched_at
    if age_seconds > max_age_hours * 3600:
        return None

    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _write_history_cache(ticker: str, range_key: str, data: dict):
    path = _history_cache_path(ticker, range_key)
    with open(path, "w") as f:
        json.dump(
            {
                "fetched_at": time.time(),
                "data": data,
            },
            f,
            indent=2,
        )


def _finnhub_get(endpoint: str, params: dict):
    if not FINNHUB_API_KEY:
        raise ValueError("Missing FINNHUB_API_KEY environment variable")

    url = f"{FINNHUB_BASE}/{endpoint}"
    merged = dict(params)
    merged["token"] = FINNHUB_API_KEY

    r = requests.get(url, params=merged, timeout=20)
    r.raise_for_status()
    return r.json()


def _simplify_statement_item(item: dict) -> dict:
    return {
        "concept": item.get("concept"),
        "label": item.get("label"),
        "value": safe_float(item.get("value")),
        "unit": item.get("unit"),
    }


def _extract_quarter_value(report_items, concepts):
    concept_set = {c.lower() for c in concepts}
    candidates = []

    for item in report_items or []:
        concept = str(item.get("concept", "")).strip()
        label = str(item.get("label", "")).strip()

        if concept.lower() in concept_set or label.lower() in concept_set:
            candidates.append(_simplify_statement_item(item))

    selected = candidates[0] if candidates else {}

    return {
        "value": safe_float(selected.get("value")),
        "concept": selected.get("concept"),
        "label": selected.get("label"),
        "unit": selected.get("unit"),
        "candidates": candidates,
    }


def _get_quarterly_financials(symbol: str):
    resp = _finnhub_get("stock/financials-reported", {"symbol": symbol, "freq": "quarterly"})
    filings = resp.get("data", []) if isinstance(resp, dict) else []

    quarter_rows = []

    for filing in filings:
        report = filing.get("report", {})
        ic = report.get("ic", [])

        revenue_match = _extract_quarter_value(
            ic,
            [
                "revenue",
                "revenues",
                "salesrevenuenet",
                "totalrevenue",
                "revenuefromcontractwithcustomerexcludingassessedtax",
            ],
        )

        net_income_match = _extract_quarter_value(
            ic,
            [
                "netincomeloss",
                "netincome",
                "profitloss",
            ],
        )

        quarter_rows.append(
            {
                "end_date": filing.get("endDate"),
                "accession_number": filing.get("accessNumber"),
                "form": filing.get("form"),
                "filed_date": filing.get("filedDate"),
                "revenue": revenue_match["value"],
                "revenue_concept": revenue_match["concept"],
                "revenue_label": revenue_match["label"],
                "revenue_unit": revenue_match["unit"],
                "revenue_candidates": revenue_match["candidates"],
                "net_income": net_income_match["value"],
                "net_income_concept": net_income_match["concept"],
                "net_income_label": net_income_match["label"],
                "net_income_unit": net_income_match["unit"],
                "net_income_candidates": net_income_match["candidates"],
                "income_statement_items": [_simplify_statement_item(item) for item in ic or []],
            }
        )

    quarter_rows = [q for q in quarter_rows if q.get("end_date")]
    quarter_rows = sorted(quarter_rows, key=lambda x: x["end_date"], reverse=True)

    return quarter_rows


def _growth_pct(current_value, prior_value):
    current_value = safe_float(current_value)
    prior_value = safe_float(prior_value)

    if current_value is None or prior_value is None:
        return None
    if prior_value == 0:
        return None

    return ((current_value / prior_value) - 1) * 100.0


def _get_ttm_financials(symbol: str):
    quarters = _get_quarterly_financials(symbol)

    revenue_quarters = [q for q in quarters if safe_float(q.get("revenue")) is not None]
    ni_quarters = [q for q in quarters if safe_float(q.get("net_income")) is not None]

    current_revenue_quarters = revenue_quarters[:4]
    prior_revenue_quarters = revenue_quarters[4:8]

    current_ni_quarters = ni_quarters[:4]
    prior_ni_quarters = ni_quarters[4:8]

    current_ttm_revenue = (
        sum(safe_float(q["revenue"]) for q in current_revenue_quarters)
        if len(current_revenue_quarters) == 4
        else None
    )
    prior_ttm_revenue = (
        sum(safe_float(q["revenue"]) for q in prior_revenue_quarters)
        if len(prior_revenue_quarters) == 4
        else None
    )

    current_ttm_net_income = (
        sum(safe_float(q["net_income"]) for q in current_ni_quarters)
        if len(current_ni_quarters) == 4
        else None
    )
    prior_ttm_net_income = (
        sum(safe_float(q["net_income"]) for q in prior_ni_quarters)
        if len(prior_ni_quarters) == 4
        else None
    )

    return {
        "ttm_revenue": current_ttm_revenue,
        "net_income_ttm": current_ttm_net_income,
        "prior_ttm_revenue": prior_ttm_revenue,
        "prior_net_income_ttm": prior_ttm_net_income,
        "revenue_quarter_dates_used": [q["end_date"] for q in current_revenue_quarters],
        "prior_revenue_quarter_dates_used": [q["end_date"] for q in prior_revenue_quarters],
        "ni_quarter_dates_used": [q["end_date"] for q in current_ni_quarters],
        "prior_ni_quarter_dates_used": [q["end_date"] for q in prior_ni_quarters],
        "revenue_quarters_used": current_revenue_quarters,
        "prior_revenue_quarters_used": prior_revenue_quarters,
        "ni_quarters_used": current_ni_quarters,
        "prior_ni_quarters_used": prior_ni_quarters,
    }


def _get_market_data_for_ticker(symbol: str):
    quote = _finnhub_get("quote", {"symbol": symbol})
    price = safe_float(quote.get("c"))

    secondary_errors = []
    try:
        profile = _finnhub_get("stock/profile2", {"symbol": symbol})
    except Exception as exc:
        profile = {}
        secondary_errors.append(f"profile unavailable ({exc})")

    try:
        financials = _get_ttm_financials(symbol)
    except Exception as exc:
        financials = {}
        secondary_errors.append(f"financials unavailable ({exc})")

    share_outstanding_millions = safe_float(profile.get("shareOutstanding"))
    shares_outstanding = (
        share_outstanding_millions * 1_000_000
        if share_outstanding_millions is not None
        else None
    )

    ttm_revenue = financials.get("ttm_revenue")
    net_income_ttm = financials.get("net_income_ttm")
    prior_ttm_revenue = financials.get("prior_ttm_revenue")
    prior_net_income_ttm = financials.get("prior_net_income_ttm")

    revenue_growth_pct = _growth_pct(ttm_revenue, prior_ttm_revenue)
    net_income_growth_pct = _growth_pct(net_income_ttm, prior_net_income_ttm)

    missing = []
    for name, value in {
        "price": price,
        "ttm_revenue": ttm_revenue,
        "net_income_ttm": net_income_ttm,
        "shares_outstanding": shares_outstanding,
    }.items():
        if value is None:
            missing.append(name)

    status_parts = []
    if missing:
        status_parts.append(f"missing {', '.join(missing)}")
    status_parts.extend(secondary_errors)
    status = "OK" if not status_parts else f"PARTIAL: {'; '.join(status_parts)}"

    return {
        "ticker": symbol,
        "price": price,
        "ttm_revenue": ttm_revenue,
        "net_income_ttm": net_income_ttm,
        "prior_ttm_revenue": prior_ttm_revenue,
        "prior_net_income_ttm": prior_net_income_ttm,
        "revenue_growth_pct": revenue_growth_pct,
        "net_income_growth_pct": net_income_growth_pct,
        "shares_outstanding": shares_outstanding,
        "revenue_quarter_dates_used": financials.get("revenue_quarter_dates_used"),
        "prior_revenue_quarter_dates_used": financials.get("prior_revenue_quarter_dates_used"),
        "ni_quarter_dates_used": financials.get("ni_quarter_dates_used"),
        "prior_ni_quarter_dates_used": financials.get("prior_ni_quarter_dates_used"),
        "revenue_quarters_used": financials.get("revenue_quarters_used"),
        "prior_revenue_quarters_used": financials.get("prior_revenue_quarters_used"),
        "ni_quarters_used": financials.get("ni_quarters_used"),
        "prior_ni_quarters_used": financials.get("prior_ni_quarters_used"),
        "status": status,
        "cache_source": "live",
    }


def _empty_row(ticker: str, status: str, cache_source: str = "none") -> dict:
    row = {
        "ticker": str(ticker).strip().upper(),
        "price": None,
        "ttm_revenue": None,
        "net_income_ttm": None,
        "prior_ttm_revenue": None,
        "prior_net_income_ttm": None,
        "revenue_growth_pct": None,
        "net_income_growth_pct": None,
        "shares_outstanding": None,
        "revenue_quarter_dates_used": None,
        "prior_revenue_quarter_dates_used": None,
        "ni_quarter_dates_used": None,
        "prior_ni_quarter_dates_used": None,
        "status": status,
        "cache_source": cache_source,
    }
    row.update(DEBUG_FIELDS)
    return row


# Tickers whose live fetch failed and that have no cached data. Normal page
# loads skip them for a while instead of retrying (and waiting on Finnhub) on
# every load; "Update prices" (force_refresh) always retries. Their price falls
# back to the imported sheet prices.
FAILED_FETCH_RETRY_SECONDS = 6 * 3600
# Live fetches run together on a normal page load (see get_live_market_data).
LIVE_FETCH_WORKERS = 6
_failed_fetches: dict[str, tuple[float, str]] = {}


def get_live_market_data(tickers, force_refresh=False, max_age_hours=12):
    if not FINNHUB_API_KEY:
        return pd.DataFrame(
            [
                _empty_row(
                    t,
                    "ERROR: Missing FINNHUB_API_KEY environment variable",
                    cache_source="none",
                )
                for t in tickers
            ]
        )

    def fetch_live(ticker):
        try:
            row = _get_market_data_for_ticker(ticker)
            try:
                _write_cache(ticker, row)
            except OSError:
                # A valid live response should still be returned when the local
                # cache is temporarily read-only or unavailable.
                row = dict(row)
                row["status"] = f"{row['status']}; cache unavailable"
            _failed_fetches.pop(ticker, None)
            return row
        except Exception as e:
            fallback = _read_cache(ticker, max_age_hours=99999)
            if fallback is not None:
                fallback["status"] = f"CACHE_FALLBACK: {e}"
                fallback["cache_source"] = "cache-fallback"
                fallback["cache_fetched_at"] = _cache_fetched_at(ticker)
                for key, default in DEBUG_FIELDS.items():
                    fallback.setdefault(key, default)
                return fallback
            status = f"ERROR: {e}"
            _failed_fetches[ticker] = (time.time(), status)
            return _empty_row(ticker, status, cache_source="none")

    rows_by_index = {}
    to_fetch = []

    for index, ticker in enumerate(tickers):
        ticker = str(ticker).strip().upper()

        if not force_refresh:
            cached = _read_cache(ticker, max_age_hours=max_age_hours)
            if cached is not None:
                cached["cache_source"] = "cache"
                cached["cache_fetched_at"] = _cache_fetched_at(ticker)
                rows_by_index[index] = cached
                continue

            failed = _failed_fetches.get(ticker)
            if failed and time.time() - failed[0] < FAILED_FETCH_RETRY_SECONDS:
                rows_by_index[index] = _empty_row(ticker, failed[1], cache_source="none")
                continue

        to_fetch.append((index, ticker))

    if force_refresh or len(to_fetch) <= 1:
        # "Update prices" fetches every ticker; keep that sequential so it
        # stays within Finnhub's per-minute rate limit.
        for index, ticker in to_fetch:
            rows_by_index[index] = fetch_live(ticker)
    else:
        # A normal page load only fetches the few tickers with no cached data;
        # fetch them together so a fresh server does not wait on each in turn.
        with ThreadPoolExecutor(max_workers=min(LIVE_FETCH_WORKERS, len(to_fetch))) as pool:
            futures = {pool.submit(fetch_live, ticker): index for index, ticker in to_fetch}
            for future in as_completed(futures):
                rows_by_index[futures[future]] = future.result()

    rows = [rows_by_index[index] for index in sorted(rows_by_index)]
    return pd.DataFrame(rows)


def get_price_history(ticker: str, range_key: str = "1y", force_refresh: bool = False, max_age_hours: int = 24):
    ticker = str(ticker).strip().upper()
    range_key = str(range_key).strip().lower()

    if range_key not in PRICE_RANGE_CONFIG:
        raise ValueError(f"Unsupported range '{range_key}'. Use one of: {', '.join(sorted(PRICE_RANGE_CONFIG))}")

    if not FINNHUB_API_KEY:
        raise ValueError("Missing FINNHUB_API_KEY environment variable")

    if not force_refresh:
        cached = _read_history_cache(ticker, range_key, max_age_hours=max_age_hours)
        if cached is not None:
            cached["cache_source"] = "cache"
            return cached

    cfg = PRICE_RANGE_CONFIG[range_key]
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=cfg["days"])

    response = _finnhub_get(
        "stock/candle",
        {
            "symbol": ticker,
            "resolution": cfg["resolution"],
            "from": int(start.timestamp()),
            "to": int(now.timestamp()),
        },
    )

    status = response.get("s")
    if status != "ok":
        raise ValueError(f"Failed to fetch price history for {ticker}: {response}")

    closes = response.get("c", []) or []
    timestamps = response.get("t", []) or []
    opens = response.get("o", []) or []
    highs = response.get("h", []) or []
    lows = response.get("l", []) or []
    volumes = response.get("v", []) or []

    points = []
    for idx, ts in enumerate(timestamps):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        points.append(
            {
                "date": dt.date().isoformat(),
                "timestamp": int(ts),
                "open": safe_float(opens[idx]) if idx < len(opens) else None,
                "high": safe_float(highs[idx]) if idx < len(highs) else None,
                "low": safe_float(lows[idx]) if idx < len(lows) else None,
                "close": safe_float(closes[idx]) if idx < len(closes) else None,
                "volume": safe_float(volumes[idx]) if idx < len(volumes) else None,
            }
        )

    payload = {
        "ticker": ticker,
        "range": range_key,
        "resolution": cfg["resolution"],
        "points": points,
        "cache_source": "live",
    }

    _write_history_cache(ticker, range_key, payload)
    return payload
