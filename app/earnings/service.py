"""Orchestrates one earnings scan: calendar -> evidence per company -> probability -> Monte Carlo.

Read-only research. All network access goes through the shared rate-limited providers; a failing
source is recorded in `Scan.sources` and `Scan.warnings` and the scan carries on without it.
"""

import asyncio
import json
import math
from collections import defaultdict
from collections.abc import Awaitable
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from app.config import Settings
from app.config.clock import utcnow
from app.earnings import DISCLAIMER, MODEL_VERSION, model
from app.earnings.extra import (
    build_consensus,
    consensus_flag,
    finnhub_company_news,
    merge_headlines,
    nasdaq_consensus,
    sec_8k_events,
    short_interest_flag,
    short_interest_map,
)
from app.earnings.models import InsiderSummary, Scan, SourceStatus, TickerReport
from app.earnings.montecarlo import seed_for, simulate
from app.earnings.sources import (
    InstitutionalFeed,
    PoliticianFeed,
    analyst_scores,
    finnhub_congress,
    finnhub_history,
    history_stats,
    insider_summary,
    reaction_for,
    to_headlines,
)
from app.earnings.suggestions import global_suggestions, report_suggestions
from app.earnings.weeks import week_windows, window_for
from app.services.providers.base import FileCache, HttpClient
from app.services.providers.finnhub import EarningsEvent
from app.services.providers.registry import Providers
from app.services.universe.sp500 import fetch_constituents

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "generated" / "earnings"
SNAPSHOT = OUT_DIR / "latest.json"
SOURCES = (
    "finnhub",
    "alpha_vantage",
    "sec_edgar",
    "yahoo",
    "google_news",
    "politicians",
    "13f",
    "options",
    "finra",
    "nasdaq",
)


class _Tally:
    def __init__(self) -> None:
        self.ok: dict[str, int] = defaultdict(int)
        self.fail: dict[str, int] = defaultdict(int)
        self.err: dict[str, str] = {}

    async def run(self, source: str, aw: Awaitable[Any]) -> Any:
        try:
            out = await aw
        except Exception as exc:  # one broken feed must not sink the scan
            self.fail[source] += 1
            self.err[source] = f"{type(exc).__name__}: {str(exc)[:140]}"
            return None
        self.ok[source] += 1
        return out


def _ret(closes: list[float], n: int) -> float | None:
    return closes[-1] / closes[-1 - n] - 1 if len(closes) > n else None


def _news_query(ticker: str, info: Any) -> str:
    """Company name beats a bare ticker: 'T' or 'F' would match unrelated stories."""
    return f"{info.title} ({ticker}) earnings" if info else f"{ticker} stock earnings"


def local_today(settings: Settings) -> date:
    """Today in the display timezone, so Friday evening in Chicago is still Friday."""
    return utcnow().astimezone(ZoneInfo(settings.display_timezone)).date()


def _implied_move(snapshot: dict | None, today: date) -> float | None:
    """Rough one-event move from the nearest-expiry ATM implied vol: 0.8 * IV * sqrt(days/365)."""
    if not snapshot or not snapshot.get("atm_implied_vol") or not snapshot.get("expiry"):
        return None
    dte = (snapshot["expiry"] - today).days
    if not 0 < dte <= 35:
        return None
    return float(min(0.4, 0.8 * snapshot["atm_implied_vol"] * math.sqrt(dte / 365)))


async def fetch_calendar(
    providers: Providers, start: date, end: date, tally: _Tally
) -> dict[tuple[str, date], EarningsEvent]:
    events: dict[tuple[str, date], EarningsEvent] = {}
    if providers.alpha_vantage:
        rows = await tally.run("alpha_vantage", providers.alpha_vantage.earnings_calendar("3month"))
        for e in rows or []:
            if start <= e.earnings_date <= end:
                events[(e.ticker, e.earnings_date)] = e
    if providers.finnhub:  # Finnhub knows before/after-market timing, so it overrides
        rows = await tally.run("finnhub", providers.finnhub.earnings_calendar(start, end))
        for e in rows or []:
            events[(e.ticker, e.earnings_date)] = e
    return events


