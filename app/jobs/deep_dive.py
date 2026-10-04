"""Deep-dive research data jobs (short interest, earnings calendar, analyst ratings, transcripts,
XBRL fundamentals, SEC filing feed, options IV). Public company data only; nothing here touches WSR,
Trader View, a broker or any order/trade state.

Market-wide jobs filter to the active target-sector names in security_master. Per-company jobs run
on a shortlist (DEEP_DIVE_TICKERS) so free-tier limits are respected.
"""

from datetime import date, timedelta
from typing import Any

from app.db.store import select_all
from app.models.universe import TARGET_SECTORS
from app.services.ingestion.runner import JobContext, JobResult
from app.services.providers.base import ProviderError
from app.services.providers.sec import summarize_company_facts
from app.services.universe.master import upsert_uniform
from app.services.universe.repo import rebuild_view_state
from app.services.universe.views import UniverseParams

FEED_FORMS = ("8-K", "10-Q", "10-K")


def _target_master(ctx: JobContext) -> dict[str, dict[str, Any]]:
    return {
        r["ticker"]: r
        for r in select_all(ctx.db, "security_master")
        if r.get("is_active", True) and r.get("sector") in TARGET_SECTORS
    }


def finalist_tickers(db: Any) -> list[str]:
    """Tickers in the latest leaders & laggards book: current picks first, then the alternates."""
    try:
        rows = db.select("leader_laggard_books", order="scoring_week_start", desc=True, limit=1)
    except Exception:  # table not migrated yet: no finalists
        return []
    if not rows:
        return []
    picks, alts = [], []
    for pr in (rows[0].get("book") or {}).get("pairs", []):
        picks += [pr["long"]["ticker"], pr["short"]["ticker"]]
        alts += [a["ticker"] for a in pr.get("alternates", {}).get("longs", [])]
        alts += [a["ticker"] for a in pr.get("alternates", {}).get("shorts", [])]
    return list(dict.fromkeys([*picks, *alts]))


def _shortlist(ctx: JobContext, job: str) -> tuple[list[str], JobResult | None]:
    tickers = list(dict.fromkeys([*finalist_tickers(ctx.db), *ctx.settings.deep_dive_list]))
    if not tickers:
        return [], JobResult(
            job=job,
            status="SKIPPED",
            message="set DEEP_DIVE_TICKERS or build the leaders & laggards book first",
        )
    return tickers, None


async def refresh_short_interest(ctx: JobContext) -> JobResult:
    finra = ctx.providers.require("finra")
    rows_in = await finra.latest_short_interest(ctx.today)
    keep = _target_master(ctx)
    rows = [
        {
            "ticker": r.ticker, "settlement_date": r.settlement_date,
            "short_interest_shares": r.short_interest_shares,
            "previous_short_interest_shares": r.previous_short_interest_shares,
            "change_percent": r.change_percent, "avg_daily_volume": r.avg_daily_volume,
            "days_to_cover": r.days_to_cover, "fetched_at": ctx.now,
        }
        for r in rows_in
        if r.ticker in keep
    ]  # fmt: skip
    written = upsert_uniform(ctx.db, "short_interest", rows, "ticker,settlement_date")
    warn = (
        []
        if rows
        else ["no FINRA rows matched target-sector tickers (run the universe jobs first)"]
    )
    when = rows_in[0].settlement_date if rows_in else None
    return JobResult(
        job="refresh_short_interest", rows_read=len(rows_in), rows_written=written,
        message=f"settlement {when}: {len(rows)} target-sector names", warnings=warn,
    )  # fmt: skip


async def refresh_short_sale_volume(ctx: JobContext) -> JobResult:
    finra = ctx.providers.require("finra")
    rows_in = await finra.latest_short_volume(ctx.today)
    keep = _target_master(ctx)
    agg: dict[tuple[str, Any], dict[str, Any]] = {}
    for r in rows_in:  # a symbol can appear on more than one line: add the volumes together
        if r.ticker not in keep:
            continue
        a = agg.setdefault(
            (r.ticker, r.trade_date),
            {"ticker": r.ticker, "trade_date": r.trade_date, "short_volume": 0.0,
             "short_exempt_volume": 0.0, "total_volume": 0.0, "fetched_at": ctx.now},
        )  # fmt: skip
        a["short_volume"] += r.short_volume or 0
        a["short_exempt_volume"] += r.short_exempt_volume or 0
        a["total_volume"] += r.total_volume or 0
    rows = list(agg.values())
    for a in rows:
        a["short_volume_ratio"] = (
            round(a["short_volume"] / a["total_volume"], 6) if a["total_volume"] else None
        )
    written = upsert_uniform(ctx.db, "short_sale_volume_daily", rows, "ticker,trade_date")
    warn = (
        []
        if rows
        else ["no FINRA rows matched target-sector tickers (run the universe jobs first)"]
    )
    return JobResult(
        job="refresh_short_sale_volume", rows_read=len(rows_in), rows_written=written, warnings=warn
    )


