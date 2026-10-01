"""Pydantic v2 row models, one per table (see supabase/migrations)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    CatalystType,
    DataStatus,
    Direction,
    EvidenceQuality,
    PairStatus,
    ReviewStatus,
    SystemMode,
)


class Row(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Profile(Row):
    email: str | None = None
    display_name: str | None = None
    is_owner: bool = False
    timezone: str = "America/Chicago"


class Security(Row):
    ticker: str
    name: str | None = None
    security_type: str = "EQUITY"
    exchange: str | None = None
    currency: str = "USD"
    sector: str | None = None
    industry: str | None = None
    is_etf: bool = False
    is_benchmark: bool = False
    is_active: bool = True
    wsr_eligibility: DataStatus = DataStatus.UNVERIFIED
    notes: str | None = None


class Company(Row):
    security_id: UUID
    cik: str | None = None
    legal_name: str | None = None
    description: str | None = None
    sic_code: str | None = None
    fiscal_year_end: str | None = None
    market_cap_usd: float | None = None
    shares_outstanding: float | None = None
    market_cap_as_of: date | None = None
    data_status: DataStatus = DataStatus.UNVERIFIED
    source: str | None = None


class SectorEtfMapping(Row):
    sector: str
    etf_security_id: UUID
    is_primary: bool = True
    notes: str | None = None


class PeerPair(Row):
    name: str
    sector: str | None = None
    rationale: str | None = None
    status: PairStatus = PairStatus.CANDIDATE
    created_by: str = "manual"
    notes: str | None = None


class PeerPairMember(Row):
    pair_id: UUID
    security_id: UUID
    role: str


class MarketBar(Row):
    security_id: UUID
    bar_date: date
    timeframe: str = "1D"
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adj_close: float | None = None
    volume: int | None = None
    vwap: float | None = None
    source: str
    data_status: DataStatus = DataStatus.UNVERIFIED
    retrieved_at: datetime | None = None


class DailyQuote(Row):
    security_id: UUID
    quote_ts: datetime
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    previous_close: float | None = None
    source: str
    data_status: DataStatus = DataStatus.UNVERIFIED
    retrieved_at: datetime | None = None


class LiquidityMetric(Row):
    security_id: UUID
    as_of_date: date
    adv_20d_shares: float | None = None
    adv_20d_usd: float | None = None
    avg_spread_bps: float | None = None
    est_max_position_usd: float | None = None
    method: str = "adv_pct_v1"
    data_status: DataStatus = DataStatus.UNVERIFIED
    evidence_quality: EvidenceQuality = EvidenceQuality.DERIVED


class MarketContextSnapshot(Row):
    snapshot_at: datetime
    spy_return_1d: float | None = None
    qqq_return_1d: float | None = None
    iwm_return_1d: float | None = None
    sector_returns: dict[str, Any] = {}
    breadth: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    source: str | None = None
    data_status: DataStatus = DataStatus.UNVERIFIED


class MacroContextSnapshot(Row):
    as_of_date: date
    rates: dict[str, Any] = {}
    upcoming_events: list[Any] = []
    summary: str | None = None
    source: str | None = None
    data_status: DataStatus = DataStatus.UNVERIFIED


class FilingDocument(Row):
    security_id: UUID
    form_type: str
    accession_number: str
    filed_at: datetime | None = None
    period_of_report: date | None = None
    url: str | None = None
    title: str | None = None
    summary: str | None = None
    evidence_quality: EvidenceQuality = EvidenceQuality.PRIMARY
    data_status: DataStatus = DataStatus.UNVERIFIED


class CorporateCatalyst(Row):
    security_id: UUID
    catalyst_type: CatalystType = CatalystType.OTHER
    headline: str
    detail: str | None = None
    event_date: date | None = None
    source_url: str | None = None
    filing_id: UUID | None = None
    evidence_quality: EvidenceQuality = EvidenceQuality.UNVERIFIED
    data_status: DataStatus = DataStatus.UNVERIFIED


class NewsItem(Row):
    security_id: UUID | None = None
    headline: str
    source_name: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    content_hash: str
    evidence_quality: EvidenceQuality = EvidenceQuality.SECONDARY
    data_status: DataStatus = DataStatus.UNVERIFIED


class ResearchPacket(Row):
    pair_id: UUID
    packet_version: int = 1
    as_of: datetime | None = None
    status: ReviewStatus = ReviewStatus.DRAFT
    content: dict[str, Any] = {}
    content_hash: str | None = None
    data_status: DataStatus = DataStatus.UNVERIFIED
    evidence_quality: EvidenceQuality = EvidenceQuality.DERIVED
    generated_by: str = "system"
    notes: str | None = None


class PairRanking(Row):
    run_id: UUID
    pair_id: UUID
    ranked_at: datetime | None = None
    rank: int
    score: float | None = None
    direction_hint: Direction = Direction.NO_TRADE
    correlation_60d: float | None = None
    spread_zscore: float | None = None
    liquidity_ok: bool | None = None
    components: dict[str, Any] = {}
    warnings: list[Any] = []
    data_status: DataStatus = DataStatus.UNVERIFIED


class PairResearchReview(Row):
    pair_id: UUID
    packet_id: UUID | None = None
    status: ReviewStatus = ReviewStatus.DRAFT
    direction: Direction = Direction.NO_TRADE
    thesis: str | None = None
    risks: str | None = None
    checklist: list[Any] = []
    claude_review_notes: str | None = None
    owner_notes: str | None = None
    reviewed_at: datetime | None = None


class ManualPortfolio(Row):
    name: str
    competition: str = "2026 Rival Cup"
    starting_cash_usd: float = 0
    is_active: bool = True
    notes: str | None = None


class ManualPosition(Row):
    portfolio_id: UUID
    pair_id: UUID | None = None
    security_id: UUID
    side: Direction
    quantity: float
    avg_entry_price: float | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    is_open: bool = True
    data_status: DataStatus = DataStatus.MANUAL
    notes: str | None = None


class ManualTrade(Row):
    portfolio_id: UUID
    position_id: UUID | None = None
    pair_id: UUID | None = None
    security_id: UUID
    side: Direction
    action: str
    quantity: float
    price: float
    fees_usd: float = 0
    traded_at: datetime
    recorded_at: datetime | None = None
    source: str = "MANUAL"
    notes: str | None = None


class PortfolioDailyValue(Row):
    portfolio_id: UUID
    value_date: date
    cash_usd: float | None = None
    long_mv_usd: float | None = None
    short_mv_usd: float | None = None
    net_liq_usd: float | None = None
    gross_exposure_usd: float | None = None
    net_exposure_usd: float | None = None
    daily_pnl_usd: float | None = None
    data_status: DataStatus = DataStatus.MANUAL


class ScoreSnapshot(Row):
    portfolio_id: UUID
    snapshot_at: datetime | None = None
    estimated_score: float | None = None
    components: dict[str, Any] = {}
    warnings: list[Any] = []
    methodology: str | None = None
    is_estimate: bool = True
    data_status: DataStatus = DataStatus.UNVERIFIED


class DataQualityIssue(Row):
    entity_table: str | None = None
    entity_id: UUID | None = None
    security_id: UUID | None = None
    severity: str = "WARNING"
    issue_type: str
    description: str | None = None
    data_status: DataStatus = DataStatus.UNVERIFIED
    detected_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_notes: str | None = None


class ProviderRunLog(Row):
    provider: str
    job_name: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "RUNNING"
    rows_read: int | None = None
    rows_written: int | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = {}


class McpAuditLog(Row):
    occurred_at: datetime | None = None
    request_id: str | None = None
    client_id: str | None = None
    tool_name: str
    args: dict[str, Any] = {}
    result_summary: dict[str, Any] = {}
    status: str = "OK"
    duration_ms: int | None = None
    error: str | None = None


class SystemControlState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = 1
    mode: SystemMode = SystemMode.PAUSED
    signal_sending_enabled: bool = False
    provider_ingestion_enabled: bool = False
    reason: str | None = None
    updated_at: datetime | None = None
