"""Leaders & laggards jobs: price history, competitor map, weekly candidate book.

Research data only: public price history, a competitor list and a ranked candidate book. Nothing here
contacts WSR, Trader View, a browser or a broker, and nothing records or places a trade.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.store import select_all
from app.services.ingestion.runner import JobContext, JobResult
from app.services.leaders import BENCHMARK_TICKERS
from app.services.leaders.book import (
    BookParams,
    build_book,
    fetch_for_tickers,
    prepare,
    tradable_names,
)
from app.services.leaders.metrics import series_to_row
from app.services.providers.base import ProviderError
from app.services.universe.master import UniverseWriteGuard, upsert_uniform
from app.services.universe.repo import load_universe
from app.services.validation.bars import validate_bars

MIN_BARS_STORED = 30
FLUSH_EVERY = 100
MAP_FRESH_DAYS = 14


def _age(v: Any) -> str:
    return "" if not v else str(v)


async def refresh_universe_price_history(ctx: JobContext) -> JobResult:
    """One year of daily bars (plus dividends and splits) for every tradable target-sector name and the
    market / sector benchmarks. Each ticker is one Yahoo request, refreshed whole so adjusted prices stay
    consistent. Resumable: run again until nothing is waiting."""
    yahoo = ctx.providers.require("yahoo")
    s = ctx.settings
    names = [r["ticker"] for r in tradable_names(load_universe(ctx.db))]
    wanted = list(dict.fromkeys([*BENCHMARK_TICKERS, *names]))
    have = {
        r["ticker"]: r.get("fetched_at")
        for r in select_all(ctx.db, "universe_price_history", columns="ticker,fetched_at")
    }
    cutoff = (ctx.now - timedelta(hours=s.leaders_history_fresh_hours)).isoformat()
    todo = [t for t in wanted if t not in have or _age(have[t]) < cutoff]
    todo.sort(key=lambda t: (t not in BENCHMARK_TICKERS, _age(have.get(t))))
    batch = todo[: s.leaders_history_batch]
    guard = UniverseWriteGuard(ctx.db)
    hist_rows: list[dict[str, Any]] = []
    master_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    written = fetched = 0

    def flush() -> int:
        n = upsert_uniform(ctx.db, "universe_price_history", hist_rows, "ticker", chunk=100)
        if master_rows:
            upsert_uniform(guard, "security_master", master_rows, "ticker")
        hist_rows.clear()
        master_rows.clear()
        return n

    for tk in batch:
        try:
            h = await yahoo.daily_history(tk, "1y", events=True)
        except ProviderError as exc:
            warnings.append(f"{tk}: {exc}")
            continue
        check = validate_bars([b.model_dump() for b in h.bars], today=ctx.today)
        if msg := check.summary(tk):
            warnings.append(msg)
        good = {c["bar_date"] for c in check.clean}
        usable = [b for b in h.bars if b.bar_date in good]
        bars = [(b.bar_date, b.close, b.adj_close, b.volume) for b in usable]
        if len(bars) < MIN_BARS_STORED:
            warnings.append(f"{tk}: only {len(bars)} usable bars")
            continue
        row = series_to_row(tk, bars, h.dividends, h.splits)
        row["fetched_at"] = ctx.now.isoformat()
        hist_rows.append(row)
        fetched += 1
        if tk not in BENCHMARK_TICKERS:
            # zero-volume days count as $0 traded: dropping them would overstate liquidity
            vb = [b for b in usable if b.volume is not None][-20:]
            if len(vb) >= 10:
                master_rows.append(
                    {
                        "ticker": tk,
                        "average_daily_volume_20d": round(sum(b.volume for b in vb) / len(vb), 2),
                        "average_dollar_volume_20d": round(
                            sum(b.close * b.volume for b in vb) / len(vb), 2
                        ),
                        "last_price": h.last_price or vb[-1].close,
                        "liquidity_data_as_of": vb[-1].bar_date,
                        "market_data_source": "yahoo_finance",
                    }
                )
        if len(hist_rows) >= FLUSH_EVERY:
            written += flush()
    written += flush()
    left = max(len(todo) - len(batch), 0)
    return JobResult(
        job="refresh_universe_price_history",
        rows_read=len(batch),
        rows_written=written,
        message=f"{fetched} histories downloaded, {left} waiting for the next run",
        warnings=warnings[:20],
    )


async def refresh_competitor_map(ctx: JobContext) -> JobResult:
    """Finnhub peer lists for the strongest names in each sector (the only stocks that could become the
    long candidate). Peers of other sectors are kept in the map but not used by the book."""
    fh = ctx.providers.require("finnhub")
    params = BookParams.from_settings(ctx.settings, ctx.today)
    prep = prepare(ctx.db, ctx.today, params)
    leaders = [t for s in prep.sectors.values() for t in s["prelim"]]
    if not leaders:
        return JobResult(
            job="refresh_competitor_map",
            status="SKIPPED",
            message="no ranked names yet: run refresh_universe_price_history first",
        )
    cut = (ctx.now - timedelta(days=MAP_FRESH_DAYS)).isoformat()
    fresh = {
        r["ticker"]
        for r in fetch_for_tickers(ctx.db, "competitor_map", leaders, chunk=50)
        if _age(r.get("fetched_at")) >= cut
    }
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for tk in leaders:
        if tk in fresh:
            continue
        try:
            peers = await fh.peers(tk)
        except ProviderError as exc:
            warnings.append(f"{tk}: {exc}")
            continue
        if not peers:
            warnings.append(f"{tk}: no Finnhub peers (SEC-industry fallback will be used)")
        rows += [
            {"ticker": tk, "peer": p, "source": "FINNHUB_PEERS", "fetched_at": ctx.now.isoformat()}
            for p in peers
        ]
    written = upsert_uniform(ctx.db, "competitor_map", rows, "ticker,peer")
    return JobResult(
        job="refresh_competitor_map",
        rows_read=len(leaders),
        rows_written=written,
        message=f"{len(leaders) - len(fresh)} leaders mapped, {len(fresh)} already fresh",
        warnings=warnings[:20],
    )


async def build_leader_laggard_book(ctx: JobContext) -> JobResult:
    params = BookParams.from_settings(ctx.settings, ctx.today)
    prep = prepare(ctx.db, ctx.today, params)
    if not any(s["population"] for s in prep.sectors.values()):
        return JobResult(
            job="build_leader_laggard_book",
            status="FAILED",
            message="no price history for any tradable name: run refresh_universe_price_history",
            warnings=[f"screened out: {prep.screened_out}"],
        )
    book = build_book(ctx.db, ctx.today, params, prep)
    ctx.db.upsert(
        "leader_laggard_books",
        [
            {
                "scoring_week_start": params.week_start.isoformat(),
                "generated_at": datetime.now(UTC).isoformat(),
                "params": book["params"],
                "book": book,
                "is_research_only": True,
            }
        ],
        "scoring_week_start",
    )
    n = len(book["pairs"])
    return JobResult(
        job="build_leader_laggard_book",
        rows_read=sum(s["population"] for s in prep.sectors.values()),
        rows_written=1,
        message=f"{n} long/short candidate pairs for the week of {params.week_start}",
        warnings=[f"{m['sector']}: {m['reason']}" for m in book["sectors_without_pair"]],
    )
