import os
import time
import json
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE = "https://finnhub.io/api/v1"


DEBUG_FIELDS = {
    "revenue_quarters_used": None,
    "prior_revenue_quarters_used": None,
    "ni_quarters_used": None,
    "prior_ni_quarters_used": None,
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
    """
    Keep enough raw detail to debug line-item selection without dumping the entire
    Finnhub response into the cache.
    """
    return {
        "concept": item.get("concept"),
        "label": item.get("label"),
        "value": safe_float(item.get("value")),
        "unit": item.get("unit"),
    }


def _extract_quarter_value(report_items, concepts):
    """
    Pull the first matching concept/label from a quarterly income statement.

    Returns:
    {
        "value": selected value,
        "concept": selected concept,
        "label": selected label,
        "unit": selected unit,
        "candidates": all matching line items
    }
    """
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
    """
    Returns quarterly revenue / net income rows sorted newest -> oldest.

    The returned rows include the selected concepts/labels and matching
    candidates so the dashboard can show exactly what Finnhub data was used.
    """
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

                # Raw-ish compact line items for deeper debugging.
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
    """
    Uses:
    - current TTM = latest 4 valid quarters
    - prior TTM   = next 4 valid quarters

    Revenue and net income are handled separately so a missing quarter in one
    does not corrupt the other.
    """
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

        # Debug details displayed by the dashboard.
        "revenue_quarters_used": current_revenue_quarters,
        "prior_revenue_quarters_used": prior_revenue_quarters,
        "ni_quarters_used": current_ni_quarters,
        "prior_ni_quarters_used": prior_ni_quarters,
    }


def _get_market_data_for_ticker(symbol: str):
    quote = _finnhub_get("quote", {"symbol": symbol})
    profile = _finnhub_get("stock/profile2", {"symbol": symbol})
    financials = _get_ttm_financials(symbol)

    price = safe_float(quote.get("c"))

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

    status = "OK" if not missing else f"PARTIAL: missing {', '.join(missing)}"

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

    rows = []

    for ticker in tickers:
        ticker = str(ticker).strip().upper()

        if not force_refresh:
            cached = _read_cache(ticker, max_age_hours=max_age_hours)
            if cached is not None:
                cached["cache_source"] = "cache"
                rows.append(cached)
                continue

        try:
            row = _get_market_data_for_ticker(ticker)
            _write_cache(ticker, row)
            rows.append(row)
        except Exception as e:
            fallback = _read_cache(ticker, max_age_hours=99999)
            if fallback is not None:
                fallback["status"] = f"CACHE_FALLBACK: {e}"
                fallback["cache_source"] = "cache-fallback"
                for key, default in DEBUG_FIELDS.items():
                    fallback.setdefault(key, default)
                rows.append(fallback)
            else:
                rows.append(_empty_row(ticker, f"ERROR: {e}", cache_source="none"))

    return pd.DataFrame(rows)