async def select_universe(
    settings: Settings,
    providers: Providers,
    events: dict[tuple[str, date], EarningsEvent],
    tickers: list[str] | None,
    max_tickers: int,
    everything: bool,
    warnings: list[str],
) -> tuple[list[tuple[str, date]], list[str]]:
    keys = sorted(events, key=lambda k: (-(events[k].revenue_estimate or 0), k))
    skipped: list[str] = []
    if tickers:
        want = {t.upper().replace(".", "-") for t in tickers}
        found = [k for k in keys if k[0] in want]
        skipped += [
            f"{t}: not on the calendar for these two weeks"
            for t in sorted(want - {k[0] for k in found})
        ]
        return found[:max_tickers], skipped
    watch = set(settings.earnings_watchlist_list)
    pool = keys
    if not everything:
        members: set[str] = set()
        if providers.wiki:
            try:
                members = {c.ticker for c in await fetch_constituents(providers.wiki)}
            except Exception as exc:
                warnings.append(
                    f"S&P 500 list unavailable ({exc}); ranking by revenue estimate instead"
                )
        if members:
            pool = [k for k in keys if k[0] in members or k[0] in watch]
    chosen = [k for k in pool if k[0] in watch] + [k for k in pool if k[0] not in watch]
    if len(chosen) > max_tickers:
        skipped.append(
            f"{len(chosen) - max_tickers} smaller reports skipped (EARNINGS_MAX_TICKERS={max_tickers})"
        )
    return chosen[:max_tickers], skipped


async def _no_sleep(_seconds: float) -> None:
    """Used only with an injected (offline) transport so tests do not wait on rate limits."""


