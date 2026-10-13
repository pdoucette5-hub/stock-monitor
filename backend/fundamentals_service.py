"""Company fundamentals from outside sources.

SEC reported figures, Nasdaq earnings dates and analyst estimates, and
StockAnalysis forecasts. `_sec_ticker_map_cache` is reassigned with `global`;
use load_sec_ticker_map() rather than importing it.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from backend.logic import safe_float
from backend.state_store import (
    load_earnings_calendar,
    load_reported_fundamentals,
    load_scenario_inputs,
    save_earnings_calendar,
    save_reported_fundamentals,
    save_scenario_inputs,
)
from backend.utils import normalize_ticker


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_ANALYST_EARNINGS_URL = "https://api.nasdaq.com/api/analyst/{ticker}/earnings-forecast"
STOCK_ANALYSIS_FORECAST_URL = "https://stockanalysis.com/stocks/{ticker}/forecast/"
SEC_HEADERS = {
    "User-Agent": "stock-monitor/1.0 pdoucette5@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
REVENUE_FACT_CANDIDATES = (
    "RevenuesNetOfInterestExpense",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "SalesRevenueServicesNet",
)
NET_INCOME_FACT_CANDIDATES = (
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)
SHARES_FACT_CANDIDATES = (
    "EntityCommonStockSharesOutstanding",
    "CommonStocksIncludingAdditionalPaidInCapitalMember",
)
_sec_ticker_map_cache: dict[str, str] | None = None


def _sec_get_json(url: str) -> Any:
    response = requests.get(url, headers=SEC_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def load_sec_ticker_map() -> dict[str, str]:
    global _sec_ticker_map_cache
    if _sec_ticker_map_cache is not None:
        return _sec_ticker_map_cache

    payload = _sec_get_json(SEC_TICKER_MAP_URL)
    mapping: dict[str, str] = {}
    if isinstance(payload, dict):
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            ticker = normalize_ticker(item.get("ticker", ""))
            cik = item.get("cik_str")
            if ticker and cik is not None:
                mapping[ticker] = str(cik).zfill(10)
    _sec_ticker_map_cache = mapping
    return mapping


def _parse_compact_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return safe_float(value)
    text = str(value).strip().strip('"').strip("'")
    if not text or text == "[PRO]" or text in {"null", "void 0", "undefined"}:
        return None
    multiplier = 1.0
    suffix = text[-1:].upper()
    if suffix in {"K", "M", "B", "T"}:
        multiplier = {
            "K": 1_000.0,
            "M": 1_000_000.0,
            "B": 1_000_000_000.0,
            "T": 1_000_000_000_000.0,
        }[suffix]
        text = text[:-1]
    text = text.replace("$", "").replace(",", "").replace("%", "")
    parsed = safe_float(text)
    return parsed * multiplier if parsed is not None else None


def _first_regex_float(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return _parse_compact_number(match.group(1))


def _stockanalysis_metric(page: str, key: str) -> dict[str, float | None]:
    pattern = (
        rf"{re.escape(key)}:\{{"
        r"last:([^,}]+),"
        r"this:([^,}]+),"
        r"growth:([^,}]+)"
    )
    match = re.search(pattern, page)
    if not match:
        return {"last": None, "this": None, "growth": None}
    return {
        "last": _parse_compact_number(match.group(1)),
        "this": _parse_compact_number(match.group(2)),
        "growth": _parse_compact_number(match.group(3)),
    }


def _timestamp_ms_to_iso(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _nasdaq_yearly_eps_rows(ticker: str) -> list[dict[str, Any]]:
    response = requests.get(
        NASDAQ_ANALYST_EARNINGS_URL.format(ticker=ticker),
        headers=NASDAQ_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    forecast = data.get("yearlyForecast") if isinstance(data, dict) else None
    rows = forecast.get("rows") if isinstance(forecast, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def fetch_online_projection(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise ValueError("Ticker is required")

    stockanalysis_url = STOCK_ANALYSIS_FORECAST_URL.format(ticker=normalized.lower())
    response = requests.get(
        stockanalysis_url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
        timeout=20,
    )
    response.raise_for_status()
    page = response.text

    revenue_this = _stockanalysis_metric(page, "revenueThis")
    revenue_next = _stockanalysis_metric(page, "revenueNext")
    eps_this = _stockanalysis_metric(page, "epsThis")
    eps_next = _stockanalysis_metric(page, "epsNext")
    analyst_count = _first_regex_float(page, r"analysts:\[null,null,null,null,null,([^,\]]+)")
    last_updated = _timestamp_ms_to_iso(_first_regex_float(page, r"lastUpdated:(\d+)"))
    estimates_source = "stockanalysis"
    source_match = re.search(r"estimatesSource:\"([^\"]+)\"", page)
    if source_match:
        estimates_source = f"stockanalysis-{source_match.group(1)}"

    earnings_growth = [eps_this.get("growth"), eps_next.get("growth"), None]
    eps_estimates = [eps_this.get("this"), eps_next.get("this"), None]
    eps_estimate_counts = [analyst_count, None, None]

    try:
        nasdaq_rows = _nasdaq_yearly_eps_rows(normalized)
    except Exception:
        nasdaq_rows = []
    previous_eps = eps_this.get("last")
    for idx, row in enumerate(nasdaq_rows[:3]):
        consensus_eps = _parse_compact_number(row.get("consensusEPSForecast"))
        if consensus_eps is None:
            continue
        if idx < len(eps_estimates):
            eps_estimates[idx] = eps_estimates[idx] if eps_estimates[idx] is not None else consensus_eps
            eps_estimate_counts[idx] = _parse_compact_number(row.get("noOfEstimates"))
        if idx < len(earnings_growth) and earnings_growth[idx] is None and previous_eps:
            earnings_growth[idx] = ((consensus_eps / previous_eps) - 1.0) * 100.0
        previous_eps = consensus_eps

    return {
        "source": estimates_source,
        "source_url": stockanalysis_url,
        "secondary_sources": [
            {
                "source": "nasdaq-analyst-earnings-forecast",
                "source_url": NASDAQ_ANALYST_EARNINGS_URL.format(ticker=normalized),
            },
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": last_updated,
        "revenue_growth": [revenue_this.get("growth"), revenue_next.get("growth"), None],
        "earnings_growth": earnings_growth,
        "revenue_estimates": [revenue_this.get("this"), revenue_next.get("this"), None],
        "eps_estimates": eps_estimates,
        "revenue_estimate_counts": [analyst_count, None, None],
        "eps_estimate_counts": eps_estimate_counts,
    }


def _duration_days(fact: dict[str, Any]) -> int | None:
    start = fact.get("start")
    end = fact.get("end")
    if not start or not end:
        return None
    try:
        start_date = datetime.fromisoformat(str(start)).date()
        end_date = datetime.fromisoformat(str(end)).date()
    except ValueError:
        return None
    return (end_date - start_date).days


def _fact_units(company_facts: dict[str, Any], concept: str, unit: str) -> list[dict[str, Any]]:
    facts = company_facts.get("facts")
    if not isinstance(facts, dict):
        return []
    matches: list[dict[str, Any]] = []
    for namespace, namespace_facts in facts.items():
        if not isinstance(namespace_facts, dict):
            continue
        concept_payload = namespace_facts.get(concept)
        if not isinstance(concept_payload, dict):
            continue
        units = concept_payload.get("units")
        if not isinstance(units, dict):
            continue
        rows = units.get(unit)
        if isinstance(rows, list):
            matches.extend({**row, "namespace": namespace} for row in rows if isinstance(row, dict))
    return matches


def _latest_quarterly_fact(
    company_facts: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for priority, concept in enumerate(reversed(concepts), start=1):
        for fact in _fact_units(company_facts, concept, unit):
            if safe_float(fact.get("val")) is None:
                continue
            form = str(fact.get("form") or "")
            if form not in {"10-Q", "10-K"}:
                continue
            duration = _duration_days(fact)
            if duration is None or duration > 130:
                continue
            candidates.append(
                {
                    **fact,
                    "concept": concept,
                    "concept_priority": priority,
                    "duration_days": duration,
                },
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            str(item.get("end") or ""),
            int(item.get("concept_priority") or 0),
            str(item.get("filed") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def _latest_instant_fact(
    company_facts: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for concept in concepts:
        for fact in _fact_units(company_facts, concept, unit):
            if safe_float(fact.get("val")) is None:
                continue
            form = str(fact.get("form") or "")
            if form not in {"10-Q", "10-K"}:
                continue
            candidates.append({**fact, "concept": concept})

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            str(item.get("end") or ""),
            str(item.get("filed") or ""),
            str(item.get("concept") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def fetch_sec_reported_fundamentals(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise ValueError("Ticker is required")

    cik = load_sec_ticker_map().get(normalized)
    if not cik:
        raise ValueError(f"No SEC CIK found for {normalized}")

    company_facts = _sec_get_json(SEC_COMPANY_FACTS_URL.format(cik=cik))
    revenue_fact = _latest_quarterly_fact(
        company_facts,
        REVENUE_FACT_CANDIDATES,
        "USD",
    )
    net_income_fact = _latest_quarterly_fact(
        company_facts,
        NET_INCOME_FACT_CANDIDATES,
        "USD",
    )
    shares_fact = _latest_instant_fact(
        company_facts,
        ("EntityCommonStockSharesOutstanding",),
        "shares",
    )

    missing = []
    if revenue_fact is None:
        missing.append("quarterly revenue")
    if net_income_fact is None:
        missing.append("quarterly net income")
    if shares_fact is None:
        missing.append("shares outstanding")

    period_end = next(
        (
            fact.get("end")
            for fact in (revenue_fact, net_income_fact, shares_fact)
            if isinstance(fact, dict) and fact.get("end")
        ),
        None,
    )
    filed_date = None
    form = None
    for fact in (revenue_fact, net_income_fact, shares_fact):
        if not isinstance(fact, dict):
            continue
        filed_date = max(filter(None, [filed_date, fact.get("filed")]), default=None)
        form = form or fact.get("form")

    confidence = "high" if not missing else "partial"
    payload = {
        "ticker": normalized,
        "cik": cik,
        "source": "sec-companyfacts",
        "source_url": SEC_COMPANY_FACTS_URL.format(cik=cik),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "period_end": period_end,
        "filed_date": filed_date,
        "form": form,
        "latest_quarter_revenue": safe_float(
            revenue_fact.get("val") if isinstance(revenue_fact, dict) else None,
        ),
        "latest_quarter_net_income": safe_float(
            net_income_fact.get("val") if isinstance(net_income_fact, dict) else None,
        ),
        "shares_outstanding": safe_float(
            shares_fact.get("val") if isinstance(shares_fact, dict) else None,
        ),
        "revenue_concept": revenue_fact.get("concept") if isinstance(revenue_fact, dict) else None,
        "net_income_concept": (
            net_income_fact.get("concept") if isinstance(net_income_fact, dict) else None
        ),
        "shares_concept": shares_fact.get("concept") if isinstance(shares_fact, dict) else None,
        "confidence": confidence,
        "missing": missing,
    }
    try:
        payload["online_projection"] = fetch_online_projection(normalized)
    except Exception as exc:
        payload["online_projection_error"] = str(exc)
    return payload


def _within_one_percent(manual_value: Any, reported_value: Any) -> bool | None:
    manual = safe_float(manual_value)
    reported = safe_float(reported_value)
    if manual is None or reported is None:
        return None
    if manual == 0 and reported == 0:
        return True
    denominator = max(abs(manual), abs(reported))
    if denominator <= 0:
        return None
    return abs(manual - reported) / denominator <= 0.01


def _reported_actuals_match_manual(
    scenario: dict[str, Any],
    reported: dict[str, Any],
) -> bool:
    required = (
        _within_one_percent(
            scenario.get("latest_quarter_revenue"),
            reported.get("latest_quarter_revenue"),
        ),
        _within_one_percent(
            scenario.get("latest_quarter_net_income"),
            reported.get("latest_quarter_net_income"),
        ),
    )
    if any(value is not True for value in required):
        return False

    shares_match = _within_one_percent(
        scenario.get("shares_outstanding"),
        reported.get("shares_outstanding"),
    )
    return shares_match is not False


def refresh_reported_fundamentals(tickers: list[str]) -> dict[str, Any]:
    stored = load_reported_fundamentals()
    scenario_inputs = load_scenario_inputs()
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    auto_selected: list[dict[str, Any]] = []

    normalized_tickers = sorted(
        {
            normalize_ticker(ticker)
            for ticker in tickers
            if normalize_ticker(ticker)
        },
    )
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_sec_reported_fundamentals, ticker): ticker
            for ticker in normalized_tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                payload = future.result()
                stored[ticker] = payload
                refreshed.append(payload)
                scenario = scenario_inputs.get(ticker)
                if isinstance(scenario, dict) and _reported_actuals_match_manual(
                    scenario,
                    payload,
                ):
                    if scenario.get("actuals_source_preference") != "reported":
                        scenario["actuals_source_preference"] = "reported"
                        auto_selected.append(
                            {
                                "ticker": ticker,
                                "source": "reported",
                                "reason": "manual and pulled actuals within 1%",
                            },
                        )
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc)})

    save_reported_fundamentals(stored)
    if auto_selected:
        save_scenario_inputs(scenario_inputs)
    return {
        "refreshed": sorted(refreshed, key=lambda row: str(row.get("ticker") or "")),
        "auto_selected_actuals": auto_selected,
        "errors": errors,
        "fundamentals": stored,
    }


def _parse_nasdaq_calendar_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _nasdaq_earnings_for_date(day: datetime.date) -> list[dict[str, Any]]:
    response = requests.get(
        NASDAQ_EARNINGS_URL,
        params={"date": day.isoformat()},
        headers=NASDAQ_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []

    rows = data.get("rows")
    if not isinstance(rows, list):
        return []

    as_of = _parse_nasdaq_calendar_date(data.get("asOf")) or day.isoformat()
    results: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = normalize_ticker(row.get("symbol", ""))
        if not ticker:
            continue
        results.append(
            {
                "ticker": ticker,
                "next_earnings_date": as_of,
                "time": row.get("time"),
                "source": "nasdaq-earnings-calendar",
                "source_url": f"{NASDAQ_EARNINGS_URL}?date={as_of}",
                "confidence": "scheduled",
                "fiscal_quarter_ending": row.get("fiscalQuarterEnding"),
                "eps_forecast": row.get("epsForecast"),
                "estimate_count": row.get("noOfEsts"),
                "last_year_report_date": row.get("lastYearRptDt"),
                "last_year_eps": row.get("lastYearEPS"),
                "company_name": row.get("name"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return results


def _estimate_next_earnings_date(ticker: str, reported: dict[str, Any]) -> dict[str, Any] | None:
    filed_date_text = reported.get("filed_date")
    if not filed_date_text:
        return None
    try:
        next_date = datetime.fromisoformat(str(filed_date_text)).date() + timedelta(days=91)
    except ValueError:
        return None

    today = datetime.now(timezone.utc).date()
    while next_date < today:
        next_date += timedelta(days=91)

    return {
        "ticker": ticker,
        "next_earnings_date": next_date.isoformat(),
        "time": None,
        "source": "sec-filing-cadence-estimate",
        "source_url": reported.get("source_url"),
        "confidence": "estimated",
        "fiscal_quarter_ending": None,
        "eps_forecast": None,
        "estimate_count": None,
        "last_year_report_date": None,
        "last_year_eps": None,
        "company_name": None,
        "basis_filed_date": filed_date_text,
        "basis_period_end": reported.get("period_end"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh_earnings_calendar(tickers: list[str], days_ahead: int = 180) -> dict[str, Any]:
    requested = {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    stored = load_earnings_calendar()
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not requested:
        return {"refreshed": [], "errors": [], "calendar": stored}

    today = datetime.now(timezone.utc).date()
    days = max(1, min(int(days_ahead or 180), 365))
    dates = [today + timedelta(days=offset) for offset in range(days + 1)]
    found: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_nasdaq_earnings_for_date, day): day
            for day in dates
        }
        for future in as_completed(futures):
            day = futures[future]
            try:
                rows = future.result()
            except Exception as exc:
                errors.append({"ticker": "*", "error": f"{day.isoformat()}: {exc}"})
                continue
            for row in rows:
                ticker = row.get("ticker")
                if ticker in requested and (
                    ticker not in found
                    or row["next_earnings_date"] < found[ticker]["next_earnings_date"]
                ):
                    found[ticker] = row

    reported_fundamentals = load_reported_fundamentals()
    for ticker in sorted(requested):
        payload = found.get(ticker)
        if payload is None:
            reported = reported_fundamentals.get(ticker)
            if isinstance(reported, dict):
                payload = _estimate_next_earnings_date(ticker, reported)
        if payload is None:
            errors.append({"ticker": ticker, "error": "No scheduled date or SEC estimate available"})
            continue
        stored[ticker] = payload
        refreshed.append(payload)

    save_earnings_calendar(stored)
    return {
        "refreshed": refreshed,
        "errors": errors,
        "calendar": stored,
    }
