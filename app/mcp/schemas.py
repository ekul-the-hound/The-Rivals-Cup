"""Strict input schemas for the 12 tools (extra fields forbidden, bounded sizes)."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Ticker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$"
    ),
]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SectorName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=50)]
FormType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z0-9][A-Z0-9/\-]{0,9}$"),
]
Cursor = Annotated[str, StringConstraints(max_length=200)]
MAX_NOTIONAL = 1_000_000_000.0


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _AsOf(_In):
    as_of: datetime | None = Field(
        None, description="Optional point in time (UTC if no timezone). Not in the future."
    )

    @field_validator("as_of")
    @classmethod
    def _not_future(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        v = v if v.tzinfo else v.replace(tzinfo=UTC)
        if v > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("as_of must not be in the future")
        if v < datetime(2000, 1, 1, tzinfo=UTC):
            raise ValueError("as_of is too far in the past")
        return v


class NoArgs(_In):
    pass


class MarketDashboardIn(_AsOf):
    pass


class RankSectorEtfsIn(_AsOf):
    lookbacks: list[Annotated[int, Field(ge=1, le=120)]] = Field(
        [1, 5, 20], min_length=1, max_length=5
    )

    @field_validator("lookbacks")
    @classmethod
    def _unique(cls, v: list[int]) -> list[int]:
        return sorted(set(v))


class PeerPairCandidatesIn(_AsOf):
    sector: SectorName | None = None
    limit: int = Field(20, ge=1, le=50)
    cursor: Cursor | None = None


class PeerPairPacketIn(_AsOf):
    long_ticker: Ticker
    short_ticker: Ticker

    @model_validator(mode="after")
    def _differ(self) -> "PeerPairPacketIn":
        if self.long_ticker == self.short_ticker:
            raise ValueError("long_ticker and short_ticker must differ")
        return self


class StockPacketIn(_AsOf):
    ticker: Ticker


class SearchFilingsIn(_In):
    ticker: Ticker | None = None
    filing_types: list[FormType] = Field(..., min_length=1, max_length=10)
    start_date: date
    end_date: date
    query: Query | None = None
    limit: int = Field(20, ge=1, le=50)
    cursor: Cursor | None = None

    @model_validator(mode="after")
    def _range(self) -> "SearchFilingsIn":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("date range may not exceed 366 days")
        return self


class SearchNewsIn(_In):
    query: Query | None = None
    ticker: Ticker | None = None
    sector: SectorName | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = Field(30, ge=1, le=50)
    cursor: Cursor | None = None

    @model_validator(mode="after")
    def _range(self) -> "SearchNewsIn":
        for n in ("start_time", "end_time"):
            v = getattr(self, n)
            if v is not None and v.tzinfo is None:
                setattr(self, n, v.replace(tzinfo=UTC))
        if self.start_time and self.end_time:
            if self.end_time < self.start_time:
                raise ValueError("end_time must be on or after start_time")
            if (self.end_time - self.start_time).days > 90:
                raise ValueError("time range may not exceed 90 days")
        return self


class LiquidityCheckIn(_AsOf):
    ticker: Ticker
    intended_notional: float = Field(..., gt=0, le=MAX_NOTIONAL)


class PortfolioRiskIn(_In):
    pass


class BlackoutListIn(_In):
    week_start: date
    week_end: date

    @model_validator(mode="after")
    def _range(self) -> "BlackoutListIn":
        if self.week_end < self.week_start:
            raise ValueError("week_end must be on or after week_start")
        if (self.week_end - self.week_start).days > 13:
            raise ValueError("range may not exceed 14 days")
        return self


class ManualChecklistIn(_AsOf):
    long_ticker: Ticker
    short_ticker: Ticker
    intended_long_notional: float | None = Field(None, gt=0, le=MAX_NOTIONAL)
    intended_short_notional: float | None = Field(None, gt=0, le=MAX_NOTIONAL)

    @model_validator(mode="after")
    def _differ(self) -> "ManualChecklistIn":
        if self.long_ticker == self.short_ticker:
            raise ValueError("long_ticker and short_ticker must differ")
        return self