class _Context:
    def __init__(
        self,
        settings: Settings,
        providers: Providers,
        today: date,
        tally: _Tally,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self.s, self.p, self.today, self.t = settings, providers, today, tally
        self.cik_map: dict[str, Any] = {}
        self.spy_closes: list[float] = []
        cache = None if transport else settings.provider_cache_dir
        self.pol_http = (
            HttpClient(
                "politicians",
                user_agent=settings.effective_web_user_agent,
                per_second=1.0,
                timeout=settings.http_timeout_seconds,
                cache=FileCache(cache),
                transport=transport,
            )
            if settings.politician_trades_url
            else None
        )
        self.pol = PoliticianFeed(self.pol_http, settings.politician_trades_url)
        self.inst = InstitutionalFeed(
            providers.sec, settings.earnings_13f_cik_list, settings.earnings_13f_max_filings
        )
        self.nasdaq_http = (
            HttpClient(
                "nasdaq",
                user_agent=settings.effective_web_user_agent,
                per_second=1.0,
                **({"sleep": _no_sleep} if transport else {}),
                timeout=settings.http_timeout_seconds,
                cache=FileCache(cache),
                transport=transport,
            )
            if settings.earnings_nasdaq_enabled
            else None
        )
        self.nasdaq: dict[date, dict[str, dict[str, Any]]] = {}
        self.short_interest: dict[str, Any] = {}
        self.bars_cache: dict[str, tuple[list[date], list[float], list[int]]] = {}

    async def bars(
        self, ticker: str, rng: str = "2y"
    ) -> tuple[list[date], list[float], list[int]] | None:
        key = f"{ticker}|{rng}"
        if key in self.bars_cache:
            return self.bars_cache[key]
        if not self.p.yahoo:
            return None
        h = await self.t.run("yahoo", self.p.yahoo.daily_history(ticker, rng))
        if not h or len(h.bars) < 25:
            return None
        out = (
            [b.bar_date for b in h.bars],
            [b.close for b in h.bars],
            [b.volume or 0 for b in h.bars],
        )
        self.bars_cache[key] = out
        return out

    async def load_consensus(self, days: list[date]) -> None:
        if not self.nasdaq_http:
            return
        for d in sorted(set(days)):
            res = await self.t.run("nasdaq", nasdaq_consensus(self.nasdaq_http, d))
            if res is not None:
                self.nasdaq[d] = res

    async def prepare(self) -> None:
        if self.p.finra:
            si = await self.t.run("finra", short_interest_map(self.p.finra, self.today))
            self.short_interest = si or {}
        if self.p.sec:
            m = await self.t.run("sec_edgar", self.p.sec.ticker_cik_map())
            self.cik_map = m or {}
        spy = await self.bars("SPY", "1y")
        self.spy_closes = spy[1] if spy else []
        await self.pol.load()
        if self.pol.error:
            self.t.fail["politicians"] += 1
            self.t.err["politicians"] = self.pol.error
        elif self.pol.configured:
            self.t.ok["politicians"] += 1
        await self.inst.load(self.today)
        for e in self.inst.errors:
            self.t.fail["13f"] += 1
            self.t.err["13f"] = e
        if self.inst.configured and not self.inst.errors:
            self.t.ok["13f"] += 1


async def _evidence(ctx: _Context, ev_row: EarningsEvent) -> tuple[model.Evidence, dict[str, Any]]:
    t, today, s, p = ev_row.ticker, ctx.today, ctx.s, ctx.p
    fh_key = s.finnhub_api_key.get_secret_value()
    info = ctx.cik_map.get(t)

    async def none() -> None:
        return None

    bars_t = ctx.bars(t)
    hist_t = (
        ctx.t.run("finnhub", finnhub_history(p.finnhub.http, fh_key, t, today))
        if p.finnhub
        else none()
    )
    recs_t = ctx.t.run("finnhub", p.finnhub.recommendations(t)) if p.finnhub else none()
    news_t = (
        ctx.t.run("google_news", p.news.search(_news_query(t, info), days=7)) if p.news else none()
    )
    ins_t = (
        ctx.t.run(
            "sec_edgar", insider_summary(p.sec, info.cik, today, s.earnings_insider_lookback_days)
        )
        if p.sec and info
        else none()
    )
    opt_t = (
        ctx.t.run("options", p.yahoo.options_snapshot(t))
        if p.yahoo and s.options_iv_enabled
        else none()
    )
    cong_t = (
        ctx.t.run("finnhub", finnhub_congress(p.finnhub.http, fh_key, t, today))
        if p.finnhub and s.finnhub_congress_enabled
        else none()
    )
    fnews_t = (
        ctx.t.run("finnhub", finnhub_company_news(p.finnhub.http, fh_key, t, today))
        if p.finnhub and s.earnings_finnhub_news_enabled
        else none()
    )
    k8_t = (
        ctx.t.run("sec_edgar", sec_8k_events(p.sec, info.cik, today, s.earnings_8k_lookback_days))
        if p.sec and info
        else none()
    )
    bars, events, recs, news, ins, opt, cong, fnews, k8 = await asyncio.gather(
        bars_t, hist_t, recs_t, news_t, ins_t, opt_t, cong_t, fnews_t, k8_t
    )

    ev = model.Evidence()
    extra: dict[str, Any] = {"bars": bars}
    if bars:
        dates, closes, vols = bars
        ev.history = history_stats(events or [], dates, closes)
        r20, s20 = _ret(closes, 20), _ret(ctx.spy_closes, 20)
        ev.excess_20d = r20 - s20 if r20 is not None and s20 is not None else r20
        rets = np.diff(np.log(np.array(closes[-61:])))
        extra["daily_sigma"] = float(rets.std()) if len(rets) > 10 else None
        extra["price"] = closes[-1]
        extra["adv"] = float(np.mean(np.array(closes[-20:]) * np.array(vols[-20:])))
    elif events:
        ev.history = history_stats(events, [], [])
    if recs:
        ev.analyst_level, ev.analyst_trend, ev.analyst_detail = analyst_scores(recs)
    if news is not None or fnews is not None:
        extra["headlines"] = merge_headlines(to_headlines(news or []), fnews or [])
        ev.headlines_scores = [h.score for h in extra["headlines"]]
    ev.filing_events = k8
    extra["filings"] = k8 or []
    ev.insiders = ins
    pol = None
    if ctx.pol.configured and not ctx.pol.error:
        pol = ctx.pol.for_ticker(t, today)
    if cong:
        pol = (pol or []) + cong
    ev.politicians = pol
    if ctx.inst.configured and info:
        ev.institutions = ctx.inst.holdings(info.title)
    if opt:
        ev.put_call_ratio = opt.get("put_call_volume_ratio")
        extra["implied"] = _implied_move(opt, today)
    extra["name"] = info.title if info else None
    return ev, extra


def _finish(
    ctx: _Context, row: EarningsEvent, week: str, ev: model.Evidence, extra: dict[str, Any]
) -> TickerReport:
    s = ctx.s
    signals = model.build_signals(ev)
    p_up, conf, lo = model.score(signals, ev.history, s.earnings_base_p_up)
    move, src = model.choose_expected_move(
        ev.history, extra.get("implied"), extra.get("daily_sigma")
    )
    mc = simulate(
        p_up,
        move,
        conf,
        s.earnings_mc_paths,
        seed_for(row.ticker, row.earnings_date),
        ev.history.up_mag,
        ev.history.down_mag,
    )
    adv = extra.get("adv")
    flags: list[str] = []
    if ev.excess_20d is not None and ev.excess_20d > 0.15:
        flags.append(f"Priced in? stock is {ev.excess_20d * 100:+.0f}% vs SPY over 20 days")
    if adv is None:
        flags.append("liquidity unknown (no price history)")
    elif adv < s.earnings_min_adv_usd:
        flags.append(f"thin liquidity: ${adv / 1e6:.1f}M average daily dollar volume")
    if not ev.history.events_used:
        flags.append("no earnings history")
    nas = ctx.nasdaq.get(row.earnings_date, {}).get(row.ticker)
    consensus = build_consensus(row.eps_estimate, nas)
    si = ctx.short_interest.get(row.ticker)
    for f in (consensus_flag(consensus), short_interest_flag(si)):
        if f:
            flags.append(f)
    for e in extra.get("filings", []):
        if e.note:
            flags.append(f"8-K {e.filed}: {e.note}")
    session = row.time_of_day if row.time_of_day in ("bmo", "amc", "dmh") else None
    session = session or (nas or {}).get("session") or "unknown"
    avail = sum(x.available for x in signals)
    liquid = adv is not None and adv >= s.earnings_min_adv_usd
    solid = conf >= s.earnings_long_min_confidence and avail >= 4 and liquid
    r = TickerReport(
        ticker=row.ticker,
        name=extra.get("name"),
        report_date=row.earnings_date,
        week=week,
        session=session,
        eps_estimate=row.eps_estimate if row.eps_estimate is not None else (nas or {}).get("eps"),
        revenue_estimate=row.revenue_estimate,
        price=extra.get("price"),
        adv_usd=adv,
        p_beat=model.p_beat(signals),
        p_up=p_up,
        p_down=round(1 - p_up, 4),
        confidence=conf,
        expected_move=move,
        move_source=src,
        base_p_up=s.earnings_base_p_up,
        log_odds=lo,
        signals=signals,
        mc=mc,
        history=ev.history,
        headlines=extra.get("headlines", []),
        insiders=ev.insiders or InsiderSummary(),
        politicians=ev.politicians or [],
        institutions=ev.institutions,
        flags=flags,
        filings=extra.get("filings", []),
        short_interest=si,
        consensus=consensus,
        long_candidate=bool(solid and p_up >= s.earnings_long_min_p_up and mc.mean > 0),
        short_candidate=bool(solid and p_up <= 1 - s.earnings_long_min_p_up and mc.mean < 0),
        rank_score=round(100 * mc.mean * conf, 3),
    )
    r.suggestions = report_suggestions(r)
    return r


async def _peer_moves(ctx: _Context, ticker: str) -> list[float]:
    fh = ctx.p.finnhub
    if not fh or not ctx.p.yahoo:
        return []
    peers = await ctx.t.run("finnhub", fh.peers(ticker)) or []
    moves: list[float] = []
    for peer in peers[:6]:
        evs = await ctx.t.run(
            "finnhub",
            fh.earnings_calendar(
                ctx.today - timedelta(days=14), ctx.today - timedelta(days=1), symbol=peer
            ),
        )
        if not evs:
            continue
        bars = await ctx.bars(peer, "6mo")
        if not bars:
            continue
        for e in evs:
            m = reaction_for(e.earnings_date, e.time_of_day or "", bars[0], bars[1])
            if m is not None:
                moves.append(m)
    return moves


async def run_scan(
    settings: Settings,
    providers: Providers,
    today: date,
    *,
    tickers: list[str] | None = None,
    max_tickers: int | None = None,
    deep: bool = True,
    everything: bool = False,
    mock: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    logged_predictions: int = 0,
    scored_predictions: int = 0,
) -> Scan:
    tally, warnings = _Tally(), []
    this, nxt = week_windows(today)
    events = await fetch_calendar(providers, this.start, nxt.end, tally)
    counts = {
        "this": sum(1 for k in events if this.contains(k[1])),
        "next": sum(1 for k in events if nxt.contains(k[1])),
    }
    if not events:
        warnings.append(
            "No earnings calendar available. Add a free FINNHUB_API_KEY (finnhub.io/register) "
            "or ALPHA_VANTAGE_API_KEY, then run `python -m scripts.earnings_scan --live-check`."
        )
    chosen, skipped = await select_universe(
        settings,
        providers,
        events,
        tickers,
        max_tickers or settings.earnings_max_tickers,
        everything or mock,
        warnings,
    )
    ctx = _Context(settings, providers, today, tally, transport)
    await ctx.prepare()
    await ctx.load_consensus([k[1] for k in chosen])
    sem = asyncio.Semaphore(4)
    evidence: dict[str, tuple[model.Evidence, dict[str, Any]]] = {}

    async def one(key: tuple[str, date]) -> TickerReport:
        async with sem:
            row = events[key]
            ev, extra = await _evidence(ctx, row)
            evidence[row.ticker] = (ev, extra)
            return _finish(ctx, row, window_for(row.earnings_date, today) or "this", ev, extra)

    reports = list(await asyncio.gather(*(one(k) for k in chosen)))

    if deep and settings.earnings_deep_top_n > 0 and providers.finnhub:
        top = sorted(
            (r for r in reports if r.confidence >= 0.3), key=lambda r: r.p_up, reverse=True
        )[: settings.earnings_deep_top_n]
        for r in top:
            ev, extra = evidence[r.ticker]
            ev.peer_moves = await _peer_moves(ctx, r.ticker)
            if len(ev.peer_moves) >= 2:
                row = events[(r.ticker, r.report_date)]
                reports[reports.index(r)] = _finish(ctx, row, r.week, ev, extra)

    reports.sort(key=lambda r: (-r.rank_score, r.ticker))
    sources = _sources(settings, providers, ctx, tally)
    ok_map = {x.name: (x.configured and (x.ok > 0 or x.failed == 0)) for x in sources}
    for x in sources:
        if x.failed and x.note:
            warnings.append(f"{x.name}: {x.failed} failed call(s); last error: {x.note}")
    return Scan(
        model_version=MODEL_VERSION,
        generated_at=utcnow(),
        today=today,
        mock=mock,
        weeks={
            w.label: {"start": w.start.isoformat(), "end": w.end.isoformat()} for w in (this, nxt)
        },
        calendar_counts=counts,
        analysed=len(reports),
        reports=reports,
        skipped=skipped,
        sources=sources,
        warnings=warnings,
        global_suggestions=global_suggestions(
            {
                k: v
                for k, v in ok_map.items()
                if k in ("finnhub", "sec_edgar", "yahoo", "google_news", "politicians")
            },
            logged_predictions,
            scored_predictions,
        ),
        disclaimer=DISCLAIMER,
    )


def _sources(s: Settings, p: Providers, ctx: _Context, t: _Tally) -> list[SourceStatus]:
    cfg = {
        "finnhub": p.finnhub is not None,
        "alpha_vantage": p.alpha_vantage is not None,
        "sec_edgar": p.sec is not None,
        "yahoo": p.yahoo is not None,
        "google_news": p.news is not None,
        "politicians": ctx.pol.configured,
        "13f": ctx.inst.configured,
        "options": bool(p.yahoo and s.options_iv_enabled),
        "finra": p.finra is not None,
        "nasdaq": ctx.nasdaq_http is not None,
    }
    notes = {
        "finnhub": p.unavailable.get("finnhub", ""),
        "alpha_vantage": p.unavailable.get("alpha_vantage", ""),
        "sec_edgar": p.unavailable.get("sec", ""),
        "politicians": "" if cfg["politicians"] else "set POLITICIAN_TRADES_URL",
        "options": "" if cfg["options"] else "set OPTIONS_IV_ENABLED=true",
    }
    return [
        SourceStatus(
            name=n,
            configured=cfg[n],
            ok=t.ok[n],
            failed=t.fail[n],
            note=t.err.get(n) or ("" if cfg[n] else notes.get(n, "not configured")),
        )
        for n in SOURCES
    ]


# ---- snapshot IO ------------------------------------------------------------------------------
def save_scan(scan: Scan, path: Path = SNAPSHOT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scan.model_dump(mode="json"), indent=1), encoding="utf-8")
    return path


