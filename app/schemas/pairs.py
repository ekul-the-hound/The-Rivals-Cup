"""Request/response schemas for /pairs. Review state only; no order/trade fields exist."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.services.pairs.models import PairPacket, Reason


class CandidateSummary(BaseModel):
    pair_id: str | None
    name: str
    long_ticker: str | None
    short_ticker: str | None
    eligible: bool
    pair_quality_score: float
    data_quality_score: float
    event_blackout_status: str | None = None
    manual_decision: str | None = None
    factor: dict[str, str | None]
    ineligible_reasons: list[Reason]
    risk_flags: list[str]


class CandidatesResponse(BaseModel):
    week_start: date
    week_end: date
    note: str = "Research candidates only. Nothing here is a trade or an instruction."
    eligible: list[CandidateSummary]
    ineligible: list[CandidateSummary]


class PairPacketResponse(BaseModel):
    week_start: date
    eligible: bool
    ineligible_reasons: list[Reason]
    components: dict[str, dict[str, float]]
    penalties: dict[str, float]
    packet: PairPacket


class BuildRequest(BaseModel):
    week_start: date | None = None
    max_pairs: int = Field(6, ge=1, le=6)
    intended_leg_notional_usd: float | None = Field(None, gt=0)
    persist: bool = True


class WeeklyPortfolioResponse(BaseModel):
    week_start: date
    run_id: str
    built_at: str
    status: str
    selected: list[dict[str, Any]]
    alternates: list[dict[str, Any]]
    warnings: list[str]
    summary: dict[str, Any]
    note: str = "RESEARCH DRAFT ONLY. Not a trade list; you enter and record any trades manually."


class ApproveRequest(BaseModel):
    week_start: date | None = None


class ExcludeRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    week_start: date | None = None
