from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.logic import merge_global_settings
from backend.logic import safe_float
from backend.intraday_store import add_intraday_samples, latest_session_prices
from backend.models import (
    GlobalSettings,
    PortfolioConfig,
    PortfolioControlsUpdate,
    PortfolioViewResponse,
    ScenarioInputsResponse,
    StockScenarioResponse,
    TickerScenarioInputs,
)
from backend.performance_service import build_portfolio_performance
from backend.portfolio_service import (
    apply_portfolio_controls,
    build_portfolio_views,
    normalize_portfolio,
)
from backend.price_store import (
    get_price_comparison_response,
    get_price_history_response,
    get_price_history_status,
    get_price_points_for_ticker,
    load_price_history_store,
    upsert_price_rows,
)
from backend.sheets_service import push_tickers_to_sheet
from backend.transactions_service import compute_position_summary
from backend.config import (
    BASE_DIR,
    CONTROL_CHANGE_LABELS,
    GOOGLE_AUTH_ENABLED,
    GOOGLE_CLIENT_ID,
    INTRADAY_FINNHUB_API_KEY,
    MATTHEW_COMPENSATION_ACCOUNTS,
    MATTHEW_COMPENSATION_ACCOUNT_KEYS,
    PRICE_IMPORT_SECRET,
    SCENARIO_CHANGE_LABELS,
    UNASSIGNED_ACCOUNT,
    VALID_MANAGEMENT_MODES,
)
from backend.schemas import (
    AccountMergeRequest,
    ArchiveTickerRequest,
    EarningsCalendarRefreshRequest,
    FundamentalsRefreshRequest,
    ManagementModesUpdate,
    ManualAllocationUpdate,
    PortfolioSharesUpdate,
    PortfolioTickerUpsert,
    PriceImportRequest,
    RestoreTickerRequest,
    TransactionCreate,
    TransactionImportRequest,
    TransactionUpdate,
    WatchlistTickerUpsert,
)
from backend.utils import normalize_ticker
from backend.state_store import (
    append_portfolio_event,
    get_cached_response,
    invalidate_response_cache,
    load_account_aliases,
    load_earnings_calendar,
    load_holdings_overrides,
    load_management_modes,
    load_manual_allocations,
    load_portfolio_events,
    load_reported_fundamentals,
    load_scenario_inputs,
    load_settings_dict,
    load_state_bundle,
    load_tickers_overrides,
    load_transactions,
    save_account_aliases,
    save_holdings_overrides,
    save_management_modes,
    save_manual_allocations,
    save_scenario_inputs,
    save_settings_dict,
    save_tickers_overrides,
    save_transactions,
    set_cached_response,
)
from backend.fundamentals_service import (
    refresh_earnings_calendar,
    refresh_reported_fundamentals,
)
from backend.transaction_import import (
    existing_import_fingerprints,
    parse_fidelity_transaction_files,
    validate_transaction_payload,
)
from backend.auth import require_google_auth, current_user_email, has_full_access
from backend.tickers_config import (
    apply_holdings_overrides_to_config,
    apply_tickers_overrides_to_config,
    get_effective_tickers_config,
    get_limited_tickers_config,
    get_tickers_config_for_request,
    load_tickers_config,
    load_user_holdings,
    redact_event_for_limited,
    redact_tickers_config_for_limited,
    save_user_holdings,
    serialize_ticker_scenario,
)
from backend.management_service import (
    apply_account_aliases_to_transactions,
    apply_management_shares_to_config,
    automatic_account_mode,
    build_management_snapshot,
    build_position_summaries,
    filter_transactions_by_management_mode,
    management_position_key,
    merge_account_records,
    transaction_backed_tickers_with_shares,
)
from backend.forecast_service import (
    append_assumption_snapshot,
    build_change_set,
    build_forecast_scorecard,
)


