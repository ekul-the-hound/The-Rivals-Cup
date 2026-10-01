"""The 12 read-only tool handlers. Each takes (ctx, validated input) and returns a ToolResult.

Only reads. No handler imports a write path, a provider, a job runner, or a network client.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.mcp.common import (
    ToolContext,
    ToolError,
    bars_for,
    evidence_sources,
    get_security,
    liquidity_block,
    paginate,
    price_freshness,
    public_security,
    returns_block,
    securities_by_ticker,
)
from app.mcp.envelope import ToolResult, clean_text, untrusted
from app.mcp.rules import CHECKLIST, rules_summary
from app.mcp.schemas import (
    BlackoutListIn,
    LiquidityCheckIn,
    ManualChecklistIn,
    MarketDashboardIn,
    PeerPairCandidatesIn,
    PeerPairPacketIn,
    PortfolioRiskIn,
    RankSectorEtfsIn,
    SearchFilingsIn,
    SearchNewsIn,
    StockPacketIn,
)
from app.models.enums import DataStatus
from app.schemas.api import ManualPortfolioResponse
from app.services.features.metrics import (
    adv_dollar,
    close_series,
    correlation,
    estimate_liquidity_cap,
)
from app.services.monitoring import get_data_quality
from app.services.pairs.blackout import evaluate_leg, scoring_week
from app.services.pairs.engine import PeerPairEngine
from app.services.pairs.factors import CLUSTER_OF_SECTOR
from app.services.pairs.inspect import PairNotFound, inspect_pair
from app.services.pairs.loader import ACTIVE_STATUSES, load_week_inputs
from app.services.pairs.params import EngineParams
from app.services.portfolio import get_manual_portfolios
from app.services.scoring import get_latest_scores
from app.services.validation.freshness import age_status

BENCHMARKS = ("SPY", "QQQ", "IWM")


def _params(ctx: ToolContext, notional: float | None = None) -> EngineParams:
    return EngineParams(
        adv_cap_pct=ctx.settings.wsr_est_adv_pct_cap,
        intended_leg_notional_usd=notional or ctx.settings.default_leg_size_usd,
    )


# ---------------------------------------------------------------- 1
def get_competition_rules_summary(ctx: ToolContext, _a: Any) -> ToolResult:
    return ToolResult(
        data=rules_summary(),
        freshness={"rules": {"version_static": True}},
        warnings=[
            "Not the official rule set; verify against current official rules and Trader View."
        ],
    )


# ---------------------------------------------------------------- 2/3 helpers
def _sector_etfs(ctx: ToolContext) -> list[dict[str, Any]]:
    return sorted(
        (s for s in ctx.store.select("securities") if s.get("is_etf") and s.get("sector") and not s.get("is_benchmark")),
        key=lambda s: s["ticker"],
    )  # fmt: skip


def _rank(
    ctx: ToolContext, lookbacks: list[int]
) -> tuple[list[dict[str, Any]], dict, list[str], dict]:
    from app.services.features.metrics import trailing_returns

    rows, missing, fresh, series = [], [], {}, {}
    for s in _sector_etfs(ctx):
        bars = bars_for(ctx, s["id"])
        fresh[s["ticker"]] = price_freshness(s["ticker"], bars, ctx, missing)
        ser = close_series(bars)
        series[s["ticker"]] = ser
        r = trailing_returns(ser, tuple(lookbacks)) if len(ser) else {}
        rows.append({
            "ticker": s["ticker"], "sector": s["sector"], "cluster": CLUSTER_OF_SECTOR.get(s["sector"], "unclassified"),
            "wsr_eligibility": s.get("wsr_eligibility"),
            "returns": {f"{n}d": r.get(n) for n in lookbacks},
        })  # fmt: skip
    for n in lookbacks:
        have = sorted(
            (r for r in rows if r["returns"][f"{n}d"] is not None),
            key=lambda r: -r["returns"][f"{n}d"],
        )
        for i, r in enumerate(have, 1):
            r.setdefault("ranks", {})[f"{n}d"] = i
    for r in rows:
        ranks = list((r.get("ranks") or {}).values())
        r["composite_rank_avg"] = (
            round(sum(ranks) / len(ranks), 2) if len(ranks) == len(lookbacks) else None
        )
    rows.sort(
        key=lambda r: (r["composite_rank_avg"] is None, r["composite_rank_avg"] or 0, r["ticker"])
    )
    return rows, fresh, missing, series


def _overlap_warnings(rows: list[dict[str, Any]], series: dict) -> list[str]:
    w: list[str] = []
    top = [r for r in rows if r["composite_rank_avg"] is not None][:3]
    by: dict[str, list[str]] = {}
    for r in top:
        by.setdefault(r["cluster"], []).append(r["ticker"])
    for c, tks in by.items():
        if len(tks) >= 2:
            w.append(f"FACTOR OVERLAP: top-ranked sectors {', '.join(tks)} share the '{c}' cluster; they tend to move together.")  # fmt: skip
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b = top[i]["ticker"], top[j]["ticker"]
            if len(series.get(a, [])) and len(series.get(b, [])):
                c, n = correlation(series[a], series[b], 60)
                if c is not None and c > 0.85:
                    w.append(f"HIGH CORRELATION: {a} and {b} 60d return correlation {c:.2f}; ranking both is largely one bet.")  # fmt: skip
    return w


# ---------------------------------------------------------------- 2
def get_market_dashboard(ctx: ToolContext, _a: MarketDashboardIn) -> ToolResult:
    missing: list[str] = []
    fresh: dict[str, Any] = {"prices": {}}
    secs = securities_by_ticker(ctx)
    bench = {}
    for t in BENCHMARKS:
        s = secs.get(t)
        if not s:
            missing.append(f"{t}: not in securities table")
            continue
        bars = bars_for(ctx, s["id"])
        fresh["prices"][t] = price_freshness(t, bars, ctx, missing)
        bench[t] = {
            "last_close": bars[-1].get("close") if bars else None,
            "returns": returns_block(bars),
        }
    rows, f2, m2, series = _rank(ctx, [1, 5, 20])
    fresh["prices"].update(f2)
    missing += m2
    macro_rows = ctx.store.select("macro_context_snapshots", order="as_of_date", desc=True, limit=1)
    macro = None
    if macro_rows:
        m = macro_rows[0]
        macro = {k: m.get(k) for k in ("as_of_date", "dgs2", "dgs10", "yield_curve_10y2y", "fed_funds", "vix", "regime", "data_status")}  # fmt: skip
        st = age_status(m.get("as_of_date"), "macro_daily", ctx.now)
        fresh["macro"] = {"as_of_date": m.get("as_of_date"), "status": st.value}
        if st != DataStatus.AVAILABLE:
            missing.append(f"macro context {st.value} (as of {m.get('as_of_date')})")
    else:
        missing.append("macro context missing")
        fresh["macro"] = {"status": "MISSING"}
    mk_rows = ctx.store.select(
        "market_context_snapshots", order="snapshot_date", desc=True, limit=1
    )
    mk = None
    if mk_rows:
        x = mk_rows[0]
        mk = {
            "snapshot_date": x.get("snapshot_date"),
            "spy_return_1d": x.get("spy_return_1d"),
            "breadth": x.get("breadth"),
        }
        fresh["market_context"] = {"snapshot_date": x.get("snapshot_date")}
    else:
        missing.append("market context snapshot missing")
    warnings = _overlap_warnings(rows, series)
    sources = [{"type": "DB", "id": "market_bars", "note": "Yahoo daily bars (unofficial, may be wrong)"}, {"type": "DB", "id": "macro_context_snapshots", "note": "FRED"}]  # fmt: skip
    return ToolResult(
        data={"benchmarks": bench, "sector_etf_ranking_20d": rows, "macro": macro, "market_context": mk},
        freshness=fresh, warnings=warnings, missing_or_stale=missing, sources=sources,
    )  # fmt: skip


# ---------------------------------------------------------------- 3
def rank_sector_etfs(ctx: ToolContext, a: RankSectorEtfsIn) -> ToolResult:
    rows, fresh, missing, series = _rank(ctx, a.lookbacks)
    if not rows:
        missing.append("no sector ETFs in securities table")
    return ToolResult(
        data={
            "lookbacks_days": a.lookbacks,
            "universe_note": "Sector ETFs in the research universe. Whether WSR allows each ETF is UNVERIFIED unless wsr_eligibility says otherwise; confirm in Trader View.",
            "ranking": rows,
        },
        freshness={"prices": fresh}, warnings=_overlap_warnings(rows, series), missing_or_stale=missing,
        sources=[{"type": "DB", "id": "market_bars"}],
    )  # fmt: skip


# ---------------------------------------------------------------- 4
def _cand_item(e: Any) -> dict[str, Any]:
    pk = e.packet
    m = pk.metrics or {}
    liq = m.get("liquidity") or {}
    cc = pk.catalyst_context or {}
    return {
        "pair_id": e.pair_id, "name": e.name, "long_ticker": e.long_ticker, "short_ticker": e.short_ticker,
        "eligible": e.eligible, "pair_quality_score": e.score, "data_quality_score": e.data_quality_score,
        "correlation_60d": m.get("correlation_60d"),
        "liquidity": {"estimated_leg_cap_usd": liq.get("est_cap_usd"), "adv_20d_usd": liq.get("adv_20d_usd"), "intended_leg_notional_usd": liq.get("intended_leg_notional_usd")},
        "event_blackout": {"status": (pk.event_blackout or {}).get("status"), "reasons": (pk.event_blackout or {}).get("blocked_reasons", [])},
        "catalyst_summary": {t: {"filings_30d": v.get("filings_30d"), "catalysts_14d": len(v.get("catalysts_14d", []))} for t, v in cc.items()},
        "top_reasons": pk.top_reasons, "risk_flags": pk.risk_flags,
        "ineligible_reasons": [r.model_dump() for r in e.ineligible_reasons],
        "factor": e.factor, "missing_or_stale": pk.missing_or_stale,
    }  # fmt: skip


def get_peer_pair_candidates(ctx: ToolContext, a: PeerPairCandidatesIn) -> ToolResult:
    inputs, start, end = load_week_inputs(ctx.store, ctx.now)
    evals = PeerPairEngine(_params(ctx)).evaluate_all(inputs)
    if a.sector:
        evals = [e for e in evals if (e.factor.get("sector") or "").lower() == a.sector.lower()]
    page, nxt = paginate(evals, a.limit, a.cursor, {"s": a.sector, "t": ctx.now.date().isoformat()})
    items = [_cand_item(e) for e in page]
    missing = [f"{i['name']}: {m}" for i in items for m in i["missing_or_stale"]][:30]
    warn = [
        "Direction is the engine's momentum-continuation rule; the counter-thesis argues mean reversion. You decide."
    ]
    if not evals:
        warn.append("no pairs matched" + (f" sector '{a.sector}'" if a.sector else ""))
    return ToolResult(
        data={"week": f"{start} to {end}", "total_matching": len(evals), "candidates": items},
        freshness={"week_start": start.isoformat(), "evaluated_at": ctx.now.isoformat()},
        warnings=warn, missing_or_stale=missing, next_cursor=nxt,
        sources=[{"type": "DB", "id": f"peer_pairs:{i['pair_id']}"} for i in items],
    )  # fmt: skip


# ---------------------------------------------------------------- 5
def _find_pair(ctx: ToolContext, lt: str, st: str) -> dict[str, Any] | None:
    secs = securities_by_ticker(ctx)
    if lt not in secs or st not in secs:
        missing = [t for t in (lt, st) if t not in secs]
        raise ToolError(f"not in the research universe: {', '.join(missing)}")
    want = {secs[lt]["id"], secs[st]["id"]}
    mem: dict[str, set[str]] = {}
    for m in ctx.store.select("peer_pair_members"):
        mem.setdefault(m["pair_id"], set()).add(m["security_id"])
    for p in ctx.store.select("peer_pairs"):
        if mem.get(p["id"]) == want:
            return p
    return None


def get_peer_pair_packet(ctx: ToolContext, a: PeerPairPacketIn) -> ToolResult:
    pair = _find_pair(ctx, a.long_ticker, a.short_ticker)
    legs = [get_security(ctx, a.long_ticker)["id"], get_security(ctx, a.short_ticker)["id"]]
    sources = evidence_sources(ctx, legs)
    if pair is None:
        try:
            ins = inspect_pair(ctx.store, ctx.settings, a.long_ticker, a.short_ticker, ctx.now)
        except PairNotFound as exc:
            raise ToolError(str(exc)) from exc
        data = {
            "curated_pair": False,
            "note": "Not a curated peer pair: ad-hoc inspection only, no pair-engine score or hard-eligibility check.",
            "inspection": ins.model_dump(mode="json"),
            "manual_review_context": {"checklist": CHECKLIST},
        }
        return ToolResult(
            data=data, freshness={"evaluated_at": ctx.now.isoformat()},
            warnings=["Ad-hoc combination; the owner has not mapped a peer relationship for it."],
            missing_or_stale=ins.missing_or_stale, sources=sources,
        )  # fmt: skip
    inputs, start, end = load_week_inputs(
        ctx.store, ctx.now, pair_ids=[pair["id"]], include_all_statuses=True
    )
    e = PeerPairEngine(_params(ctx)).evaluate_all(inputs)[0]
    pk = e.packet.model_dump(mode="json")
    data = {
        "curated_pair": True,
        "eligible": e.eligible,
        "ineligible_reasons": [r.model_dump() for r in e.ineligible_reasons],
        "components": e.components,
        "penalties": e.penalties,
        "packet": pk,
        "manual_review_context": {
            "pair_status": pair.get("status"),
            "manual_decision_this_week": e.packet.manual_decision,
            "requested_long": a.long_ticker, "requested_short": a.short_ticker,
            "engine_long": e.long_ticker, "engine_short": e.short_ticker,
            "requested_direction_matches_engine": (e.long_ticker, e.short_ticker) == (a.long_ticker, a.short_ticker),
            "review_status_is_not_a_trade": True,
            "checklist": e.packet.manual_checklist,
        },
    }  # fmt: skip
    warn = []
    if not data["manual_review_context"]["requested_direction_matches_engine"]:
        warn.append("Your requested long/short direction is the opposite of the engine's momentum rule; this is research, you decide.")  # fmt: skip
    if not e.eligible:
        warn.append(
            "Pair fails hard eligibility: " + "; ".join(r.code for r in e.ineligible_reasons)
        )
    return ToolResult(
        data=data, freshness={"week_start": start.isoformat(), "evaluated_at": ctx.now.isoformat()},
        warnings=warn, missing_or_stale=e.packet.missing_or_stale, sources=sources,
    )  # fmt: skip


# ---------------------------------------------------------------- 6
def get_stock_research_packet(ctx: ToolContext, a: StockPacketIn) -> ToolResult:
    s = get_security(ctx, a.ticker)
    sid = s["id"]
    missing: list[str] = []
    bars = bars_for(ctx, sid)
    fresh = {"prices": price_freshness(a.ticker, bars, ctx, missing)}
    comp = (ctx.store.select("companies", eq={"security_id": sid}, limit=1) or [{}])[0]
    if comp.get("wiki_profile_fetched_at") is not None:
        fresh["company_profile"] = {
            "status": age_status(comp["wiki_profile_fetched_at"], "wiki_profile", ctx.now).value
        }
    if not comp:
        missing.append(f"{a.ticker}: no company profile")
    etf = None
    for m in ctx.store.select("sector_etf_mappings", eq={"sector": s.get("sector")}):
        etf = next(
            (x for x in ctx.store.select("securities", eq={"id": m["etf_security_id"]}, limit=1)),
            None,
        )
        break
    members = [m["pair_id"] for m in ctx.store.select("peer_pair_members", eq={"security_id": sid})]
    pairs = []
    mem_all = ctx.store.select("peer_pair_members")
    secs_by_id = {x["id"]: x["ticker"] for x in ctx.store.select("securities")}
    for pid in members:
        p = (ctx.store.select("peer_pairs", eq={"id": pid}, limit=1) or [None])[0]
        if p:
            pairs.append({"pair_id": pid, "name": p["name"], "status": p.get("status"), "legs": sorted(secs_by_id.get(m["security_id"], "?") for m in mem_all if m["pair_id"] == pid)})  # fmt: skip
    wk_start, wk_end = scoring_week(ctx.today)
    brow = ctx.store.select(
        "security_event_blackouts", eq={"security_id": sid, "week_start": wk_start}, limit=1
    )
    bo = evaluate_leg(a.ticker, brow[0] if brow else None, wk_start, wk_end, ctx.now)
    if not brow:
        missing.append(f"{a.ticker}: no event-blackout row for week {wk_start}")
    since = ctx.today - timedelta(days=30)
    filings = ctx.store.select("filing_documents", eq={"security_id": sid}, gte={"filed_at": since}, order="filed_at", desc=True, limit=10)  # fmt: skip
    cats = ctx.store.select("corporate_catalysts", eq={"security_id": sid}, gte={"event_date": ctx.today - timedelta(days=30)}, order="event_date", desc=True, limit=8)  # fmt: skip
    news = ctx.store.select(
        "news_items", eq={"security_id": sid}, order="published_at", desc=True, limit=8
    )
    if not news:
        missing.append(f"{a.ticker}: no recent news stored")
    dq = ctx.store.select(
        "data_quality_issues", eq={"security_id": sid}, is_null=["resolved_at"], limit=10
    )
    data = {
        "security": public_security(s),
        "company": {
            "legal_name": clean_text(comp.get("legal_name"), 120),
            "cik": comp.get("cik"),
            "market_cap_usd": comp.get("market_cap_usd"),
            "wiki_industry": clean_text(comp.get("wiki_industry"), 100),
            "description": untrusted(
                {
                    "text": clean_text(comp.get("wiki_summary"), 500),
                    "source": "Wikipedia (static context)",
                }
            ),
        },
        "prices": {
            "last_close": bars[-1].get("close") if bars else None,
            "last_bar_date": str(bars[-1]["bar_date"])[:10] if bars else None,
            "returns": returns_block(bars),
        },  # fmt: skip
        "liquidity": liquidity_block(bars, ctx),
        "sector_etf": public_security(etf) if etf else None,
        "peer_pairs": pairs,
        "sec_filings_30d": [_filing_item(f, s["ticker"], 300) for f in filings],
        "catalysts_30d": [
            untrusted(
                {
                    "type": c.get("catalyst_type"),
                    "headline": clean_text(c.get("headline"), 200),
                    "date": c.get("event_date"),
                    "evidence_quality": c.get("evidence_quality"),
                    "url": c.get("source_url"),
                }
            )
            for c in cats
        ],  # fmt: skip
        "news": [_news_item(n, s["ticker"]) for n in news],
        "event_blackout": {
            "week": f"{wk_start} to {wk_end}",
            "blocked": bo.blocked,
            "reasons": bo.reasons,
            "warnings": bo.warnings,
            "row": _blackout_row(brow[0]) if brow else None,
        },  # fmt: skip
        "open_data_quality_issues": [
            {
                "type": d.get("issue_type"),
                "severity": d.get("severity"),
                "description": clean_text(d.get("description"), 200),
            }
            for d in dq
        ],  # fmt: skip
    }
    return ToolResult(
        data=data, freshness=fresh, missing_or_stale=missing, sources=evidence_sources(ctx, [sid], 5),
        warnings=["WSR eligibility, position limits and live prices must be checked in Trader View."],
    )  # fmt: skip


def _blackout_row(r: dict[str, Any]) -> dict[str, Any]:
    return {"week_start": r.get("week_start"), "earnings_date_if_known": r.get("earnings_date_if_known"), "known_major_event_date": r.get("known_major_event_date"), "event_risk_notes": untrusted({"text": clean_text(r.get("event_risk_notes"), 300)}), "manually_verified_at": r.get("manually_verified_at"), "source_url": r.get("source_url")}  # fmt: skip


def _filing_item(f: dict[str, Any], ticker: str | None, excerpt_len: int) -> dict[str, Any]:
    return untrusted({
        "ticker": ticker, "form_type": f.get("form_type"), "accession_number": f.get("accession_number"),
        "filed_at": f.get("filed_at"), "period_of_report": f.get("period_of_report"), "url": f.get("url"),
        "title": clean_text(f.get("title"), 200), "excerpt": clean_text(f.get("summary"), excerpt_len),
        "evidence_quality": f.get("evidence_quality"),
    })  # fmt: skip


def _news_item(n: dict[str, Any], ticker: str | None) -> dict[str, Any]:
    return untrusted({
        "ticker": ticker, "headline": clean_text(n.get("headline"), 300),
        "publisher": clean_text(n.get("publisher_name") or n.get("source_name"), 80), "url": n.get("url"),
        "published_at": n.get("published_at"), "evidence_quality": n.get("evidence_quality"),
        "link_confirmed": n.get("link_confirmed"), "summary": clean_text(n.get("summary"), 300),
    })  # fmt: skip


# ---------------------------------------------------------------- 7
def search_sec_filings(ctx: ToolContext, a: SearchFilingsIn) -> ToolResult:
    eq: dict[str, Any] = {}
    by_id: dict[str, str] = {}
    if a.ticker:
        s = get_security(ctx, a.ticker)
        eq["security_id"] = s["id"]
        by_id[s["id"]] = s["ticker"]
    else:
        by_id = {x["id"]: x["ticker"] for x in ctx.store.select("securities")}
    rows = ctx.store.select(
        "filing_documents", eq=eq or None, in_={"form_type": a.filing_types},
        gte={"filed_at": a.start_date}, lte={"filed_at": datetime.combine(a.end_date, datetime.max.time(), UTC)},
        order="filed_at", desc=True, limit=500,
    )  # fmt: skip
    if a.query:
        q = a.query.casefold()
        rows = [
            r for r in rows if q in f"{r.get('title') or ''} {r.get('summary') or ''}".casefold()
        ]
    page, nxt = paginate(
        rows, a.limit, a.cursor, {"a": a.model_dump(mode="json", exclude={"cursor", "limit"})}
    )
    items = [_filing_item(r, by_id.get(r["security_id"]), 600) for r in page]
    missing = []
    fresh: dict[str, Any] = {}
    if rows:
        newest = max(str(r.get("filed_at") or "") for r in rows)
        fresh["newest_filing_in_results"] = newest
    last_sync = ctx.store.select("filing_documents", order="retrieved_at", desc=True, limit=1)
    if last_sync:
        st = age_status(last_sync[0].get("retrieved_at"), "sec_run", ctx.now)
        fresh["last_sec_retrieval"] = {"at": last_sync[0].get("retrieved_at"), "status": st.value}
        if st != DataStatus.AVAILABLE:
            missing.append(
                f"SEC filings last retrieved {last_sync[0].get('retrieved_at')} ({st.value})"
            )
    else:
        missing.append("no SEC filings stored")
    return ToolResult(
        data={"total_matching": len(rows), "filings": items,
              "note": "Metadata plus a bounded excerpt of stored filing summaries. Read the filing on EDGAR before relying on it."},  # fmt: skip
        freshness=fresh, missing_or_stale=missing, next_cursor=nxt,
        sources=[{"type": "SEC_FILING", "id": i["accession_number"], "url": i["url"]} for i in items],
    )  # fmt: skip


# ---------------------------------------------------------------- 8
def search_news(ctx: ToolContext, a: SearchNewsIn) -> ToolResult:
    end = a.end_time or ctx.now
    start = a.start_time or (end - timedelta(days=14))
    secs = ctx.store.select("securities")
    by_id = {s["id"]: s["ticker"] for s in secs}
    ids: list[str] | None = None
    if a.ticker:
        ids = [get_security(ctx, a.ticker)["id"]]
    if a.sector:
        sec_ids = [s["id"] for s in secs if (s.get("sector") or "").lower() == a.sector.lower()]
        ids = sec_ids if ids is None else [i for i in ids if i in sec_ids]
        if not ids:
            raise ToolError(f"no securities found for sector '{a.sector}'")
    rows = ctx.store.select(
        "news_items", in_={"security_id": ids} if ids is not None else None,
        gte={"published_at": start}, lte={"published_at": end}, order="published_at", desc=True, limit=500,
    )  # fmt: skip
    if a.query:
        q = a.query.casefold()
        rows = [
            r for r in rows if q in f"{r.get('headline') or ''} {r.get('summary') or ''}".casefold()
        ]
    seen, uniq = set(), []
    for r in rows:
        k = r.get("content_hash") or " ".join(str(r.get("headline", "")).casefold().split())
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    page, nxt = paginate(
        uniq,
        a.limit,
        a.cursor,
        {"q": a.query, "t": a.ticker, "s": a.sector, "a": str(start), "b": str(end)},
    )
    items = [_news_item(r, by_id.get(r.get("security_id"))) for r in page]
    warn = ["News is third-party content of mixed reliability; verify against primary sources."]
    weak = sum(1 for i in items if i["evidence_quality"] not in ("PRIMARY", "SECONDARY"))
    if weak:
        warn.append(
            f"{weak} of {len(items)} items are DERIVED/UNVERIFIED quality; do not use them as core evidence."
        )
    missing, fresh = [], {}
    latest = ctx.store.select("news_items", order="published_at", desc=True, limit=1)
    if latest:
        st = age_status(latest[0].get("published_at"), "news", ctx.now)
        fresh["newest_news_stored"] = {
            "published_at": latest[0].get("published_at"),
            "status": st.value,
        }
        if st != DataStatus.AVAILABLE:
            missing.append(f"newest stored news is {st.value}")
    else:
        missing.append("no news stored")
    return ToolResult(
        data={"window": {"start": start.isoformat(), "end": end.isoformat()}, "total_matching": len(uniq), "items": items},
        freshness=fresh, warnings=warn, missing_or_stale=missing, next_cursor=nxt,
        sources=[{"type": "NEWS", "url": i["url"], "publisher": i["publisher"]} for i in items],
    )  # fmt: skip


# ---------------------------------------------------------------- 9
def get_liquidity_check(ctx: ToolContext, a: LiquidityCheckIn) -> ToolResult:
    s = get_security(ctx, a.ticker)
    bars = bars_for(ctx, s["id"], 40)
    missing: list[str] = []
    fresh = {"prices": price_freshness(a.ticker, bars, ctx, missing)}
    adv = adv_dollar(bars)
    cap = estimate_liquidity_cap(adv, ctx.settings.wsr_est_adv_pct_cap)
    if cap is None:
        status, detail = "UNKNOWN", "not enough volume history to estimate"
        missing.append(f"{a.ticker}: cannot compute 20-day dollar volume")
    elif a.intended_notional <= cap:
        status, detail = (
            "PASS",
            f"intended ${a.intended_notional:,.0f} is within the estimated cap ${cap:,.0f}",
        )
    else:
        status, detail = (
            "WARNING",
            f"intended ${a.intended_notional:,.0f} EXCEEDS the estimated cap ${cap:,.0f}",
        )
    if fresh["prices"]["status"] != "AVAILABLE" and status == "PASS":
        status, detail = "WARNING", detail + "; but price data is not fresh"
    lm = ctx.store.select(
        "liquidity_metrics", eq={"security_id": s["id"]}, order="as_of_date", desc=True, limit=1
    )
    stored = None
    if lm:
        stored = {"as_of_date": lm[0].get("as_of_date"), "adv_20d_usd": lm[0].get("adv_20d_usd"), "status": age_status(lm[0].get("as_of_date"), "liquidity", ctx.now).value}  # fmt: skip
    return ToolResult(
        data={"ticker": a.ticker, "intended_notional_usd": a.intended_notional, "trailing_20d_avg_dollar_volume_usd": round(adv) if adv else None,
              "estimated_cap_pct": ctx.settings.wsr_est_adv_pct_cap, "estimated_cap_usd": cap, "result": status, "detail": detail,
              "utilization_of_cap": round(a.intended_notional / cap, 3) if cap else None, "stored_liquidity_metric": stored,
              "data_controls_warning": "This is an ESTIMATE. WSR's own data and limits control; check Trader View before any entry."},  # fmt: skip
        freshness=fresh, missing_or_stale=missing, warnings=["WSR data controls; the 1% figure is an unverified estimate."],
        sources=[{"type": "DB", "id": "market_bars", "ticker": a.ticker}],
    )  # fmt: skip


# ---------------------------------------------------------------- 10
def _live_estimate(ctx: ToolContext, portfolio_id: str) -> dict[str, Any] | None:
    """Read-only ESTIMATED score for the current weekly round from manually recorded legs."""
    from datetime import timedelta

    from app.services.scoring.wsr import estimate_portfolio

    today = ctx.now.date()
    wk = today - timedelta(days=today.weekday())
    try:
        est = estimate_portfolio(ctx.store, portfolio_id, wk, min(today, wk + timedelta(days=4)))
    except Exception:  # never leak internals; the snapshot path still works
        return None
    if est.positions_used == 0:
        return None
    return {
        "source": "MODEL_ESTIMATE_LIVE", "label": est.label, "authority": est.authority, "week_start": wk.isoformat(),
        "estimated_total_return_pct": est.total_return_pct, "estimated_downside_deviation_pct": est.downside_deviation_pct,
        "estimated_player_score": est.player_score, "days_valued": est.days_valued, "scheduled_days": est.scheduled_days,
        "modeled_gross_exposure_pct": est.modeled_gross_exposure_pct, "manual_recorded_gross_usd": est.manual_recorded_gross_usd,
        "warnings": [f"{w.code}: {clean_text(w.message, 160)}" for w in est.warnings][:12],
    }  # fmt: skip


def portfolio_view(ctx: ToolContext) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    secs = {s["id"]: s["ticker"] for s in ctx.store.select("securities")}
    mp: ManualPortfolioResponse = get_manual_portfolios(ctx.store)
    scores = {s.portfolio_id: s for s in get_latest_scores(ctx.store).scores}
    missing: list[str] = []
    fresh: dict[str, Any] = {}
    manual, model = [], []
    if not mp.portfolios:
        missing.append("no manual portfolio logged")
    for v in mp.portfolios:
        pf = v.portfolio
        gross, net = v.cost_basis_gross_exposure_usd, v.cost_basis_net_exposure_usd
        cash = pf.starting_cash_usd or 0
        manual.append({
            "source": "MANUALLY_LOGGED_BY_OWNER", "portfolio": clean_text(pf.name, 80), "starting_cash_usd": cash,
            "open_positions": [{"ticker": secs.get(str(p.security_id), "?"), "side": p.side.value, "quantity": p.quantity, "avg_entry_price": p.avg_entry_price, "cost_basis_usd": round(p.quantity * (p.avg_entry_price or 0), 2), "opened_at": p.opened_at, "data_status": p.data_status.value} for p in v.open_positions],  # fmt: skip
            "estimated_gross_exposure_usd": gross, "estimated_net_exposure_usd": net,
            "gross_pct_of_starting_cash": round(gross / cash * 100, 1) if cash else None,
            "net_pct_of_starting_cash": round(net / cash * 100, 1) if cash else None,
            "exposure_basis": v.exposure_note,
            "recent_manual_trade_count": len(v.recent_trades),
        })  # fmt: skip
        sc = scores.get(str(pf.id))
        snap = sc.latest if sc else None
        live = _live_estimate(ctx, str(pf.id))
        if live:
            model.append(live)
        if snap is None:
            missing.append(f"{pf.name}: no estimated score snapshot")
            model.append({"source": "MODEL_ESTIMATE", "portfolio": clean_text(pf.name, 80), "estimated_player_score": None, "estimated_drawdown": None})  # fmt: skip
        else:
            comps = snap.components or {}
            st = age_status(snap.snapshot_at, "liquidity", ctx.now)  # 5-day staleness window
            fresh[f"score:{pf.name}"] = {"snapshot_at": snap.snapshot_at, "status": st.value}
            if st != DataStatus.AVAILABLE:
                missing.append(f"{pf.name}: score snapshot {st.value}")
            model.append({
                "source": "MODEL_ESTIMATE", "portfolio": clean_text(pf.name, 80), "estimated_player_score": snap.estimated_score,
                "estimated_drawdown": comps.get("drawdown", comps.get("max_drawdown")), "is_estimate": True,
                "snapshot_at": snap.snapshot_at, "methodology": clean_text(snap.methodology, 200),
                "warnings": [clean_text(w, 200) for w in (snap.warnings or [])][:10],
            })  # fmt: skip
    data = {
        "manually_logged_data": manual,
        "model_estimates": model,
        "distinction": "manually_logged_data = positions the owner entered by hand (cost basis, not marked to market). model_estimates = computed estimates, never the official Player Score or drawdown.",
    }
    return data, missing, fresh


def get_portfolio_risk_context(ctx: ToolContext, _a: PortfolioRiskIn) -> ToolResult:
    data, missing, fresh = portfolio_view(ctx)
    warn = ["Estimates only. Trader View is authoritative for exposure, Player Score and drawdown."]
    for m in data["manually_logged_data"]:
        if m["gross_pct_of_starting_cash"] and m["gross_pct_of_starting_cash"] > 100:
            warn.append(
                f"{m['portfolio']}: estimated gross exposure exceeds 100% of starting cash."
            )
    return ToolResult(data=data, freshness=fresh, warnings=warn, missing_or_stale=missing,
                      sources=[{"type": "DB", "id": "manual_positions"}, {"type": "DB", "id": "score_snapshots"}])  # fmt: skip


# ---------------------------------------------------------------- 11
def get_weekly_event_blackout_list(ctx: ToolContext, a: BlackoutListIn) -> ToolResult:
    monday = a.week_start - timedelta(days=a.week_start.weekday())
    secs = {s["id"]: s for s in ctx.store.select("securities")}
    rows = ctx.store.select(
        "security_event_blackouts", gte={"week_start": monday}, lte={"week_start": a.week_end}
    )
    blocked, missing, by_sid = [], [], {}
    for r in rows:
        s = secs.get(r["security_id"])
        if not s:
            continue
        lb = evaluate_leg(s["ticker"], r, a.week_start, a.week_end, ctx.now)
        by_sid.setdefault(r["security_id"], []).append(lb)
        if lb.blocked:
            blocked.append({"ticker": s["ticker"], "reasons": lb.reasons, **_blackout_row(r)})
        missing += [w for w in lb.warnings if "never manually verified" in w or "older than" in w]
    overrides = ctx.store.select(
        "pair_blackout_overrides", gte={"week_start": monday}, lte={"week_start": a.week_end}
    )
    ov_by_pair = {o["pair_id"]: o for o in overrides}
    mem: dict[str, list[str]] = {}
    for m in ctx.store.select("peer_pair_members"):
        mem.setdefault(m["pair_id"], []).append(m["security_id"])
    blocked_ids = {x["ticker"] for x in blocked}
    pairs = []
    for p in ctx.store.select("peer_pairs"):
        if p.get("status") not in ACTIVE_STATUSES:
            continue
        legs = [secs[i]["ticker"] for i in mem.get(p["id"], []) if i in secs]
        hit = sorted(t for t in legs if t in blocked_ids)
        for i in mem.get(p["id"], []):
            if i in secs and i not in by_sid:
                missing.append(
                    f"{secs[i]['ticker']}: no blackout row entered for this period (events unknown)"
                )
        if hit:
            ov = ov_by_pair.get(p["id"])
            pairs.append({"pair_id": p["id"], "name": p["name"], "legs": sorted(legs), "blocked_legs": hit,
                          "status": "OVERRIDDEN_BY_OWNER" if ov else "EXCLUDED_BY_DEFAULT",
                          "override_reason": untrusted({"text": clean_text(ov.get("reason"), 300)}) if ov else None})  # fmt: skip
    return ToolResult(
        data={"week_start": a.week_start.isoformat(), "week_end": a.week_end.isoformat(), "excluded_securities": blocked, "affected_pairs": pairs,
              "note": "Blackout fields are entered and verified by the owner by hand; a missing row means events are UNKNOWN, not clear."},  # fmt: skip
        freshness={"blackout_rows_in_range": len(rows)}, missing_or_stale=missing,
        warnings=["Verify earnings/event dates on each company's IR calendar; this list is only as good as the manual entries."],
        sources=[{"type": "DB", "id": "security_event_blackouts"}] + [{"type": "URL", "url": b.get("source_url")} for b in blocked if b.get("source_url")],
    )  # fmt: skip


# ---------------------------------------------------------------- 12
def get_manual_entry_checklist(ctx: ToolContext, a: ManualChecklistIn) -> ToolResult:
    missing: list[str] = []
    legs = {"LONG": get_security(ctx, a.long_ticker), "SHORT": get_security(ctx, a.short_ticker)}
    notional = {"LONG": a.intended_long_notional, "SHORT": a.intended_short_notional}
    p = _params(ctx)
    items: list[dict[str, Any]] = []
    fresh: dict[str, Any] = {}

    def add(cid, check, status, detail, **kw):
        items.append({"id": cid, "check": check, "status": status, "detail": detail, **kw})

    for side, s in legs.items():
        elig = s.get("wsr_eligibility")
        ok = elig == "AVAILABLE" and s.get("is_active", True) and (s.get("country") or "US") == "US"
        add(f"allowed_security_{side.lower()}", f"{side} {s['ticker']}: allowed security",
            "MANUAL" if not ok else "PASS",
            f"database flag wsr_eligibility={elig}; security_type={s.get('security_type')}; exchange={s.get('exchange')}. Confirm availability and permissions in Trader View yourself.")  # fmt: skip
    for side, s in legs.items():
        lev = float(s.get("leverage_factor") or 1)
        if s.get("is_etf") and lev > p.max_leverage:
            add(f"leveraged_etf_{side.lower()}", f"{s['ticker']}: leveraged ETF check", "FAIL", f"ETF leverage {lev}x exceeds the {p.max_leverage}x limit in your strategy rules")  # fmt: skip
        elif s.get("is_etf"):
            add(f"leveraged_etf_{side.lower()}", f"{s['ticker']}: leveraged ETF check", "WARN", f"ETF, recorded leverage {lev}x; confirm the fund's actual leverage and whether it is inverse")  # fmt: skip
        else:
            add(f"leveraged_etf_{side.lower()}", f"{s['ticker']}: leveraged ETF check", "PASS", "not an ETF in the database")  # fmt: skip
    bars_by = {}
    for side, s in legs.items():
        bars = bars_by[side] = bars_for(ctx, s["id"], 40)
        adv = adv_dollar(bars)
        cap = estimate_liquidity_cap(adv, ctx.settings.wsr_est_adv_pct_cap)
        n = notional[side]
        if cap is None:
            add(f"liquidity_{side.lower()}", f"{s['ticker']}: estimated liquidity cap", "UNKNOWN", "cannot estimate 20-day dollar volume")  # fmt: skip
            missing.append(f"{s['ticker']}: liquidity estimate unavailable")
        elif n is None:
            add(f"liquidity_{side.lower()}", f"{s['ticker']}: estimated liquidity cap", "UNKNOWN", f"estimated cap ${cap:,.0f} (1% of 20d ADV); no intended notional supplied")  # fmt: skip
        elif n > cap:
            add(f"liquidity_{side.lower()}", f"{s['ticker']}: estimated liquidity cap", "WARN", f"intended ${n:,.0f} exceeds estimated cap ${cap:,.0f}")  # fmt: skip
        else:
            add(f"liquidity_{side.lower()}", f"{s['ticker']}: estimated liquidity cap", "PASS", f"intended ${n:,.0f} within estimated cap ${cap:,.0f}")  # fmt: skip
    # gross exposure
    pf_data, _, _ = portfolio_view(ctx)
    cur_gross = sum(m["estimated_gross_exposure_usd"] for m in pf_data["manually_logged_data"])
    this_gross = sum(v for v in notional.values() if v)
    assumed = p.portfolio_value_usd
    ceiling_pair, ceiling_total = (
        assumed * p.max_pair_gross_pct / 100,
        assumed * p.total_gross_budget_pct / 100,
    )
    if not this_gross:
        add("gross_exposure", "gross-exposure warning", "UNKNOWN", f"supply intended notionals; currently logged gross is ${cur_gross:,.0f}")  # fmt: skip
    else:
        warn = []
        if this_gross > ceiling_pair:
            warn.append(
                f"this pair's gross ${this_gross:,.0f} exceeds the suggested per-pair ceiling ${ceiling_pair:,.0f}"
            )
        if cur_gross + this_gross > ceiling_total:
            warn.append(
                f"prospective total gross ${cur_gross + this_gross:,.0f} exceeds the suggested budget ${ceiling_total:,.0f}"
            )
        add("gross_exposure", "gross-exposure warning", "WARN" if warn else "PASS",
            ("; ".join(warn) or f"this pair adds ${this_gross:,.0f} gross; logged gross ${cur_gross:,.0f}") + f". Ceilings assume a ${assumed:,.0f} portfolio (assumption, not your real figure).")  # fmt: skip
    if all(notional.values()):
        lg, sh = notional["LONG"], notional["SHORT"]
        imb = abs(lg - sh) / max(lg, sh)
        add("dollar_balance", "long/short dollar balance", "WARN" if imb > 0.10 else "PASS",
            f"legs differ by {imb:.0%}. Equal dollars are not market-neutral: check beta and volatility.")  # fmt: skip
    # event blackout
    wk_start, wk_end = scoring_week(ctx.today)
    for side, s in legs.items():
        row = ctx.store.select(
            "security_event_blackouts", eq={"security_id": s["id"], "week_start": wk_start}, limit=1
        )
        lb = evaluate_leg(s["ticker"], row[0] if row else None, wk_start, wk_end, ctx.now)
        if lb.blocked:
            add(
                f"event_blackout_{side.lower()}",
                f"{s['ticker']}: event blackout",
                "FAIL",
                "; ".join(lb.reasons),
            )
        elif lb.warnings:
            add(f"event_blackout_{side.lower()}", f"{s['ticker']}: event blackout", "WARN", "; ".join(lb.warnings) + ". Verify dates yourself.")  # fmt: skip
            missing += lb.warnings
        else:
            add(f"event_blackout_{side.lower()}", f"{s['ticker']}: event blackout", "PASS", f"no earnings/major event recorded for {wk_start}..{wk_end} (manually verified)")  # fmt: skip
    for side, s in legs.items():
        pf = price_freshness(s["ticker"], bars_by[side], ctx, missing)
        fresh[s["ticker"]] = pf
        last = bars_by[side][-1].get("close") if bars_by[side] else None
        add(f"price_freshness_{side.lower()}", f"{s['ticker']}: price freshness", "PASS" if pf["status"] == "AVAILABLE" else "WARN", f"latest daily bar {pf['latest_bar']} (status {pf['status']}), last close {last}. Compare with live prices in Trader View.")  # fmt: skip
    pair = _find_pair(ctx, a.long_ticker, a.short_ticker)
    concept = "Decide your own exit review points before entering."
    if pair:
        inputs, _, _ = load_week_inputs(
            ctx.store, ctx.now, pair_ids=[pair["id"]], include_all_statuses=True
        )
        e = PeerPairEngine(p).evaluate_all(inputs)[0]
        concept = f"{e.packet.invalidation_concept} {e.packet.target_concept}"
    add("manual_stop_target_reminder", "manual stop/target reminder", "MANUAL", concept + " These are concepts for your own review, not levels this system sets.")  # fmt: skip
    add("rule_reminder", "rule reminder", "MANUAL", "Re-read the current official Rival Cup rules. This checklist is not the official rule set. Trader View is authoritative for what is allowed.")  # fmt: skip
    return ToolResult(
        data={"long_ticker": a.long_ticker, "short_ticker": a.short_ticker, "curated_pair": pair is not None, "checklist": items,
              "general_checklist": CHECKLIST,
              "reminder": "This is a human checklist for your own review. It does not enter, queue or manage any trade; you enter any trade yourself in Trader View."},  # fmt: skip
        freshness=fresh, missing_or_stale=missing,
        warnings=[f"{i['check']}: {i['detail']}" for i in items if i["status"] in ("FAIL",)],
        sources=evidence_sources(ctx, [legs["LONG"]["id"], legs["SHORT"]["id"]], 2),
    )  # fmt: skip


# ---------------------------------------------------------------- resource-only
def quality_current(ctx: ToolContext) -> ToolResult:
    dq = get_data_quality(ctx.store, limit=50)
    missing: list[str] = []
    fresh: dict[str, Any] = {}
    spy = ctx.store.select("securities", eq={"ticker": "SPY"}, limit=1)
    if spy:
        fresh["spy_prices"] = price_freshness("SPY", bars_for(ctx, spy[0]["id"], 5), ctx, missing)
    for key, table, col, kind in (("macro", "macro_context_snapshots", "as_of_date", "macro_daily"), ("news", "news_items", "published_at", "news"), ("sec_filings", "filing_documents", "retrieved_at", "sec_run")):  # fmt: skip
        r = ctx.store.select(table, order=col, desc=True, limit=1)
        st = age_status(r[0].get(col), kind, ctx.now) if r else DataStatus.MISSING
        fresh[key] = {"latest": r[0].get(col) if r else None, "status": st.value}
        if st != DataStatus.AVAILABLE:
            missing.append(f"{key}: {st.value}")
    runs = ctx.store.select("provider_run_logs", order="started_at", desc=True, limit=20)
    wk = scoring_week(ctx.today)[0]
    bl = ctx.store.select("security_event_blackouts", eq={"week_start": wk})
    unverified = [r for r in bl if not r.get("manually_verified_at")]
    fresh["event_blackouts"] = {
        "week_start": wk.isoformat(),
        "rows": len(bl),
        "unverified_rows": len(unverified),
    }
    if not bl:
        missing.append(f"no event-blackout rows entered for week {wk}")
    data = {
        "open_issue_count": dq.open_issue_count,
        "by_severity": dq.by_severity,
        "issues": [
            {
                "type": i.issue_type,
                "severity": i.severity,
                "description": clean_text(i.description, 200),
                "detected_at": i.detected_at,
            }
            for i in dq.issues[:30]
        ],  # fmt: skip
        "recent_provider_runs": [
            {
                "provider": r.get("provider"),
                "job": r.get("job_name"),
                "status": r.get("status"),
                "started_at": r.get("started_at"),
                "rows_written": r.get("rows_written"),
            }
            for r in runs
        ],  # fmt: skip
        "note": "Provider error text is deliberately omitted from MCP output.",
    }
    return ToolResult(data=data, freshness=fresh, missing_or_stale=missing, warnings=["Stale or missing data is a reason to double-check, never an opportunity."])  # fmt: skip