async def refresh_sec_filing_feed(ctx: JobContext) -> JobResult:
    sec = ctx.providers.require("sec")
    keep = _target_master(ctx)
    by_cik = {r["cik"]: t for t, r in keep.items() if r.get("cik")}
    rows, read, warnings = [], 0, []
    for form in FEED_FORMS:
        try:
            entries = await sec.current_filings_feed(form)
        except ProviderError as exc:
            warnings.append(f"{form} feed: {exc}")
            continue
        read += len(entries)
        for e in entries:
            tk = by_cik.get(e.cik or "")
            if tk:
                rows.append(
                    {
                        "accession_number": e.accession_number, "cik": e.cik, "ticker": tk,
                        "company_name": e.company_name, "form_type": e.form_type, "filed_at": e.filed_at,
                        "title": e.title, "link": e.link, "fetched_at": ctx.now,
                    }
                )  # fmt: skip
    written = upsert_uniform(ctx.db, "sec_filing_feed", rows, "accession_number")
    return JobResult(
        job="refresh_sec_filing_feed", rows_read=read, rows_written=written, warnings=warnings
    )


async def refresh_earnings_calendar(ctx: JobContext) -> JobResult:
    """Market-wide upcoming earnings (Alpha Vantage: one call; else Finnhub 21-day window)."""
    p = ctx.providers
    events, source, notes = [], "", []
    if p.alpha_vantage is not None:
        try:
            events, source = await p.alpha_vantage.earnings_calendar("3month"), "alpha_vantage"
        except ProviderError as exc:
            notes.append(f"alpha_vantage unavailable ({exc}); falling back to finnhub")
    if not events:
        fh = p.require("finnhub")
        events, source = (
            await fh.earnings_calendar(ctx.today, ctx.today + timedelta(days=21)),
            "finnhub",
        )
    keep = _target_master(ctx)
    rows = [
        {
            "ticker": e.ticker, "earnings_date": e.earnings_date, "time_of_day": e.time_of_day,
            "eps_estimate": e.eps_estimate, "revenue_estimate": e.revenue_estimate,
            "fiscal_period_end": e.fiscal_period_end, "source": source, "fetched_at": ctx.now,
        }
        for e in events
        if e.ticker in keep and e.earnings_date >= ctx.today
    ]  # fmt: skip
    written = upsert_uniform(ctx.db, "earnings_calendar", rows, "ticker,earnings_date")
    # feed the nearest upcoming date into security_master so the pair-research view can apply its
    # earnings-week blackout; a date you entered by hand is never overwritten.
    nxt: dict[str, date] = {}
    for r in rows:
        d = r["earnings_date"]
        if r["ticker"] not in nxt or d < nxt[r["ticker"]]:
            nxt[r["ticker"]] = d
    upd = []
    for tk, d in nxt.items():
        cur = keep[tk]
        existing = cur.get("earnings_date_if_known")
        if cur.get("manually_overridden") and existing and str(existing) >= ctx.today.isoformat():
            continue
        upd.append({"ticker": tk, "earnings_date_if_known": d})
    if upd:
        upsert_uniform(ctx.db, "security_master", upd, "ticker")
        rebuild_view_state(
            ctx.db,
            UniverseParams.from_settings(ctx.settings),
            ctx.today,
            ctx.now,
            [u["ticker"] for u in upd],
        )
    warn = notes + ([] if rows else ["no upcoming earnings matched target-sector tickers"])
    return JobResult(
        job="refresh_earnings_calendar", rows_read=len(events), rows_written=written,
        message=f"{source}: {len(rows)} upcoming events, {len(upd)} dates fed to security_master", warnings=warn,
    )  # fmt: skip


async def refresh_analyst_ratings(ctx: JobContext) -> JobResult:
    tickers, skip = _shortlist(ctx, "refresh_analyst_ratings")
    if skip:
        return skip
    fh = ctx.providers.require("finnhub")
    rows, warnings = [], []
    for tk in tickers:
        try:
            recs = await fh.recommendations(tk)
        except ProviderError as exc:
            warnings.append(f"{tk}: {exc}")
            continue
        for r in sorted(recs, key=lambda x: x.period, reverse=True)[:4]:
            rows.append(
                {
                    "ticker": tk, "period": r.period, "strong_buy": r.strong_buy, "buy": r.buy,
                    "hold": r.hold, "sell": r.sell, "strong_sell": r.strong_sell, "fetched_at": ctx.now,
                }
            )  # fmt: skip
    written = upsert_uniform(ctx.db, "analyst_recommendations", rows, "ticker,period")
    return JobResult(
        job="refresh_analyst_ratings",
        rows_read=len(tickers),
        rows_written=written,
        warnings=warnings,
    )


def last_completed_quarters(today: date, n: int = 2) -> list[str]:
    q = (today.month - 1) // 3  # 0..3: quarters completed this year
    y = today.year
    out = []
    for _ in range(n):
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}Q{q}")
        q -= 1
    return out


