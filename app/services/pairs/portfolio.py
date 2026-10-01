"""Weekly portfolio selection: 4-6 pairs max, diversified by sector/cluster/driver.

Deterministic. Output is a RESEARCH_DRAFT. Nothing here marks a pair as traded.

Greedy selection
  1. Hard-ineligible pairs never enter (including a manual INCLUDE: it pins a pair but can NOT
     override hard eligibility).
  2. Pinned pairs (manual INCLUDE, eligible) go first, up to max_pairs; caps become warnings.
  3. Each round: adjusted = score - factor-overlap penalty against what is already selected.
     Take the best candidate with score >= min_score, adjusted >= min_adjusted_score, and room
     under max_per_cluster and max_per_driver. Stop when nothing qualifies or max_pairs reached.
  4. If fewer than target_min_pairs qualify, the portfolio stays short and says so.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from app.db.store import Store
from app.services.pairs.engine import PeerPairEngine
from app.services.pairs.factors import (
    CROWDED_CLUSTERS,
    FactorProfile,
    factor_overlap_penalty,
    pair_overlap,
)
from app.services.pairs.loader import load_week_inputs
from app.services.pairs.models import PairEvaluation, PairPacket
from app.services.pairs.params import EngineParams

if TYPE_CHECKING:  # write-side imports stay lazy so read-only consumers (MCP) never load them
    from app.db.writer import DB

PORTFOLIO_NOTE = (
    "RESEARCH DRAFT ONLY. No order, trade or position has been created or implied. "
    "You review each pair and enter any trade yourself in Trader View."
)


class SelectedPair(BaseModel):
    rank: int
    pair_id: str | None
    name: str
    long_ticker: str | None
    short_ticker: str | None
    pair_quality_score: float
    adjusted_score: float
    overlap_penalty: float
    overlap_reasons: list[str]
    suggested_max_gross_pct: float
    pinned_by_owner: bool = False
    factor: dict[str, str | None]
    packet: PairPacket
    is_trade: Literal[False] = False


class AlternatePair(BaseModel):
    rank: int
    pair_id: str | None
    name: str
    long_ticker: str | None
    short_ticker: str | None
    pair_quality_score: float
    adjusted_score: float
    why_not_selected: list[str]


class ExcludedPair(BaseModel):
    pair_id: str | None
    name: str
    reasons: list[dict[str, str]]


class WeeklyPortfolio(BaseModel):
    run_id: str
    week_start: date
    week_end: date
    built_at: datetime
    status: Literal["RESEARCH_DRAFT"] = "RESEARCH_DRAFT"
    note: str = PORTFOLIO_NOTE
    params: dict[str, Any]
    selected: list[SelectedPair]
    alternates: list[AlternatePair]
    excluded: list[ExcludedPair]
    warnings: list[str]
    summary: dict[str, Any]


def _fp(e: PairEvaluation) -> FactorProfile:
    f = e.factor
    return FactorProfile(
        f.get("sector"), f.get("cluster") or "unclassified", f.get("driver") or "unclassified"
    )


def _count(sel: list[PairEvaluation], attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in sel:
        v = getattr(_fp(e), attr) or "unclassified"
        out[v] = out.get(v, 0) + 1
    return out


def select_pairs(
    evals: list[PairEvaluation], p: EngineParams
) -> tuple[list[tuple[PairEvaluation, float, list[str], bool]], list[str]]:
    """Return ([(eval, overlap_penalty, overlap_reasons, pinned)], notes)."""
    eligible = [e for e in evals if e.eligible]
    pinned = [e for e in eligible if e.packet.manual_decision == "INCLUDE"]
    pinned.sort(key=lambda e: (-e.score, e.name))
    chosen: list[PairEvaluation] = []
    pin_flags: dict[str, bool] = {}
    notes: list[str] = []
    for e in pinned[: p.max_pairs]:
        chosen.append(e)
        pin_flags[e.name] = True
    if len(pinned) > p.max_pairs:
        notes.append(
            f"{len(pinned)} pairs pinned by owner; only the top {p.max_pairs} kept (max pairs)."
        )
    pool = [e for e in eligible if e.name not in pin_flags]
    while len(chosen) < p.max_pairs:
        profiles = [_fp(c) for c in chosen]
        clusters, drivers = _count(chosen, "cluster"), _count(chosen, "driver")
        best: tuple[float, str, PairEvaluation] | None = None
        for e in pool:
            fp = _fp(e)
            if e.score < p.min_score:
                continue
            if clusters.get(fp.cluster, 0) >= p.max_per_cluster and fp.cluster != "unclassified":
                continue
            if drivers.get(fp.driver, 0) >= p.max_per_driver and fp.driver != "unclassified":
                continue
            pen, _ = factor_overlap_penalty(fp, profiles, p)
            adj = e.score - pen
            if adj < p.min_adjusted_score:
                continue
            if best is None or (-adj, e.name) < (-best[0], best[1]):
                best = (adj, e.name, e)
        if best is None:
            break
        chosen.append(best[2])
        pool.remove(best[2])
    # final overlap vs the *other* selected pairs (order-independent display value)
    out = []
    for e in chosen:
        others = [_fp(o) for o in chosen if o is not e]
        pen, why = factor_overlap_penalty(_fp(e), others, p)
        out.append((e, pen, why, pin_flags.get(e.name, False)))
    return out, notes


def _liq_pct(e: PairEvaluation, p: EngineParams) -> float | None:
    caps = (e.packet.metrics.get("liquidity") or {}).get("est_cap_usd") or {}
    vals = [v for v in caps.values() if v]
    if len(vals) < 2:
        return None
    return round(2 * min(vals) / p.portfolio_value_usd * 100, 1)


def allocate(
    selected: list[tuple[PairEvaluation, float, list[str], bool]], p: EngineParams
) -> dict[str, float]:
    """Suggested max gross % per pair (both legs combined, % of portfolio value). Ceilings, not orders."""
    weights = {e.name: max(e.score - pen, 1.0) for e, pen, _, _ in selected}
    tot = sum(weights.values()) or 1.0
    out: dict[str, float] = {}
    for e, _, _, _ in selected:
        pct = p.total_gross_budget_pct * weights[e.name] / tot
        pct = min(pct, p.max_pair_gross_pct)
        lq = _liq_pct(e, p)
        if lq is not None:
            pct = min(pct, lq)
        out[e.name] = round(pct, 1)
    return out


def _warnings(
    selected: list[tuple[PairEvaluation, float, list[str], bool]],
    alloc: dict[str, float],
    p: EngineParams,
    notes: list[str],
) -> list[str]:
    w = list(notes)
    n = len(selected)
    if n == 0:
        w.append(
            "NO PAIRS QUALIFIED this week. Standing aside is a valid outcome; nothing is forced."
        )
    elif n < p.target_min_pairs:
        w.append(
            f"Only {n} pair(s) met the quality bar (target {p.target_min_pairs}-{p.max_pairs}). "
            "Portfolio deliberately left short rather than filled with weak pairs."
        )
    evs = [s[0] for s in selected]
    profs = [_fp(e) for e in evs]
    for i in range(n):
        for j in range(i + 1, n):
            pts, why = pair_overlap(profs[i], profs[j])
            if pts > 0:
                w.append(f"FACTOR OVERLAP: {evs[i].name} vs {evs[j].name}: {', '.join(why)}")
    total = sum(alloc.values())
    by_cluster: dict[str, float] = {}
    for e in evs:
        c = _fp(e).cluster
        by_cluster[c] = by_cluster.get(c, 0.0) + alloc[e.name]
    for c, g in sorted(by_cluster.items()):
        if total and g / total > p.cluster_gross_warn_share and n >= 2:
            w.append(
                f"CLUSTER CONCENTRATION: '{c}' carries {g / total:.0%} of suggested gross exposure."
            )
    crowded = [c for c in by_cluster if c in CROWDED_CLUSTERS]
    if len(crowded) > 1 or any(_count(evs, "cluster").get(c, 0) > 1 for c in crowded):
        w.append(
            "CROWDED CLUSTERS: growth-beta (tech/communications/discretionary) or rate-sensitive "
            "(financials/REITs/utilities) pairs move together in macro shocks."
        )
    # net beta of the book if each leg is half of the pair's gross ceiling
    net_beta = 0.0
    known = 0
    for e in evs:
        b = e.packet.metrics.get("beta_vs_spy_60d") or {}
        bl, bs = b.get(e.long_ticker), b.get(e.short_ticker)
        if bl is None or bs is None:
            continue
        known += 1
        net_beta += alloc[e.name] / 2 * (bl - bs)
    if known and abs(net_beta) > p.net_beta_warn_pct:
        w.append(
            f"NET MARKET BETA: equal-dollar legs leave ~{net_beta:+.1f}% of portfolio value net-long-beta "
            f"(warn above {p.net_beta_warn_pct:.0f}%). Net-zero dollars is not market-neutral."
        )
    if known < n:
        w.append(f"Beta unavailable for {n - known} selected pair(s); net beta is understated.")
    for e in evs:
        flags = e.packet.risk_flags
        if any(f.startswith("UNVERIFIED_BLACKOUT") for f in flags):
            w.append(f"{e.name}: event blackout NOT manually verified; verify before entry.")
        if "BLACKOUT_OVERRIDDEN" in flags:
            w.append(f"{e.name}: included via a logged blackout override; re-read the reason.")
    w.append(
        "The 1%-of-20d-dollar-volume cap and the portfolio value are ASSUMPTIONS, not verified WSR "
        "rules; confirm limits in Trader View."
    )
    return w


def build_portfolio_from_evals(
    evals: list[PairEvaluation],
    p: EngineParams,
    week_start: date,
    week_end: date,
    now: datetime,
    run_id: str | None = None,
) -> WeeklyPortfolio:
    selected, notes = select_pairs(evals, p)
    alloc = allocate(selected, p)
    sel_names = {e.name for e, *_ in selected}
    chosen_prof = [_fp(e) for e, *_ in selected]
    clusters, drivers = (
        _count([e for e, *_ in selected], "cluster"),
        _count([e for e, *_ in selected], "driver"),
    )

    sel_out: list[SelectedPair] = []
    ordered = sorted(selected, key=lambda t: (-(t[0].score - t[1]), t[0].name))
    for i, (e, pen, why, pinned) in enumerate(ordered, 1):
        pk = e.packet.model_copy(
            update={
                "rank": i,
                "adjusted_score": round(e.score - pen, 1),
                "overlap_penalty": pen,
                "overlap_reasons": why,
                "suggested_max_gross_pct": alloc[e.name],
            }
        )
        sel_out.append(
            SelectedPair(
                rank=i, pair_id=e.pair_id, name=e.name, long_ticker=e.long_ticker,
                short_ticker=e.short_ticker, pair_quality_score=e.score,
                adjusted_score=round(e.score - pen, 1), overlap_penalty=pen, overlap_reasons=why,
                suggested_max_gross_pct=alloc[e.name], pinned_by_owner=pinned,
                factor=e.factor, packet=pk,
            )
        )  # fmt: skip

    alts: list[AlternatePair] = []
    rest = [e for e in evals if e.eligible and e.name not in sel_names]
    scored = []
    for e in rest:
        pen, _ = factor_overlap_penalty(_fp(e), chosen_prof, p)
        scored.append((e.score - pen, e, pen))
    scored.sort(key=lambda t: (-t[0], t[1].name))
    for i, (adj, e, pen) in enumerate(scored, 1):
        fp = _fp(e)
        why = []
        if len(selected) >= p.max_pairs:
            why.append(f"portfolio already holds the maximum {p.max_pairs} pairs")
        if e.score < p.min_score:
            why.append(f"score {e.score:.1f} below minimum {p.min_score:.0f}")
        if adj < p.min_adjusted_score:
            why.append(f"overlap-adjusted score {adj:.1f} below minimum {p.min_adjusted_score:.0f}")
        if clusters.get(fp.cluster, 0) >= p.max_per_cluster and fp.cluster != "unclassified":
            why.append(f"cluster '{fp.cluster}' already at limit {p.max_per_cluster}")
        if drivers.get(fp.driver, 0) >= p.max_per_driver and fp.driver != "unclassified":
            why.append(f"macro driver '{fp.driver}' already represented")
        if pen > 0:
            why.append(f"factor-overlap penalty {pen:.0f} vs selected pairs")
        alts.append(
            AlternatePair(
                rank=i, pair_id=e.pair_id, name=e.name, long_ticker=e.long_ticker,
                short_ticker=e.short_ticker, pair_quality_score=e.score,
                adjusted_score=round(adj, 1), why_not_selected=why or ["ranked below selected pairs"],
            )
        )  # fmt: skip
    excluded = [
        ExcludedPair(
            pair_id=e.pair_id, name=e.name, reasons=[r.model_dump() for r in e.ineligible_reasons]
        )
        for e in evals
        if not e.eligible
    ]
    warns = _warnings(selected, alloc, p, notes)
    summary = {
        "pairs_evaluated": len(evals),
        "pairs_eligible": sum(e.eligible for e in evals),
        "pairs_selected": len(sel_out),
        "pairs_excluded": len(excluded),
        "total_suggested_gross_pct": round(sum(alloc.values()), 1),
        "clusters": clusters,
        "drivers": drivers,
        "short_of_target": len(sel_out) < p.target_min_pairs,
    }
    return WeeklyPortfolio(
        run_id=run_id or str(uuid.uuid4()), week_start=week_start, week_end=week_end,
        built_at=now, params=p.model_dump(), selected=sel_out, alternates=alts, excluded=excluded,
        warnings=warns, summary=summary,
    )  # fmt: skip


def compute_evaluations(
    store: Store, now: datetime, p: EngineParams, week_start: date | None = None
) -> tuple[list[PairEvaluation], date, date]:
    inputs, start, end = load_week_inputs(store, now, week_start)
    return PeerPairEngine(p).evaluate_all(inputs), start, end


def build_weekly_portfolio(
    store: Store,
    now: datetime,
    p: EngineParams | None = None,
    week_start: date | None = None,
    db: "DB | None" = None,
) -> WeeklyPortfolio:
    """Evaluate every active pair and select the weekly draft. Persists only if `db` is given."""
    p = p or EngineParams()
    evals, start, end = compute_evaluations(store, now, p, week_start)
    pf = build_portfolio_from_evals(evals, p, start, end, now)
    if db is not None:
        persist(db, pf, evals)
    return pf


def persist(db: "DB", pf: WeeklyPortfolio, evals: list[PairEvaluation]) -> None:
    from app.services.pairs.review import ResearchWriteGuard

    g = ResearchWriteGuard(db)
    sel = {s.name: s for s in pf.selected}
    alt = {a.name: a for a in pf.alternates}
    ordered = [s.name for s in pf.selected] + [a.name for a in pf.alternates]
    ordered += [e.name for e in evals if not e.eligible]
    rank_of = {n: i for i, n in enumerate(ordered, 1)}
    rows = []
    for e in evals:
        if not e.pair_id:
            continue
        s, a = sel.get(e.name), alt.get(e.name)
        rows.append(
            {
                "run_id": pf.run_id, "pair_id": e.pair_id, "ranked_at": pf.built_at,
                "rank": rank_of.get(e.name, len(ordered) + 1), "score": e.score,
                "direction_hint": "NO_TRADE",  # research hint only; never a signal
                "correlation_60d": e.packet.metrics.get("correlation_60d"),
                "liquidity_ok": not any(r.code in ("INSUFFICIENT_LIQUIDITY", "LIQUIDITY_UNKNOWN") for r in e.ineligible_reasons),
                "components": {"components": e.components, "penalties": e.penalties},
                "warnings": e.packet.risk_flags,
                "data_status": "AVAILABLE" if e.data_quality_score >= 85 else "UNVERIFIED",
                "week_start": pf.week_start, "long_security_id": e.long_security_id,
                "short_security_id": e.short_security_id, "eligible": e.eligible,
                "ineligible_reasons": [r.model_dump() for r in e.ineligible_reasons],
                "data_quality_score": e.data_quality_score,
                "adjusted_score": s.adjusted_score if s else (a.adjusted_score if a else None),
                "packet": (s.packet if s else e.packet).model_dump(mode="json"),
            }
        )  # fmt: skip
    g.upsert("pair_rankings", rows, "run_id,pair_id")
    g.upsert(
        "weekly_portfolios",
        [
            {
                "week_start": pf.week_start, "run_id": pf.run_id, "built_at": pf.built_at,
                "status": "RESEARCH_DRAFT", "params": pf.params,
                "selected": [s.model_dump(mode="json") for s in pf.selected],
                "alternates": [a.model_dump(mode="json") for a in pf.alternates],
                "warnings": pf.warnings, "summary": pf.summary,
            }
        ],
        "run_id",
    )  # fmt: skip


def render_text(pf: WeeklyPortfolio) -> str:
    L = [
        f"WEEKLY PEER-PAIR PORTFOLIO  week {pf.week_start} .. {pf.week_end}   [{pf.status}]",
        pf.note,
        "",
    ]
    if not pf.selected:
        L.append("No pairs selected.")
    for s in pf.selected:
        pk = s.packet
        L.append(f"#{s.rank}  LONG {s.long_ticker} / SHORT {s.short_ticker}   score {s.pair_quality_score:.1f}  adj {s.adjusted_score:.1f}  max gross {s.suggested_max_gross_pct:.1f}%{'  [owner-pinned]' if s.pinned_by_owner else ''}")  # fmt: skip
        L.append(f"    driver: {s.factor.get('driver')} | cluster: {s.factor.get('cluster')} | blackout: {pk.event_blackout.get('status')} | review: {pk.review_cadence}")  # fmt: skip
        for r in pk.top_reasons:
            L.append(f"    + {r}")
        L.append(f"    - counter: {pk.counter_thesis[0] if pk.counter_thesis else 'n/a'}")
        for z in pk.entry_zones:
            L.append(f"    zone {z.side} {z.ticker}: ref {z.reference_price} band {z.band_low}-{z.band_high} (not an instruction)")  # fmt: skip
        if s.overlap_reasons:
            L.append(f"    overlap -{s.overlap_penalty:.0f}: {'; '.join(s.overlap_reasons)}")
        L.append("")
    if pf.alternates:
        L.append("ALTERNATES")
        for a in pf.alternates:
            L.append(f"  {a.rank}. {a.long_ticker}/{a.short_ticker} score {a.pair_quality_score:.1f} adj {a.adjusted_score:.1f}: {'; '.join(a.why_not_selected)}")  # fmt: skip
        L.append("")
    if pf.excluded:
        L.append("EXCLUDED (hard eligibility)")
        for x in pf.excluded:
            L.append(
                f"  {x.name}: " + " | ".join(f"{r['code']}: {r['message']}" for r in x.reasons)
            )
        L.append("")
    L.append("WARNINGS")
    L += [f"  ! {w}" for w in pf.warnings]
    L.append(f"\nSummary: {pf.summary}")
    return "\n".join(L)
