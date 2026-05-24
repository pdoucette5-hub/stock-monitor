from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.logic import merge_global_settings
from backend.models import (
    GlobalSettings,
    PortfolioConfig,
    PortfolioControlsUpdate,
    PortfolioViewResponse,
    ScenarioInputsResponse,
    StockScenarioResponse,
    TickerScenarioInputs,
)
from backend.portfolio_service import (
    apply_portfolio_controls,
    build_portfolio_views,
    normalize_portfolio,
)

BASE_DIR = Path(__file__).resolve().parent.parent

SCENARIO_STATE_FILE = BASE_DIR / "cache" / "scenario_inputs.json"
GLOBAL_SETTINGS_FILE = BASE_DIR / "cache" / "global_settings.json"
HOLDINGS_OVERRIDES_FILE = BASE_DIR / "cache" / "holdings_overrides.json"
TICKERS_FILE = BASE_DIR / "config" / "tickers.yaml"

SCENARIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
GLOBAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
HOLDINGS_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Stock Monitor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PortfolioSharesUpdate(BaseModel):
    ticker: str
    shares: float


def load_json_file(path: Path, default_data: Any) -> Any:
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


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def load_tickers_config() -> dict[str, Any]:
    if not TICKERS_FILE.exists():
        return {"portfolio": [], "watchlist": []}
    with open(TICKERS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {"portfolio": [], "watchlist": []}


def apply_holdings_overrides_to_config(
    tickers_config: dict[str, Any],
    overrides: dict[str, float],
) -> dict[str, Any]:
    portfolio = tickers_config.get("portfolio", []) or []
    updated_portfolio: list[dict[str, Any]] = []

    for item in portfolio:
        if isinstance(item, str):
            ticker = normalize_ticker(item)
            updated_portfolio.append(
                {
                    "ticker": ticker,
                    "shares": overrides.get(ticker),
                }
            )
            continue

        if isinstance(item, dict):
            ticker = normalize_ticker(item.get("ticker", ""))
            if not ticker:
                continue
            updated_item = dict(item)
            if ticker in overrides:
                updated_item["shares"] = overrides[ticker]
            updated_portfolio.append(updated_item)

    return {
        "portfolio": updated_portfolio,
        "watchlist": tickers_config.get("watchlist", []) or [],
    }


def get_effective_tickers_config() -> dict[str, Any]:
    base = load_tickers_config()
    overrides = load_holdings_overrides()
    return apply_holdings_overrides_to_config(base, overrides)


def serialize_ticker_scenario(raw: dict[str, Any]) -> TickerScenarioInputs:
    return TickerScenarioInputs.model_validate(raw)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.put("/api/portfolio/shares", response_model=PortfolioViewResponse)
def update_portfolio_shares(
    body: PortfolioSharesUpdate,
    force_refresh: bool = False,
) -> PortfolioViewResponse:
    ticker = normalize_ticker(body.ticker)
    if body.shares < 0:
        raise HTTPException(status_code=400, detail="Shares must be non-negative")

    tickers_config = get_effective_tickers_config()
    portfolio_rows = normalize_portfolio(tickers_config.get("portfolio", []))
    portfolio_tickers = {row["ticker"] for row in portfolio_rows}

    if ticker not in portfolio_tickers:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in portfolio")

    overrides = load_holdings_overrides()
    overrides[ticker] = float(body.shares)
    save_holdings_overrides(overrides)

    payload = build_portfolio_views(
        get_effective_tickers_config(),
        load_scenario_inputs(),
        load_settings_dict(),
        force_refresh=force_refresh,
    )
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
    updates = [item.model_dump(exclude_unset=True) for item in body.updates]
    scenario_inputs = apply_portfolio_controls(
        scenario_inputs,
        updates,
        portfolio_shares_map,
    )
    save_scenario_inputs(scenario_inputs)

    payload = build_portfolio_views(
        tickers_config,
        scenario_inputs,
        load_settings_dict(),
        force_refresh=force_refresh,
    )
    return PortfolioViewResponse.model_validate(payload)


@app.get("/api/portfolio/view", response_model=PortfolioViewResponse)
def get_portfolio_view(force_refresh: bool = False) -> PortfolioViewResponse:
    payload = build_portfolio_views(
        get_effective_tickers_config(),
        load_scenario_inputs(),
        load_settings_dict(),
        force_refresh=force_refresh,
    )
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
def get_tickers_config() -> PortfolioConfig:
    return PortfolioConfig.model_validate(get_effective_tickers_config())


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
    raw[normalized] = body.model_dump(mode="json")
    save_scenario_inputs(raw)
    return StockScenarioResponse(ticker=normalized, scenario=body)


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
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Frontend not built")