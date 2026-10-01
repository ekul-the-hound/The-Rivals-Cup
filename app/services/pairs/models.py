"""Inputs and outputs of the PeerPairEngine."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

RESEARCH_NOTE = (
    "RESEARCH CANDIDATE ONLY. Not entered anywhere; you place and record any trade manually."
)


@dataclass
class LegInputs:
    security: dict[str, Any]
    company: dict[str, Any] = field(default_factory=dict)
    bars: list[dict[str, Any]] = field(default_factory=list)  # ascending by bar_date
    quote: dict[str, Any] | None = None


@dataclass
class PairInputs:
    pair: dict[str, Any]
    legs: list[LegInputs]  # exactly 2 for a valid mapping
    sector_etf: str | None
    sector_etf_bars: list[dict[str, Any]]
    spy_bars: list[dict[str, Any]]
    week_start: date
    week_end: date
    now: datetime
    blackout_rows: dict[str, dict[str, Any]] = field(default_factory=dict)  # by security_id
    override: dict[str, Any] | None = None
    catalysts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    filings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    news: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    macro: dict[str, Any] | None = None
    market_ctx: dict[str, Any] | None = None
    manual_decision: str | None = None  # INCLUDE | EXCLUDE | None

    @property
    def today(self) -> date:
        return self.now.date()


class Reason(BaseModel):
    code: str
    message: str


class EntryZone(BaseModel):
    ticker: str
    side: Literal["LONG", "SHORT"]
    reference_price: float | None
    previous_close: float | None
    band_low: float | None
    band_high: float | None
    basis: str


class PairPacket(BaseModel):
    pair_id: str | None
    name: str
    long_ticker: str | None
    short_ticker: str | None
    direction_rule: str
    relationship: dict[str, Any]
    pair_quality_score: float
    adjusted_score: float | None = None
    overlap_penalty: float | None = None
    overlap_reasons: list[str] = []
    rank: int | None = None
    top_reasons: list[str]
    counter_thesis: list[str]
    event_blackout: dict[str, Any]
    entry_zones: list[EntryZone]
    invalidation_concept: str
    target_concept: str
    suggested_max_gross_pct: float | None = None
    allocation_note: str = (
        "Suggested ceiling for both legs combined, as % of portfolio value. Not an order."
    )
    review_cadence: str
    manual_checklist: list[str]
    risk_flags: list[str]
    metrics: dict[str, Any]
    catalyst_context: dict[str, Any]
    news_summary: dict[str, Any]
    macro_sector_context: dict[str, Any]
    data_quality_score: float
    missing_or_stale: list[str]
    manual_decision: str | None = None
    status_note: str = RESEARCH_NOTE


class PairEvaluation(BaseModel):
    pair_id: str | None
    name: str
    long_ticker: str | None
    short_ticker: str | None
    long_security_id: str | None = None
    short_security_id: str | None = None
    eligible: bool
    ineligible_reasons: list[Reason]
    score: float
    components: dict[str, dict[str, float]]
    penalties: dict[str, float]
    data_quality_score: float
    factor: dict[str, str | None]
    packet: PairPacket