app = FastAPI(title="Stock Monitor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registered after CORS, exactly as the decorator was before, so the
# middleware order is unchanged.
app.middleware("http")(require_google_auth)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/config")
def get_auth_config() -> dict[str, Any]:
    return {
        "enabled": GOOGLE_AUTH_ENABLED,
        "client_id": GOOGLE_CLIENT_ID if GOOGLE_AUTH_ENABLED else "",
    }


@app.get("/api/auth/me")
def get_auth_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        return {"authenticated": False, "role": "full"}
    return {
        "authenticated": True,
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "picture": user.get("picture") or "",
        "role": user.get("role") or "full",
    }


@app.get("/api/tickers")
def get_ticker_registry(request: Request) -> dict[str, Any]:
    if has_full_access(request):
        return {
            "base": load_tickers_config(),
            "overrides": load_tickers_overrides(),
            "effective": get_effective_tickers_config(),
        }

    redacted = redact_tickers_config_for_limited(
        {
            "base": load_tickers_config(),
            "overrides": load_tickers_overrides(),
            "effective": get_effective_tickers_config(),
        },
    )
    redacted["effective"] = get_limited_tickers_config(request)
    return redacted


def build_portfolio_view_for_request(
    request: Request,
    force_refresh: bool = False,
) -> dict[str, Any]:
    request_started = time.monotonic()
    full_access = has_full_access(request)
    load_state_bundle()
    bundle_loaded_at = time.monotonic()

    # Initialize cloud credentials and load the state bundle before reading the
    # separate price-history object. Concurrent first-use clients can contend on
    # credential initialization in Cloud Run and turn two small downloads into a
    # 20+ second wait.
    state = {
        "config": get_tickers_config_for_request(request),
        "scenarios": load_scenario_inputs(),
        "settings": load_settings_dict(),
        "earnings": load_earnings_calendar(),
        "fundamentals": load_reported_fundamentals(),
    }
    if full_access:
        state.update(
            transactions=load_transactions(),
            management_modes=load_management_modes(),
            manual_allocations=load_manual_allocations(),
        )
    state_loaded_at = time.monotonic()
    state["price_history"] = load_price_history_store()
    prices_loaded_at = time.monotonic()

    position_summaries = None
    management_shares = None
    if full_access:
        position_summaries = build_position_summaries(state["transactions"])
        management_shares = build_management_snapshot(
            transactions=state["transactions"],
            settings=state["management_modes"],
            manual_allocations=state["manual_allocations"],
            config=state["config"],
        )["shares_by_mode"]

    payload = build_portfolio_views(
        state["config"],
        state["scenarios"],
        state["settings"],
        management_shares=management_shares,
        position_summaries=position_summaries,
        earnings_calendar=state["earnings"],
        reported_fundamentals=state["fundamentals"],
        price_history=state["price_history"],
        force_refresh=force_refresh,
    )
    finished_at = time.monotonic()
    print(
        "portfolio_view_timing",
        json.dumps(
            {
                "bundle": round(bundle_loaded_at - request_started, 3),
                "state": round(state_loaded_at - bundle_loaded_at, 3),
                "prices": round(prices_loaded_at - state_loaded_at, 3),
                "build": round(finished_at - prices_loaded_at, 3),
                "total": round(finished_at - request_started, 3),
            },
        ),
        flush=True,
    )
    return payload


@app.get("/api/tickers/shared")
def get_shared_ticker_registry() -> dict[str, Any]:
    return {
        "base": load_tickers_config(),
        "overrides": load_tickers_overrides(),
        "effective": get_effective_tickers_config(),
    }


@app.post("/api/sheets/tickers/sync")
def sync_tickers_to_sheet() -> dict[str, Any]:
    try:
        return push_tickers_to_sheet(get_effective_tickers_config())
    except requests.HTTPError as exc:
        detail = str(exc)
        if exc.response is not None:
            detail = exc.response.text or detail
        raise HTTPException(status_code=502, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/events")
def get_portfolio_events(
    request: Request,
    ticker: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    events = load_portfolio_events()

    if ticker:
        normalized = normalize_ticker(ticker)
        events = [event for event in events if event.get("ticker") == normalized]

    events = sorted(
        events,
        key=lambda event: str(event.get("timestamp", "")),
        reverse=True,
    )

    limit = max(1, min(int(limit), 5000))
    events = events[:limit]
    if not has_full_access(request):
        events = [redact_event_for_limited(event) for event in events]

    return {
        "count": len(events),
        "events": events,
    }


@app.get("/api/forecasts")
def get_forecast_scorecard(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Forecast scorecard is private")
    return build_forecast_scorecard()


@app.get("/api/fundamentals/reported")
def get_reported_fundamentals(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Reported fundamentals are private")
    return {"fundamentals": load_reported_fundamentals()}


@app.post("/api/fundamentals/reported/refresh")
def refresh_reported_fundamentals_endpoint(
    request: Request,
    body: FundamentalsRefreshRequest,
) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Reported fundamentals are private")

    tickers = [normalize_ticker(ticker) for ticker in body.tickers]
    tickers = [ticker for ticker in tickers if ticker]
    if not tickers:
        config = get_effective_tickers_config()
        portfolio = normalize_portfolio(config.get("portfolio", []))
        tickers = sorted(
            {
                row["ticker"]
                for row in portfolio
                if row.get("ticker")
            }
        )

    payload = refresh_reported_fundamentals(tickers)
    invalidate_response_cache()
    return payload


@app.get("/api/earnings-calendar")
def get_earnings_calendar(request: Request) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Earnings calendar is private")
    return {"calendar": load_earnings_calendar()}


@app.post("/api/earnings-calendar/refresh")
def refresh_earnings_calendar_endpoint(
    request: Request,
    body: EarningsCalendarRefreshRequest,
) -> dict[str, Any]:
    if not has_full_access(request):
        raise HTTPException(status_code=403, detail="Earnings calendar is private")

    tickers = [normalize_ticker(ticker) for ticker in body.tickers]
    tickers = [ticker for ticker in tickers if ticker]
    if not tickers:
        config = get_effective_tickers_config()
        portfolio = normalize_portfolio(config.get("portfolio", []))
        tickers = sorted(
            {
                row["ticker"]
                for row in portfolio
                if row.get("ticker")
            }
            | {
                normalize_ticker(ticker)
                for ticker in config.get("watchlist", [])
                if normalize_ticker(ticker)
            },
        )

    payload = refresh_earnings_calendar(tickers, days_ahead=body.days_ahead)
    invalidate_response_cache()
    return payload


@app.get("/api/performance/portfolio")
def get_portfolio_performance(
    range: str = "3y",
    accounts: str = "",
    mode: str = "all",
    force_refresh: bool = False,
) -> dict[str, Any]:
    try:
        cache_key = (
            f"performance:v2:{str(range).strip().lower()}:"
            f"{str(accounts).strip().casefold()}:{str(mode).strip().lower()}"
        )
        if not force_refresh:
            cached = get_cached_response(cache_key)
            if cached is not None:
                return cached

        transactions = load_transactions()
        tickers_config = get_effective_tickers_config()
        management = build_management_snapshot()
        normalized_mode = str(mode or "all").strip().lower()
        if normalized_mode != "all" and normalized_mode not in VALID_MANAGEMENT_MODES:
            raise ValueError("Mode must be all, managed, track, or excluded")

        if normalized_mode in VALID_MANAGEMENT_MODES:
            transactions = filter_transactions_by_management_mode(
                transactions,
                normalized_mode,
            )
            tickers_config = apply_management_shares_to_config(
                tickers_config,
                management["shares_by_mode"],
                normalized_mode,
            )
        account_filter = [
            account.strip()
            for account in str(accounts).split(",")
            if account.strip()
        ]
        payload = build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=tickers_config,
            range_key=range,
            accounts=account_filter or None,
            supplemental_positions=[
                position
                for position in management["positions"]
                if position["source"] != "transactions"
                and (
                    normalized_mode == "all"
                    or position["mode"] == normalized_mode
                )
            ],
        )
        return set_cached_response(cache_key, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def transaction_cash_flow_amount(tx: dict[str, Any]) -> tuple[float, float]:
    tx_type = str(tx.get("type", "")).strip().lower()
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0

    if tx_type in {"buy", "transfer_in", "adjustment"}:
        if shares <= 0 or price_per_share is None:
            return 0.0, 0.0
        return (shares * price_per_share) + fees, 0.0

    if tx_type in {"sell", "transfer_out"}:
        if shares <= 0 or price_per_share is None:
            return 0.0, 0.0
        return -max((shares * price_per_share) - fees, 0.0), 0.0

    if tx_type == "dividend":
        return 0.0, max(price_per_share or 0.0, 0.0)

    return 0.0, 0.0


def benchmark_close_on_or_after(
    benchmark_points: list[dict[str, Any]],
    flow_date: str,
) -> float | None:
    for point in benchmark_points:
        point_date = str(point.get("date") or "")
        close = safe_float(point.get("close"))
        if point_date >= flow_date and close is not None and close > 0:
            return close
    return None


def benchmark_close_on_or_before(
    benchmark_lookup: dict[str, float],
    sorted_dates: list[str],
    target_date: str,
) -> float | None:
    for point_date in reversed(sorted_dates):
        if point_date <= target_date:
            return benchmark_lookup.get(point_date)
    return None


def filter_transactions_by_accounts(
    transactions: dict[str, list[dict[str, Any]]],
    accounts: set[str],
) -> dict[str, list[dict[str, Any]]]:
    selected = {account.casefold() for account in accounts}
    filtered: dict[str, list[dict[str, Any]]] = {}
    for ticker, entries in transactions.items():
        rows = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("account") or "").strip().casefold() in selected
        ]
        if rows:
            filtered[ticker] = rows
    return filtered


def sorted_transaction_rows_with_ticker(
    transactions: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ticker, entries in transactions.items():
        for entry in entries:
            if isinstance(entry, dict):
                rows.append({**entry, "ticker": ticker})
    return sorted(
        rows,
        key=lambda tx: (
            str(tx.get("date") or ""),
            str(tx.get("id") or ""),
            str(tx.get("ticker") or ""),
        ),
    )


def transaction_trade_amount(tx: dict[str, Any]) -> float:
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0
    if shares <= 0 or price_per_share is None:
        return 0.0
    return (shares * price_per_share) + fees


def transaction_sell_proceeds(tx: dict[str, Any]) -> float:
    shares = safe_float(tx.get("shares")) or 0.0
    price_per_share = safe_float(tx.get("price_per_share"))
    fees = safe_float(tx.get("fees")) or 0.0
    if shares <= 0 or price_per_share is None:
        return 0.0
    return max((shares * price_per_share) - fees, 0.0)


def compensation_same_day_priority(tx: dict[str, Any]) -> int:
    tx_type = str(tx.get("type") or "").strip().lower()
    if tx_type in {"sell", "dividend", "transfer_out"}:
        return 0
    if tx_type in {"buy", "transfer_in", "adjustment"}:
        return 1
    return 2


@app.get("/api/compensation")
def get_compensation_tracker(
    range: str = "1y",
    benchmark: str = "SPY",
    share_pct: float = 0.33,
) -> dict[str, Any]:
    try:
        normalized_benchmark = normalize_ticker(benchmark) or "SPY"
        payout_share = max(0.0, min(float(share_pct), 1.0))
        transactions = filter_transactions_by_accounts(
            load_transactions(),
            MATTHEW_COMPENSATION_ACCOUNTS,
        )
        management = build_management_snapshot()
        performance = build_portfolio_performance(
            transactions_by_ticker=transactions,
            tickers_config=get_effective_tickers_config(),
            range_key=range,
            accounts=sorted(MATTHEW_COMPENSATION_ACCOUNTS),
            supplemental_positions=[
                position
                for position in management["positions"]
                if position["source"] != "transactions"
                and str(position.get("account") or "").strip().casefold()
                in MATTHEW_COMPENSATION_ACCOUNT_KEYS
            ],
        )
        portfolio_series = performance.get("series")
        if not isinstance(portfolio_series, list):
            portfolio_series = []
        valid_portfolio = [
            row
            for row in portfolio_series
            if isinstance(row, dict) and safe_float(row.get("market_value")) is not None
        ]
        benchmark_points = [
            row
            for row in get_price_points_for_ticker(normalized_benchmark, range_key=range)
            if isinstance(row, dict) and safe_float(row.get("close")) is not None
        ]

        if len(valid_portfolio) < 2:
            raise ValueError("Need at least two portfolio performance points")
        if len(benchmark_points) < 2:
            raise ValueError(f"Need at least two price points for {normalized_benchmark}")

        portfolio_dates = [str(row.get("date") or "") for row in valid_portfolio]
        benchmark_dates = [str(row.get("date") or "") for row in benchmark_points]
        first_positive_portfolio_date = next(
            (
                str(row.get("date") or "")
                for row in valid_portfolio
                if (safe_float(row.get("market_value")) or 0.0) > 0
            ),
            "",
        )
        if not first_positive_portfolio_date:
            raise ValueError("Need a positive recorded portfolio value")
        start_date = max(first_positive_portfolio_date, benchmark_dates[0])
        end_date = min(portfolio_dates[-1], benchmark_dates[-1])
        if start_date >= end_date:
            raise ValueError("Need overlapping portfolio and benchmark history")

        window_portfolio = [
            row
            for row in valid_portfolio
            if start_date <= str(row.get("date") or "") <= end_date
        ]
        window_benchmark = [
            row
            for row in benchmark_points
            if start_date <= str(row.get("date") or "") <= end_date
        ]
        if len(window_portfolio) < 2 or len(window_benchmark) < 2:
            raise ValueError("Need at least two overlapping performance points")

        start_portfolio = window_portfolio[0]
        end_portfolio = window_portfolio[-1]
        start_value = safe_float(start_portfolio.get("market_value")) or 0.0
        end_value = safe_float(end_portfolio.get("market_value")) or 0.0
        end_cost_basis = safe_float(end_portfolio.get("cost_basis")) or 0.0
        if start_value <= 0:
            raise ValueError("Starting portfolio value must be positive")
        if end_cost_basis <= 0:
            raise ValueError("Current cost basis must be positive")

        benchmark_lookup = {
            str(row.get("date") or ""): safe_float(row.get("close")) or 0.0
            for row in window_benchmark
            if row.get("date") and safe_float(row.get("close")) is not None
        }
        benchmark_dates_sorted = sorted(benchmark_lookup)
        start_portfolio_date = str(start_portfolio.get("date") or start_date)
        end_portfolio_date = str(end_portfolio.get("date") or end_date)
        start_close = benchmark_close_on_or_before(
            benchmark_lookup,
            benchmark_dates_sorted,
            start_portfolio_date,
        ) or 0.0
        benchmark_as_of = next(
            (
                point_date
                for point_date in reversed(benchmark_dates_sorted)
                if point_date <= end_portfolio_date
            ),
            "",
        )
        end_close = benchmark_close_on_or_before(
            benchmark_lookup,
            benchmark_dates_sorted,
            end_portfolio_date,
        ) or 0.0
        if start_close <= 0:
            raise ValueError(f"Starting {normalized_benchmark} price must be positive")
        if end_close <= 0:
            raise ValueError(f"Ending {normalized_benchmark} price must be positive")

        # Performance already derives contributed capital from the complete
        # transaction ledger, accounting for sale proceeds and dividends that
        # are reinvested. Rebuilding that path from the selected window's
        # opening market value changes the basis whenever the range changes.
        benchmark_series: list[dict[str, Any]] = []
        for row in window_portfolio:
            row_date = str(row.get("date") or "")
            if not row_date:
                continue
            capital_basis = safe_float(row.get("capital_basis"))
            benchmark_value_for_date = safe_float(row.get("benchmark_value"))
            if capital_basis is None or benchmark_value_for_date is None:
                continue
            cost_basis = safe_float(row.get("cost_basis")) or 0.0
            market_value = safe_float(row.get("market_value")) or 0.0
            benchmark_series.append(
                {
                    "date": row_date,
                    "market_value": round(market_value, 8),
                    "cost_basis": round(cost_basis, 8),
                    "capital_basis": round(capital_basis, 8),
                    "benchmark_value": round(benchmark_value_for_date, 8),
                }
            )
        if len(benchmark_series) < 2:
            raise ValueError("Need at least two benchmark-equivalent points")

        invested_capital = benchmark_series[-1]["capital_basis"]
        if invested_capital <= 0:
            raise ValueError("Current capital basis must be positive")

        distributions = 0.0
        for ticker, entries in transactions.items():
            for tx in entries:
                if not isinstance(tx, dict):
                    continue
                tx_date = str(tx.get("date") or "").strip()
                if not tx_date or not (start_portfolio_date < tx_date <= end_portfolio_date):
                    continue
                _, distribution = transaction_cash_flow_amount(tx)
                if distribution > 0:
                    distributions += distribution

        actual_terminal_value = end_value + distributions
        spy_window_return = (end_close / start_close) - 1.0
        benchmark_value = benchmark_series[-1]["benchmark_value"]
        actual_gain = actual_terminal_value - invested_capital
        benchmark_gain = benchmark_value - invested_capital
        excess_gain = actual_terminal_value - benchmark_value
        portfolio_return = actual_gain / invested_capital if invested_capital > 0 else 0.0
        benchmark_return = benchmark_gain / invested_capital if invested_capital > 0 else 0.0
        excess_return = portfolio_return - benchmark_return
        payout_base = max(excess_gain, 0.0)
        payout = payout_base * payout_share

        return {
            "range": range,
            "benchmark": normalized_benchmark,
            "mode": "matthew",
            "accounts": sorted(MATTHEW_COMPENSATION_ACCOUNTS),
            "share_pct": payout_share,
            "start_date": start_portfolio_date,
            "end_date": end_portfolio_date,
            "portfolio_start_value": start_value,
            "portfolio_end_value": end_value,
            "cost_basis": end_cost_basis,
            "invested_capital": invested_capital,
            "portfolio_return": portfolio_return,
            "actual_gain": actual_gain,
            "distributions": distributions,
            "actual_terminal_value": actual_terminal_value,
            "benchmark_start_price": start_close,
            "benchmark_end_price": end_close,
            "benchmark_as_of": benchmark_as_of,
            "spy_window_return": spy_window_return,
            "benchmark_return": benchmark_return,
            "benchmark_equivalent_value": benchmark_value,
            "benchmark_gain": benchmark_gain,
            "excess_return": excess_return,
            "excess_gain": excess_gain,
            "payout_base": payout_base,
            "payout": payout,
            "series": benchmark_series,
            "portfolio_series_points": len(window_portfolio),
            "benchmark_points": len(window_benchmark),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/prices/import")
def import_price_history(
    body: PriceImportRequest,
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
        import_rows = [row.model_dump(mode="json") for row in body.rows]
        # GOOGLEFINANCE intermittently omits VENU. Keep the normal sheet feed for
        # every other ticker, but recover missing VENU closes from Nasdaq.
        try:
            from datetime import date, timedelta
            from backend.venu_price_fallback import fetch_venu_history, fetch_venu_latest_trade

            status = get_price_history_status(["VENU"])["tickers"]["VENU"]
            latest = status["latest_date"]
            since = date.fromisoformat(latest) - timedelta(days=1) if latest else date.today() - timedelta(days=30)
            sheet_dates = set()
            for row in import_rows:
                if str(row.get("ticker", "")).upper() != "VENU":
                    continue
                try:
                    if float(row.get("close")) > 0:
                        sheet_dates.add(str(row.get("date")))
                except (TypeError, ValueError):
                    continue
            import_rows.extend(
                row for row in fetch_venu_history(since)
                if row["date"] not in sheet_dates
            )
            quote = fetch_venu_latest_trade()
            if quote and quote["date"] not in sheet_dates:
                import_rows.append(quote)
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"VENU history fallback unavailable: {exc}")

        result = upsert_price_rows(
            import_rows,
            default_source="googlefinance",
        )
        invalidate_response_cache()
        return {
            "status": "ok",
            **result,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/prices/status")
def get_price_import_status(tickers: str = "") -> dict[str, Any]:
    ticker_list = [
        normalize_ticker(ticker)
        for ticker in str(tickers).split(",")
        if str(ticker).strip()
    ]
    return get_price_history_status(ticker_list or None)


@app.put("/api/tickers/portfolio", response_model=PortfolioConfig)
def add_portfolio_ticker(request: Request, body: PortfolioTickerUpsert) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        if body.shares > 0:
            holdings[ticker] = float(body.shares)
        else:
            holdings.pop(ticker, None)
        save_user_holdings(current_user_email(request), holdings)
        return PortfolioConfig.model_validate(get_limited_tickers_config(request))

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    tickers_map[ticker] = {
        "list": "portfolio",
        "shares": float(body.shares),
        "archived": False,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    holdings = load_holdings_overrides()
    holdings[ticker] = float(body.shares)
    save_holdings_overrides(holdings)

    append_portfolio_event(
        "add_portfolio",
        ticker,
        {"shares": float(body.shares)},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.put("/api/tickers/watchlist", response_model=PortfolioConfig)
def add_watchlist_ticker(body: WatchlistTickerUpsert) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    tickers_map[ticker] = {
        "list": "watchlist",
        "shares": None,
        "archived": False,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    append_portfolio_event(
        "add_watchlist",
        ticker,
        {},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.put("/api/tickers/archive", response_model=PortfolioConfig)
def archive_ticker(body: ArchiveTickerRequest) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    effective = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(effective.get("portfolio", []))
    portfolio_set = {row["ticker"] for row in portfolio_rows}
    watchlist_set = {normalize_ticker(t) for t in effective.get("watchlist", [])}

    if ticker not in portfolio_set and ticker not in watchlist_set:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in active tracking")

    previous_list = "portfolio" if ticker in portfolio_set else "watchlist"

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(ticker, {})
    tickers_map[ticker] = {
        "list": current.get("list") or previous_list,
        "shares": current.get("shares"),
        "archived": True,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    append_portfolio_event(
        "archive_ticker",
        ticker,
        {"previous_list": previous_list},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.put("/api/tickers/restore", response_model=PortfolioConfig)
def restore_ticker(body: RestoreTickerRequest) -> PortfolioConfig:
    ticker = normalize_ticker(body.ticker)
    target_list = str(body.list).strip().lower()

    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if target_list not in {"portfolio", "watchlist"}:
        raise HTTPException(status_code=400, detail="Restore list must be portfolio or watchlist")

    overrides = load_tickers_overrides()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(ticker, {})

    tickers_map[ticker] = {
        "list": target_list,
        "shares": current.get("shares"),
        "archived": False,
        "removed": False,
    }
    save_tickers_overrides(overrides)

    append_portfolio_event(
        "restore_ticker",
        ticker,
        {"target_list": target_list},
    )

    return PortfolioConfig.model_validate(get_effective_tickers_config())


@app.delete("/api/tickers/{ticker}", response_model=PortfolioConfig)
def remove_ticker(
    request: Request,
    ticker: str,
    background_tasks: BackgroundTasks,
) -> PortfolioConfig:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        holdings.pop(normalized, None)
        save_user_holdings(current_user_email(request), holdings)
        return PortfolioConfig.model_validate(get_limited_tickers_config(request))

    with ThreadPoolExecutor(max_workers=2) as executor:
        overrides_future = executor.submit(load_tickers_overrides)
        holdings_future = executor.submit(load_holdings_overrides)
        overrides = overrides_future.result()
        holdings = holdings_future.result()
    tickers_map = overrides.setdefault("tickers", {})
    current = tickers_map.get(normalized, {})

    tickers_map[normalized] = {
        "list": current.get("list"),
        "shares": current.get("shares"),
        "archived": False,
        "removed": True,
    }
    holdings_changed = normalized in holdings
    if holdings_changed:
        del holdings[normalized]

    with ThreadPoolExecutor(max_workers=2) as executor:
        writes = [executor.submit(save_tickers_overrides, overrides)]
        if holdings_changed:
            writes.append(executor.submit(save_holdings_overrides, holdings))
        for write in writes:
            write.result()

    background_tasks.add_task(
        append_portfolio_event,
        "remove_ticker",
        normalized,
        {},
    )

    effective = apply_tickers_overrides_to_config(load_tickers_config(), overrides)
    effective = apply_holdings_overrides_to_config(effective, holdings)
    return PortfolioConfig.model_validate(effective)


@app.get("/api/accounts")
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


@app.get("/api/management")
def get_management_settings() -> dict[str, Any]:
    return build_management_snapshot()


@app.put("/api/management")
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


@app.put("/api/management/allocation")
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


@app.put("/api/accounts/merge")
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


@app.post("/api/transactions/import")
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


@app.get("/api/transactions/{ticker}")
def get_transactions_for_ticker(ticker: str) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    transactions = load_transactions()
    return {
        "ticker": normalized,
        "transactions": transactions.get(normalized, []),
    }


@app.post("/api/transactions/{ticker}")
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


@app.put("/api/transactions/{ticker}/{transaction_id}")
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


@app.delete("/api/transactions/{ticker}/{transaction_id}")
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


@app.get("/api/position/{ticker}")
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


@app.get("/api/prices/history")
def get_price_history_endpoint(
    ticker: str,
    range: str = "3y",
) -> dict[str, Any]:
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Ticker is required")

    try:
        return get_price_history_response(normalized, range_key=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/prices/compare")
def get_price_comparison(
    tickers: str,
    range: str = "3y",
) -> dict[str, Any]:
    raw_tickers = [
        normalize_ticker(ticker)
        for ticker in str(tickers).split(",")
        if str(ticker).strip()
    ]

    try:
        if str(range).strip().lower() == "1d":
            price_series = latest_session_prices(raw_tickers)
            if any(len(points) >= 2 for points in price_series.values()):
                daily_fallback = get_price_comparison_response(raw_tickers, range_key="1d").get(
                    "price_series",
                    {},
                )
                price_series = {
                    ticker: (
                        points
                        if len(points) >= 2
                        else daily_fallback.get(ticker, [])
                    )
                    for ticker, points in price_series.items()
                }
                return {
                    "range": "1d",
                    "resolution": "5",
                    "tickers": raw_tickers,
                    "series": price_series,
                    "price_series": price_series,
                }
        return get_price_comparison_response(raw_tickers, range_key=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/prices/intraday/sample")
def sample_intraday_prices(
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    if not PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=500, detail="PRICE_IMPORT_SECRET is not configured")
    if x_import_secret != PRICE_IMPORT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid import secret")

    shares_by_mode = build_management_snapshot()["shares_by_mode"]
    tickers = sorted(
        ticker
        for ticker, shares in shares_by_mode.items()
        if float(shares.get("managed", 0.0)) + float(shares.get("track", 0.0)) > 0
    )
    samples: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    def fetch_quote(ticker: str) -> dict[str, Any]:
        if ticker == "VENU":
            from backend.venu_price_fallback import fetch_venu_latest_trade

            quote = fetch_venu_latest_trade()
            if not quote:
                raise ValueError("Nasdaq did not return a VENU trade")
            timestamp = int(datetime.fromisoformat(str(quote["fetched_at"])).timestamp())
            return {"ticker": ticker, "timestamp": timestamp, "close": float(quote["close"])}
        if not INTRADAY_FINNHUB_API_KEY:
            raise ValueError("INTRADAY_FINNHUB_API_KEY is not configured")
        response = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": INTRADAY_FINNHUB_API_KEY},
            timeout=20,
        )
        response.raise_for_status()
        quote = response.json()
        return {
            "ticker": ticker,
            "timestamp": int(quote.get("t") or time.time()),
            "close": safe_float(quote.get("c")) or 0.0,
        }

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as executor:
        futures = {executor.submit(fetch_quote, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                sample = future.result()
                if sample["close"] > 0:
                    samples.append(sample)
            except Exception as exc:
                errors[ticker] = str(exc)

    result = add_intraday_samples(samples)
    return {"status": "ok", **result, "requested": len(tickers), "errors": errors}


@app.post("/api/prices/intraday/refresh")
def refresh_intraday_prices() -> dict[str, Any]:
    return sample_intraday_prices(x_import_secret=PRICE_IMPORT_SECRET)


@app.put("/api/portfolio/shares", response_model=PortfolioViewResponse)
def update_portfolio_shares(
    request: Request,
    body: PortfolioSharesUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    ticker = normalize_ticker(body.ticker)
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    if not has_full_access(request):
        holdings = load_user_holdings(current_user_email(request))
        if ticker not in holdings:
            raise HTTPException(status_code=404, detail=f"{ticker} not found in your portfolio")
        if body.shares > 0:
            holdings[ticker] = float(body.shares)
        else:
            holdings.pop(ticker, None)
        save_user_holdings(current_user_email(request), holdings)
        payload = build_portfolio_view_for_request(request, force_refresh=force_refresh)
        return PortfolioViewResponse.model_validate(payload)

    tickers_config = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_tickers = {row["ticker"] for row in portfolio_rows}

    if ticker not in portfolio_tickers:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in portfolio")

    overrides = load_holdings_overrides()
    overrides[ticker] = float(body.shares)
    save_holdings_overrides(overrides)

    ticker_overrides = load_tickers_overrides()
    tickers_map = ticker_overrides.setdefault("tickers", {})
    existing = tickers_map.get(ticker)
    if isinstance(existing, dict) and existing.get("list") == "portfolio":
        existing["shares"] = float(body.shares)
        tickers_map[ticker] = existing
        save_tickers_overrides(ticker_overrides)

    append_portfolio_event(
        "update_shares",
        ticker,
        {"shares": float(body.shares)},
    )

    payload = build_portfolio_views(
        get_effective_tickers_config(),
        load_scenario_inputs(),
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.put("/api/portfolio/controls", response_model=PortfolioViewResponse)
def update_portfolio_controls(
    body: PortfolioControlsUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    tickers_config = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_shares_map = {row["ticker"]: row["shares"] for row in portfolio_rows}

    scenario_inputs = load_scenario_inputs()
    previous_by_ticker = {
        str(item.ticker).strip().upper(): scenario_inputs.get(str(item.ticker).strip().upper(), {})
        for item in body.updates
    }
    updates = [item.model_dump(exclude_unset=True) for item in body.updates]
    scenario_inputs = apply_portfolio_controls(
        scenario_inputs,
        updates,
        portfolio_shares_map,
    )
    save_scenario_inputs(scenario_inputs)

    for item in updates:
        ticker = normalize_ticker(item.get("ticker", ""))
        if not ticker:
            continue

        current = scenario_inputs.get(ticker, {})
        previous = previous_by_ticker.get(ticker, {})
        old_control_values = {
            "show_in_holdings": (previous.get("display_rules") or {}).get("show_in_holdings"),
            "include_in_redistribution": (previous.get("redistribution_rules") or {}).get(
                "include_in_redistribution",
            ),
            "eligible_redistribution_shares": (previous.get("redistribution_rules") or {}).get(
                "eligible_redistribution_shares",
            ),
        }
        new_control_values = {
            "show_in_holdings": (current.get("display_rules") or {}).get("show_in_holdings"),
            "include_in_redistribution": (current.get("redistribution_rules") or {}).get(
                "include_in_redistribution",
            ),
            "eligible_redistribution_shares": (current.get("redistribution_rules") or {}).get(
                "eligible_redistribution_shares",
            ),
        }
        changes = build_change_set(
            old_control_values,
            new_control_values,
            CONTROL_CHANGE_LABELS,
        )
        if changes:
            append_portfolio_event(
                "update_controls",
                ticker,
                {"changes": changes},
            )

    payload = build_portfolio_views(
        tickers_config,
        scenario_inputs,
        load_settings_dict(),
        management_shares=build_management_snapshot()["shares_by_mode"],
        position_summaries=build_position_summaries(),
        earnings_calendar=load_earnings_calendar(),
        reported_fundamentals=load_reported_fundamentals(),
        force_refresh=force_refresh,
    )
    set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio/view", response_model=PortfolioViewResponse)
def get_portfolio_view(request: Request, force_refresh: bool = False) -> PortfolioViewResponse:
    if has_full_access(request) and not force_refresh:
        cached = get_cached_response("portfolio_view")
        if cached is not None:
            return PortfolioViewResponse.model_validate(cached)

    payload = build_portfolio_view_for_request(request, force_refresh=force_refresh)
    if has_full_access(request):
        set_cached_response("portfolio_view", payload)
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio", response_model=ScenarioInputsResponse)
def get_portfolio_scenarios() -> ScenarioInputsResponse:
    raw = load_scenario_inputs()
    scenarios = {
        ticker: serialize_ticker_scenario(state)
        for ticker, state in raw.items()
        if isinstance(state, dict)
    }
    return ScenarioInputsResponse(scenarios=scenarios)


@app.put("/api/portfolio", response_model=ScenarioInputsResponse)
def replace_portfolio_scenarios(body: ScenarioInputsResponse) -> ScenarioInputsResponse:
    payload = {
        ticker: scenario.model_dump(mode="json")
        for ticker, scenario in body.scenarios.items()
    }
    save_scenario_inputs(payload)
    return body


@app.get("/api/config/tickers", response_model=PortfolioConfig)
def get_tickers_config(request: Request) -> PortfolioConfig:
    return PortfolioConfig.model_validate(get_tickers_config_for_request(request))


@app.get("/api/stock/{ticker}", response_model=StockScenarioResponse)
def get_stock_scenario(ticker: str) -> StockScenarioResponse:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    if normalized not in raw or not isinstance(raw[normalized], dict):
        raise HTTPException(status_code=404, detail=f"No scenario found for {normalized}")
    return StockScenarioResponse(
        ticker=normalized,
        scenario=serialize_ticker_scenario(raw[normalized]),
    )


@app.put("/api/stock/{ticker}", response_model=StockScenarioResponse)
def upsert_stock_scenario(ticker: str, body: TickerScenarioInputs) -> StockScenarioResponse:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    existing = raw.get(normalized)
    payload = body.model_dump(mode="json")

    if isinstance(existing, dict):
        for key in ("redistribution_rules", "display_rules", "trade_rules"):
            if isinstance(existing.get(key), dict):
                payload[key] = existing[key]

    raw[normalized] = payload
    save_scenario_inputs(raw)

    changes = build_change_set(
        existing if isinstance(existing, dict) else None,
        payload,
        SCENARIO_CHANGE_LABELS,
    )
    if changes:
        append_portfolio_event(
            "update_assumptions",
            normalized,
            {"changes": changes},
        )
        try:
            snapshot_view = build_portfolio_views(
                get_effective_tickers_config(),
                raw,
                load_settings_dict(),
                management_shares=build_management_snapshot()["shares_by_mode"],
                position_summaries=build_position_summaries(),
                earnings_calendar=load_earnings_calendar(),
                reported_fundamentals=load_reported_fundamentals(),
                force_refresh=False,
            )
            snapshot_row = next(
                (
                    row
                    for section in ("portfolio", "watchlist")
                    for row in snapshot_view.get(section, [])
                    if isinstance(row, dict) and row.get("ticker") == normalized
                ),
                None,
            )
            append_assumption_snapshot(
                normalized,
                payload,
                snapshot_row,
                previous_scenario=existing if isinstance(existing, dict) else None,
                changes=changes,
            )
        except Exception:
            pass

    return StockScenarioResponse(
        ticker=normalized,
        scenario=serialize_ticker_scenario(payload),
    )


@app.delete("/api/stock/{ticker}")
def delete_stock_scenario(ticker: str) -> dict[str, str]:
    normalized = normalize_ticker(ticker)
    raw = load_scenario_inputs()
    if normalized not in raw:
        raise HTTPException(status_code=404, detail=f"No scenario found for {normalized}")
    del raw[normalized]
    save_scenario_inputs(raw)
    return {"status": "deleted", "ticker": normalized}


@app.get("/api/settings", response_model=GlobalSettings)
def get_global_settings() -> GlobalSettings:
    return GlobalSettings.model_validate(load_settings_dict())


@app.put("/api/settings", response_model=GlobalSettings)
def update_global_settings(body: GlobalSettings) -> GlobalSettings:
    payload = body.model_dump(mode="json")
    save_settings_dict(payload)
    return GlobalSettings.model_validate(merge_global_settings(payload))


static_dir = BASE_DIR / "backend" / "static"
assets_dir = static_dir / "assets"
index_file = static_dir / "index.html"

if static_dir.exists():
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{rest_of_path:path}")
    async def serve_spa(rest_of_path: str):
        if index_file.exists():
            return FileResponse(
                str(index_file),
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        raise HTTPException(status_code=404, detail="Frontend not built")
