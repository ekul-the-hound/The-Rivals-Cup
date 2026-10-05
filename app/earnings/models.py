"""Plain data shapes for the earnings scan (JSON-serialisable via model_dump(mode="json"))."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class Signal(BaseModel):
    name: str
    label: str
    available: bool
    value: float | None = None  # -1 (bearish) .. +1 (bullish)
    weight: float = 0.0  # log-odds per unit of value
    contribution: float = 0.0  # weight * value, in log-odds
    detail: str = ""
    source: str = ""


class McResult(BaseModel):
    n_paths: int
    p_up: float
    p_gain_5: float
    p_loss_5: float
    p_loss_10: float
    mean: float
    median: float
    p05: float
    p25: float
    p75: float
    p95: float
    cvar5: float  # mean of the worst 5% of paths
    expected_move: float  # assumed mean absolute move used to scale the paths
    edge: float  # mean / expected_move
    hist_edges: list[float]
    hist_counts: list[int]


class Headline(BaseModel):
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    url: str | None = None
    score: int = 0  # +1 positive, -1 negative, 0 neutral/no keyword hit


class InsiderSummary(BaseModel):
    filings_seen: int = 0
    buyers: int = 0
    sellers: int = 0
    buy_value_usd: float = 0.0
    sell_value_usd: float = 0.0
    open_market_buys: int = 0
    open_market_sells: int = 0
    note: str = ""


class PoliticianTrade(BaseModel):
    politician: str
    chamber: str | None = None
    side: str  # buy | sell
    amount_mid_usd: float | None = None
    transaction_date: date | None = None
    disclosure_date: date | None = None


class InstitutionHolding(BaseModel):
    institution: str
    shares_latest: float
    shares_prior: float | None = None
    change_pct: float | None = None
    period_latest: date | None = None


class HistoryStats(BaseModel):
    events_used: int = 0
    beat_rate: float | None = None
    avg_surprise_pct: float | None = None
    reactions: int = 0
    p_up_hist: float | None = None
    mean_abs_move: float | None = None
    up_mag: float | None = None
    down_mag: float | None = None
    last_moves: list[float] = Field(default_factory=list)


class FilingEvent(BaseModel):
    form: str
    filed: date
    items: list[str] = Field(default_factory=list)
    note: str = ""


class ShortInterestInfo(BaseModel):
    settlement_date: date
    shares: float | None = None
    change_pct: float | None = None  # percent change vs the prior report
    days_to_cover: float | None = None


class ConsensusInfo(BaseModel):
    finnhub_eps: float | None = None
    nasdaq_eps: float | None = None
    n_estimates: int | None = None
    last_year_eps: float | None = None
    disagreement_pct: float | None = None


class Suggestion(BaseModel):
    kind: str  # data | risk | model
    text: str


class TickerReport(BaseModel):
    ticker: str
    name: str | None = None
    report_date: date
    week: str  # this | next
    session: str = "unknown"  # bmo (before open) | amc (after close) | dmh | unknown
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    price: float | None = None
    adv_usd: float | None = None
    p_beat: float | None = None
    p_up: float
    p_down: float
    confidence: float
    expected_move: float
    move_source: str
    base_p_up: float
    log_odds: float
    signals: list[Signal] = Field(default_factory=list)
    mc: McResult | None = None
    history: HistoryStats = Field(default_factory=HistoryStats)
    headlines: list[Headline] = Field(default_factory=list)
    insiders: InsiderSummary = Field(default_factory=InsiderSummary)
    politicians: list[PoliticianTrade] = Field(default_factory=list)
    institutions: list[InstitutionHolding] = Field(default_factory=list)
    filings: list[FilingEvent] = Field(default_factory=list)
    short_interest: ShortInterestInfo | None = None
    consensus: ConsensusInfo | None = None
    suggestions: list[Suggestion] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    long_candidate: bool = False
    short_candidate: bool = False
    rank_score: float = 0.0


class SourceStatus(BaseModel):
    name: str
    configured: bool
    ok: int = 0
    failed: int = 0
    note: str = ""


class Scan(BaseModel):
    model_version: str
    generated_at: datetime
    today: date
    mock: bool = False
    weeks: dict[str, dict[str, str]]
    calendar_counts: dict[str, int]
    analysed: int
    reports: list[TickerReport]
    skipped: list[str] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    global_suggestions: list[Suggestion] = Field(default_factory=list)
    disclaimer: str
