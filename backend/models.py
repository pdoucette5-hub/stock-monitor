from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

FORECAST_YEARS = 3


class GrowthPePoint(BaseModel):
    growth: float
    pe: float


class GlobalSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    target_cagr_threshold_pct: float = 15.0
    strong_buy_base_premium_pct: float = 15.0
    buy_base_premium_pct: float = 3.0
    buy_bear_buffer_pct: float = 5.0
    risk_base_shortfall_pct: float = 5.0
    speculative_bull_premium_pct: float = 50.0
    portfolio_table_height: int = 800
    watchlist_table_height: int = 650
    growth_pe_table: list[GrowthPePoint] = Field(default_factory=list)
    bottom_pinned_tickers: list[str] = Field(default_factory=list)
    buy_hurdle_pct: float = 15.0
    max_position_weight_pct: float = 10.0
    min_position_weight_pct: float = 0.0
    rebalance_band_pct: float = 0.75
    rebalance_step_pct: float = 25.0
    min_trade_dollars: float = 1000.0
    trim_hurdle_pct: Optional[float] = None
    max_new_buy_weight_pct: Optional[float] = None
    bottom_pinned_actions: Optional[list[str]] = None
    cagr_threshold_pct: Optional[float] = None
    buy_bear_within_threshold_pct: Optional[float] = None
    risk_base_below_threshold_pct: Optional[float] = None


class ScenarioAssumptions(BaseModel):
    rev_growth_rates: list[float] = Field(
        min_length=FORECAST_YEARS,
        max_length=FORECAST_YEARS,
    )
    net_income_growth_rates: list[float] = Field(
        min_length=FORECAST_YEARS,
        max_length=FORECAST_YEARS,
    )
    durable_growth_view: float
    growth_weight_pct: float


class RedistributionRules(BaseModel):
    include_in_redistribution: bool = False
    eligible_redistribution_shares: Optional[float] = None


class DisplayRules(BaseModel):
    show_in_holdings: bool = True


class TradeRules(BaseModel):
    model_config = ConfigDict(extra="allow")

    adjustments_enabled: bool = True
    min_shares_to_keep: float = 0.0
    max_shares_to_own: Optional[float] = None
    max_shares_to_trade: Optional[float] = None


class TickerScenarioInputs(BaseModel):
    model_config = ConfigDict(extra="allow")

    latest_quarter_revenue: Optional[float] = None
    latest_quarter_net_income: Optional[float] = None
    shares_outstanding: Optional[float] = None
    notes: str = ""
    redistribution_rules: RedistributionRules = Field(
        default_factory=RedistributionRules,
    )
    display_rules: DisplayRules = Field(default_factory=DisplayRules)
    trade_rules: Optional[TradeRules] = None
    bear: ScenarioAssumptions
    base: ScenarioAssumptions
    bull: ScenarioAssumptions


class PortfolioPosition(BaseModel):
    ticker: str
    shares: Optional[float] = None


class PortfolioConfig(BaseModel):
    portfolio: list[PortfolioPosition] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)


class ScenarioInputsResponse(BaseModel):
    scenarios: dict[str, TickerScenarioInputs]


class StockScenarioResponse(BaseModel):
    ticker: str
    scenario: TickerScenarioInputs


class PortfolioSummaryRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    price: Optional[float] = None
    shares: Optional[float] = None
    market_value: Optional[float] = None
    bear_price_y3: Optional[float] = None
    base_price_y3: Optional[float] = None
    bull_price_y3: Optional[float] = None
    bear_cagr_y3: Optional[float] = None
    base_cagr_y3: Optional[float] = None
    bull_cagr_y3: Optional[float] = None
    current_pe: Optional[float] = None
    blended_future_pe: Optional[float] = None
    weighted_cagr_y3: Optional[float] = None
    confidence: str = "Review Assumptions"
    action: str = "Needs Input"
    action_rank: int = 999
    bottom_pinned: bool = False
    show_in_holdings: bool = True
    include_in_redistribution: bool = False
    eligible_redistribution_shares: Optional[float] = None
    locked_shares: Optional[float] = None
    status: Optional[str] = None
    cache_source: Optional[str] = None


class ActionQueueRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    recommended_action: str
    reason: Optional[str] = None
    eligible_redistribution_shares: Optional[float] = None
    current_eligible_weight: Optional[float] = None
    target_weight_effective: Optional[float] = None
    dollar_trade: Optional[float] = None
    shares_trade: Optional[float] = None
    action: Optional[str] = None
    confidence: Optional[str] = None


class TickerControlUpdate(BaseModel):
    ticker: str
    show_in_holdings: Optional[bool] = None
    include_in_redistribution: Optional[bool] = None
    eligible_redistribution_shares: Optional[float] = None


class PortfolioControlsUpdate(BaseModel):
    updates: list[TickerControlUpdate]


class PortfolioViewResponse(BaseModel):
    portfolio: list[PortfolioSummaryRow] = Field(default_factory=list)
    watchlist: list[PortfolioSummaryRow] = Field(default_factory=list)
    action_queue: list[ActionQueueRow] = Field(default_factory=list)
    action_queue_summary: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    data_issues: list[dict] = Field(default_factory=list)