async def refresh_earnings_transcripts(ctx: JobContext) -> JobResult:
    tickers, skip = _shortlist(ctx, "refresh_earnings_transcripts")
    if skip:
        return skip
    av = ctx.providers.require("alpha_vantage")
    stored = select_all(ctx.db, "earnings_transcripts")
    have = {(r["ticker"], r["fiscal_quarter"]) for r in stored}
    fresh_cut = (ctx.now - timedelta(days=30)).isoformat()
    # a ticker with a transcript fetched in the last 30 days is skipped entirely, so an unreported
    # newest quarter does not burn a call per run; older ones get re-checked for the new quarter.
    recent = {r["ticker"] for r in stored if str(r.get("fetched_at") or "") >= fresh_cut}
    budget = ctx.settings.alpha_vantage_daily_budget
    rows, warnings, calls = [], [], 0
    for tk in tickers:
        if tk in recent:
            continue
        for q in last_completed_quarters(ctx.today):
            if (tk, q) in have:
                break  # already stored the newest quarter we can get
            if calls >= budget:
                warnings.append(
                    f"daily budget of {budget} Alpha Vantage calls reached; rerun tomorrow"
                )
                break
            calls += 1
            try:
                tr = await av.transcript(tk, q)
            except ProviderError as exc:
                warnings.append(f"{tk} {q}: {exc}")
                calls = budget  # rate-limit notice: stop for the day
                break
            if tr:
                rows.append(
                    {
                        "ticker": tk, "fiscal_quarter": q, "excerpt": tr.excerpt,
                        "segment_count": tr.segment_count, "fetched_at": ctx.now,
                    }
                )  # fmt: skip
                break
    written = upsert_uniform(ctx.db, "earnings_transcripts", rows, "ticker,fiscal_quarter")
    return JobResult(
        job="refresh_earnings_transcripts", rows_read=calls, rows_written=written,
        message=f"{calls} API calls used of {budget}", warnings=warnings,
    )  # fmt: skip


async def refresh_company_fundamentals(ctx: JobContext) -> JobResult:
    tickers, skip = _shortlist(ctx, "refresh_company_fundamentals")
    if skip:
        return skip
    sec = ctx.providers.require("sec")
    master = {r["ticker"]: r for r in select_all(ctx.db, "security_master")}
    rows, warnings = [], []
    for tk in tickers:
        cik = (master.get(tk) or {}).get("cik")
        if not cik:
            warnings.append(f"{tk}: no SEC CIK in security_master")
            continue
        try:
            s = summarize_company_facts(await sec.company_facts(int(cik)))
        except ProviderError as exc:
            warnings.append(f"{tk}: {exc}")
            continue
        rows.append(
            {
                "ticker": tk, "cik": str(cik).zfill(10), "period_end": s.get("period_end"),
                "fiscal_period": s.get("fiscal_period"), "revenue": s.get("revenue"),
                "net_income": s.get("net_income"), "operating_income": s.get("operating_income"),
                "total_assets": s.get("total_assets"), "total_liabilities": s.get("total_liabilities"),
                "stockholders_equity": s.get("stockholders_equity"), "eps_diluted": s.get("eps_diluted"),
                "shares_outstanding": s.get("shares_outstanding"),
                "prior_year_revenue": s.get("prior_year_revenue"),
                "revenue_growth_pct": s.get("revenue_growth_pct"), "metrics": s["metrics"],
                "fetched_at": ctx.now,
            }
        )  # fmt: skip
    written = upsert_uniform(ctx.db, "company_fundamentals", rows, "ticker")
    return JobResult(
        job="refresh_company_fundamentals",
        rows_read=len(tickers),
        rows_written=written,
        warnings=warnings,
    )


async def refresh_options_iv(ctx: JobContext) -> JobResult:
    if not ctx.settings.options_iv_enabled:
        return JobResult(
            job="refresh_options_iv", status="SKIPPED",
            message="OPTIONS_IV_ENABLED is false (Yahoo options is unofficial, best effort)",
        )  # fmt: skip
    tickers, skip = _shortlist(ctx, "refresh_options_iv")
    if skip:
        return skip
    yahoo = ctx.providers.require("yahoo")
    rows, warnings = [], []
    for tk in tickers:
        snap = await yahoo.options_snapshot(tk)
        if snap is None:
            warnings.append(f"{tk}: options data unavailable (Yahoo refused or no listed options)")
            continue
        rows.append(
            {
                "ticker": tk,
                "as_of": ctx.today,
                "source": "yahoo_options",
                "fetched_at": ctx.now,
                **snap,
            }
        )
    written = upsert_uniform(ctx.db, "options_iv_snapshots", rows, "ticker,as_of")
    return JobResult(
        job="refresh_options_iv", rows_read=len(tickers), rows_written=written, warnings=warnings
    )
