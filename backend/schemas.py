"""Request body models for the API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PortfolioSharesUpdate(BaseModel):
    ticker: str
    shares: float


class PortfolioTickerUpsert(BaseModel):
    ticker: str
    shares: float


class WatchlistTickerUpsert(BaseModel):
    ticker: str


class ArchiveTickerRequest(BaseModel):
    ticker: str


class RestoreTickerRequest(BaseModel):
    ticker: str
    list: str


class TransactionCreate(BaseModel):
    date: str
    type: str
    shares: float
    price_per_share: float | None = None
    fees: float | None = 0.0
    account: str | None = ""
    notes: str | None = ""


class TransactionUpdate(BaseModel):
    date: str
    type: str
    shares: float
    price_per_share: float | None = None
    fees: float | None = 0.0
    account: str | None = ""
    notes: str | None = ""


class ImportedPriceRow(BaseModel):
    ticker: str
    date: str
    close: float
    source: str | None = "googlefinance"
    fetched_at: str | None = None


class PriceImportRequest(BaseModel):
    rows: list[ImportedPriceRow]


class TransactionImportFile(BaseModel):
    name: str
    content: str


class TransactionImportRequest(BaseModel):
    files: list[TransactionImportFile]
    account_mappings: dict[str, str] = Field(default_factory=dict)
    commit: bool = False


class ManagementModeUpdate(BaseModel):
    account: str
    ticker: str | None = None
    mode: str


class ManagementModesUpdate(BaseModel):
    updates: list[ManagementModeUpdate]


class ManualAllocationUpdate(BaseModel):
    ticker: str
    account: str
    shares: float
    mode: str = "track"


class AccountMergeRequest(BaseModel):
    from_account: str
    to_account: str
    source_account_keys: list[str] = Field(default_factory=list)


class FundamentalsRefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)


class EarningsCalendarRefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    days_ahead: int = 180
