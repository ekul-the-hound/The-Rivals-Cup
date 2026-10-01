"""Response schemas for the read-only status endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import SystemMode
from app.models.tables import (
    DataQualityIssue,
    ManualPortfolio,
    ManualPosition,
    ManualTrade,
    ProviderRunLog,
    ScoreSnapshot,
)

MANUAL_DISCLAIMER = (
    "Records of trades the owner entered MANUALLY in Trader View. This system never places, "
    "modifies, or cancels orders and cannot confirm any trade was entered."
)
SCORE_DISCLAIMER = (
    "ESTIMATE ONLY. Not an official Wall Street Rivals score; scoring rules may differ."
)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str
    time_utc: datetime
    time_display: str
    display_timezone: str


class ControlsStatus(BaseModel):
    mode: SystemMode
    signal_sending_enabled: Literal[False] = False
    provider_ingestion_enabled: bool
    execution_code_present: Literal[False] = False
    state_source: Literal["database", "default"]
    reason: str | None = None
    updated_at: datetime | None = None
    prohibited_capabilities: list[str]


class ResearchStatus(BaseModel):
    mode: SystemMode
    counts: dict[str, int]
    pairs_by_status: dict[str, int]
    latest_packet_at: datetime | None
    latest_ranking_at: datetime | None
    recent_provider_runs: list[ProviderRunLog]
    notes: list[str]


class DataQualityResponse(BaseModel):
    open_issue_count: int
    by_severity: dict[str, int]
    issues: list[DataQualityIssue]


class ManualPortfolioView(BaseModel):
    portfolio: ManualPortfolio
    open_positions: list[ManualPosition]
    recent_trades: list[ManualTrade]
    cost_basis_gross_exposure_usd: float
    cost_basis_net_exposure_usd: float
    exposure_note: str


class ManualPortfolioResponse(BaseModel):
    disclaimer: str = MANUAL_DISCLAIMER
    portfolios: list[ManualPortfolioView]


class PortfolioScore(BaseModel):
    portfolio_id: str
    portfolio_name: str
    latest: ScoreSnapshot | None


class ScoreResponse(BaseModel):
    official: Literal[False] = False
    disclaimer: str = SCORE_DISCLAIMER
    scores: list[PortfolioScore]