def load_scan(path: Path = SNAPSHOT) -> Scan | None:
    if not path.exists():
        return None
    try:
        return Scan.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def render_table(scan: Scan, only: str | None = None) -> str:
    rows = [r for r in scan.reports if only in (None, "both", r.week)]
    head = f"{'tkr':6} {'report':10} {'when':4} {'P(up)':>6} {'conf':>5} {'move':>6} {'MC EV':>7} {'p5':>7} {'long?':5}  top signal"
    lines = [
        f"Earnings scan {scan.generated_at:%Y-%m-%d %H:%M} UTC  |  this week {scan.weeks['this']['start']}.."
        f"{scan.weeks['this']['end']}: {scan.calendar_counts['this']} reports, next week: "
        f"{scan.calendar_counts['next']}  |  analysed {scan.analysed}",
        head,
        "-" * len(head),
    ]
    for r in rows:
        top = max(
            (x for x in r.signals if x.available), key=lambda x: abs(x.contribution), default=None
        )
        mc = r.mc
        lines.append(
            f"{r.ticker:6} {r.report_date.isoformat():10} {r.session:4} {r.p_up:6.1%} {r.confidence:5.0%} "
            f"{r.expected_move:6.1%} {mc.mean:7.2%} {mc.p05:7.1%} {'LONG' if r.long_candidate else ('short' if r.short_candidate else ''):5}  "
            f"{top.label + ' ' + format(top.contribution, '+.2f') if top else '-'}"
        )
    lines += ["", *[f"WARNING: {w}" for w in scan.warnings], "", scan.disclaimer]
    return "\n".join(lines)
