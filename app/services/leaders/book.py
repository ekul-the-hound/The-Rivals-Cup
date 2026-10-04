"""Weekly leaders & laggards book: per sector, the strongest stock (long candidate) and the weakest
of its direct competitors (short candidate). Five longs and five shorts at most.

Read-only research. The book is a candidate list with reasons, flags and sizing *estimates*. It never
places, queues or records a trade; you decide, and you enter anything yourself in Trader View.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.db.store import Store, select_all
from app.models.universe import DISCLAIMER, TARGET_SECTORS, SecurityType
from app.services.deep_dive.packet import analyst_score
from app.services.leaders import MARKET_BENCHMARK, SECTOR_ETF
from app.services.leaders.metrics import (
    Series,
    compute_metrics,
    pair_correlation,
    series_from_row,
)
from app.services.leaders.ranking import (
    context_adjustment,
    long_penalties,
    short_penalties,
    strength_scores,
)
from app.services.pairs.blackout import scoring_week
from app.services.universe.repo import load_universe

STRATEGY = (
    "In each target sector: long the strongest stock, short the weakest of its direct competitors. "
    "Research candidates only; every trade is entered by hand in Trader View."
)
SIZE_BAND = (0.1, 10.0)  # fallback competitors must trade within 0.1x-10x of the leader's $ volume
RECENT_SPLIT_DAYS = 30


@dataclass
class BookParams:
    week_start: date
    week_end: date
    min_adv_usd: float = 5_000_000.0
    min_price: float = 5.0
    min_bars: int = 120
    max_stale_days: int = 7
    min_competitors: int = 3
    candidates_per_sector: int = 8
    min_strength_gap: float = 15.0
    portfolio_usd: float = 1_000_000.0
    gross_target_pct: float = 100.0
    adv_cap_pct: float = 0.01
    max_leg_pct: float = 15.0  # no single leg above this % of portfolio value

    @classmethod
    def from_settings(cls, s: Any, today: date) -> BookParams:
        ws, we = scoring_week(today)
        return cls(
            week_start=ws, week_end=we,
            min_adv_usd=s.leaders_min_adv_usd, min_price=s.leaders_min_price,
            min_bars=s.leaders_min_history_bars, min_competitors=s.leaders_min_competitors,
            candidates_per_sector=s.leaders_candidates_per_sector,
            min_strength_gap=s.leaders_min_strength_gap,
            portfolio_usd=s.leaders_portfolio_usd,
            gross_target_pct=min(s.leaders_gross_target_pct, 180.0),
            adv_cap_pct=s.wsr_est_adv_pct_cap, max_leg_pct=s.leaders_max_leg_pct,
        )  # fmt: skip


@dataclass
class Prepared:
    params: BookParams
    today: date
    rows: dict[str, dict[str, Any]]
    series: dict[str, Series]
    metrics: dict[str, dict[str, Any]]
    blocked: set[str]
    sectors: dict[str, dict[str, Any]]
    screened_out: dict[str, int] = field(default_factory=dict)


def _chunks(xs: list[Any], n: int) -> list[list[Any]]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def fetch_for_tickers(
    store: Store, table: str, tickers: list[str], chunk: int = 100, **kw: Any
) -> list[dict[str, Any]]:  # fmt: skip
    out: list[dict[str, Any]] = []
    for ch in _chunks(sorted(set(tickers)), chunk):
        out += store.select(table, in_={"ticker": ch}, limit=5000, **kw)
    return out


def tradable_names(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r.get("sector") not in TARGET_SECTORS or not r.get("is_active", True):
            continue
        if r.get("security_type") not in (SecurityType.COMMON_STOCK, SecurityType.ADR):
            continue
        if r.get("is_test_issue"):
            continue
        reasons = set(r.get("exclusion_reasons") or [])
        if r.get("in_pair_research_eligible") or reasons == {"EVENT_BLACKOUT"}:
            out.append(r)
    return out


def prepare(store: Store, today: date, params: BookParams) -> Prepared:
    """Load histories, screen the population and score strength within each sector."""
    universe = load_universe(store)
    names = tradable_names(universe)
    hist = {h["ticker"]: h for h in select_all(store, "universe_price_history")}
    series: dict[str, Series] = {}
    for tk, h in hist.items():
        s = series_from_row(h)
        if s:
            series[tk] = s
    market = series.get(MARKET_BENCHMARK)
    metrics: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict[str, Any]] = {}
    blocked: set[str] = set()
    screened: dict[str, int] = {}

    def drop(reason: str) -> None:
        screened[reason] = screened.get(reason, 0) + 1

    for r in names:
        tk = r["ticker"]
        s = series.get(tk)
        if s is None:
            drop("NO_PRICE_HISTORY")
            continue
        m = compute_metrics(s, market, series.get(SECTOR_ETF[r["sector"]]), today)
        adv = float(r.get("average_dollar_volume_20d") or m.get("adv_usd_20d_hist") or 0)
        m["adv_usd"] = adv
        if m["n_bars"] < params.min_bars:
            drop("SHORT_HISTORY")
        elif m["stale_days"] > params.max_stale_days:
            drop("STALE_PRICES")
        elif adv < params.min_adv_usd:
            drop("BELOW_MIN_ADV")
        elif m["last_close"] < params.min_price:
            drop("BELOW_MIN_PRICE")
        elif m["days_since_split"] is not None and m["days_since_split"] <= RECENT_SPLIT_DAYS:
            drop("RECENT_SPLIT")
        else:
            metrics[tk] = m
            rows[tk] = r
            if not r.get("in_pair_research_eligible"):
                blocked.add(tk)
    sectors: dict[str, dict[str, Any]] = {}
    for sec in TARGET_SECTORS:
        pop = {t: m for t, m in metrics.items() if rows[t]["sector"] == sec}
        st = strength_scores(pop)
        for t, v in st.items():
            metrics[t]["strength"] = v["strength"]
            metrics[t]["strength_components"] = v["components"]
        ranked = sorted(
            (t for t in pop if st[t]["strength"] is not None),
            key=lambda t: st[t]["strength"] - long_penalties(pop[t])[0],
            reverse=True,
        )
        prelim = [t for t in ranked if t not in blocked][: params.candidates_per_sector]
        sectors[sec] = {
            "population": len(pop),
            "ranked": ranked,
            "prelim": prelim,
            "blocked_top": [t for t in ranked[:5] if t in blocked],
        }
    return Prepared(params, today, rows, series, metrics, blocked, sectors, screened)


def competitor_candidates(
    prep: Prepared, leader: str, mapped: dict[str, list[str]]
) -> tuple[list[tuple[str, str]], int]:  # fmt: skip
    """Same-sector tradable competitors of `leader` as (ticker, source). Finnhub peers come first;
    same-SEC-industry names of similar size fill in only when there are too few mapped peers."""
    sec = prep.rows[leader]["sector"]
    chosen: list[tuple[str, str]] = []
    other_sector = 0
    for p in mapped.get(leader, []):
        if p == leader:
            continue
        if p in prep.rows and prep.rows[p]["sector"] == sec:
            chosen.append((p, "FINNHUB_PEERS"))
        elif p in prep.rows or p in prep.series:
            other_sector += 1
    if len([c for c in chosen if c[0] not in prep.blocked]) < prep.params.min_competitors:
        ind = prep.rows[leader].get("industry")
        adv = prep.metrics[leader]["adv_usd"]
        have = {c[0] for c in chosen}
        extra = []
        for t, r in prep.rows.items():
            if (
                t == leader
                or t in have
                or r["sector"] != sec
                or not ind
                or r.get("industry") != ind
            ):
                continue
            a = prep.metrics[t]["adv_usd"]
            if adv and SIZE_BAND[0] <= a / adv <= SIZE_BAND[1]:
                extra.append((abs(a - adv), t))
        chosen += [(t, "SEC_INDUSTRY_SIZE_MATCH") for _, t in sorted(extra)[:12]]
    return chosen, other_sector


def load_competitor_map(store: Store, leaders: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in fetch_for_tickers(store, "competitor_map", leaders, chunk=50):
        out.setdefault(r["ticker"], []).append(r["peer"])
    return out


def load_context(store: Store, tickers: list[str], today: date) -> dict[str, dict[str, Any]]:
    ctx: dict[str, dict[str, Any]] = {t: {} for t in tickers}
    si_cut = date.fromordinal(today.toordinal() - 45)
    for r in sorted(
        fetch_for_tickers(store, "short_interest", tickers, gte={"settlement_date": si_cut}),
        key=lambda x: str(x["settlement_date"]),
    ):
        ctx[r["ticker"]].update(
            days_to_cover=r.get("days_to_cover"),
            short_interest_change_pct=r.get("change_percent"),
            short_interest_date=str(r["settlement_date"]),
        )
    sv_cut = date.fromordinal(today.toordinal() - 8)
    ratios: dict[str, list[float]] = {}
    for r in sorted(
        fetch_for_tickers(
            store, "short_sale_volume_daily", tickers, chunk=60, gte={"trade_date": sv_cut}
        ),
        key=lambda x: str(x["trade_date"]),
    ):
        if r.get("short_volume_ratio") is not None:
            ratios.setdefault(r["ticker"], []).append(float(r["short_volume_ratio"]))
    for t, v in ratios.items():
        ctx[t]["short_volume_ratio_5d"] = round(sum(v[-5:]) / len(v[-5:]), 4)
    ar_cut = date.fromordinal(today.toordinal() - 120)
    latest_ar: dict[str, dict[str, Any]] = {}
    for r in sorted(
        fetch_for_tickers(store, "analyst_recommendations", tickers, gte={"period": ar_cut}),
        key=lambda x: str(x["period"]),
    ):
        latest_ar[r["ticker"]] = r
    for t, r in latest_ar.items():
        ctx[t]["analyst_score"] = analyst_score(r)
    for r in fetch_for_tickers(store, "company_fundamentals", tickers):
        ctx[r["ticker"]]["revenue_growth_pct"] = r.get("revenue_growth_pct")
    return ctx


def _pct(v: float | None, nd: int = 1) -> str:
    return "n/a" if v is None else f"{v:+.{nd}f}%"


def _leg(
    tk: str, prep: Prepared, ctx: dict[str, Any], role: str, score: float, pen_text: list[str],
    flags: list[str],
) -> dict[str, Any]:  # fmt: skip
    m, r = prep.metrics[tk], prep.rows[tk]
    why = (
        f"strength {m['strength']:.0f}/100 in sector: 20d {_pct(m['ret_20d'])}, 60d {_pct(m['ret_60d'])}, "
        f"120d {_pct(m['ret_120d'])}; {_pct(m['ma50_dist'])} vs 50-day average; "
        f"downside dev {m['downside_dev_60d']:.2f}%/day"
        if m.get("downside_dev_60d") is not None
        else f"strength {m['strength']:.0f}/100 in sector"
    )
    return {
        "role": role,
        "ticker": tk,
        "company_name": r.get("company_name"),
        "industry": r.get("industry"),
        "exchange": r.get("exchange"),
        "last_price": round(m["last_close"], 2),
        "adv_usd_20d": round(m["adv_usd"]),
        "strength": m["strength"],
        "score": round(score, 2),
        "why": why,
        "penalties": pen_text,
        "flags": flags,
        "metrics": {
            k: (round(m[k], 2) if isinstance(m.get(k), float) else m.get(k))
            for k in (
                "ret_5d",
                "ret_20d",
                "ret_60d",
                "ret_120d",
                "ma50_dist",
                "pos_52w",
                "vol_60d",
                "downside_dev_60d",
                "max_dd_60d",
                "beta_60d",
                "rel_60d_vs_sector",
                "div_yield_pct",
                "next_ex_dividend_est",
            )
        },  # fmt: skip
        "context": {k: v for k, v in ctx.items() if v is not None},
        "earnings_date": str(r.get("earnings_date_if_known") or "") or None,
        "tradable_status_manual_note": r.get("competition_tradable_status"),
    }


def _earnings_flag(leg: dict[str, Any], params: BookParams) -> str | None:
    d = leg.get("earnings_date")
    if not d:
        return None
    dd = date.fromisoformat(d)
    if params.week_end < dd <= date.fromordinal(params.week_end.toordinal() + 14):
        return f"{leg['ticker']}: earnings {d} is within two weeks after the scoring week"
    return None


def select_pair(
    prep: Prepared, sector: str, ctx: dict[str, dict[str, Any]], mapped: dict[str, list[str]]
) -> dict[str, Any]:  # fmt: skip
    p = prep.params
    info = prep.sectors[sector]
    scored_leaders = []
    for t in info["prelim"]:
        pen, text, flags = long_penalties(prep.metrics[t])
        sc = prep.metrics[t]["strength"] - pen + context_adjustment(ctx.get(t, {}), "long")
        scored_leaders.append((sc, t, text, flags))
    scored_leaders.sort(reverse=True)
    attempts: list[dict[str, Any]] = []
    for sc, lt, ltext, lflags in scored_leaders:
        comps, other = competitor_candidates(prep, lt, mapped)
        cands = []
        for ct, src in comps:
            if ct in prep.blocked or ct not in prep.metrics:
                continue
            cm = prep.metrics[ct]
            if cm.get("strength") is None:
                continue
            pen, text, flags = short_penalties(cm, ctx.get(ct, {}), (p.week_start, p.week_end))
            weak = 100 - cm["strength"] - pen + context_adjustment(ctx.get(ct, {}), "short")
            cands.append((weak, ct, text, flags, src))
        cands.sort(reverse=True)
        attempts.append(
            {"leader": (sc, lt, ltext, lflags), "cands": cands, "other_sector_peers": other}
        )
    usable = [a for a in attempts if len(a["cands"]) >= p.min_competitors]
    if not usable:
        why = "no strong stock had enough comparable, tradable competitors"
        if not info["prelim"]:
            why = "no tradable names with enough price history"
        return {"sector": sector, "pair": None, "reason": why}
    chosen = next(
        (
            a for a in usable
            if prep.metrics[a["leader"][1]]["strength"] - prep.metrics[a["cands"][0][1]]["strength"]
            >= p.min_strength_gap
        ),
        usable[0],
    )  # fmt: skip
    sc, lt, ltext, lflags = chosen["leader"]
    weak, ct, ctext, cflags, src = chosen["cands"][0]
    lleg = _leg(lt, prep, ctx.get(lt, {}), "LONG_CANDIDATE", sc, ltext, lflags)
    sleg = _leg(ct, prep, ctx.get(ct, {}), "SHORT_CANDIDATE", weak, ctext, cflags)
    gap = prep.metrics[lt]["strength"] - prep.metrics[ct]["strength"]
    warnings: list[str] = []
    if gap < p.min_strength_gap:
        warnings.append(
            f"WEAK_SPREAD: strength gap {gap:.0f} points is below the {p.min_strength_gap:.0f}-point target"
        )
    if src != "FINNHUB_PEERS":
        warnings.append("COMPETITOR_SOURCE: same-SEC-industry size match, not a Finnhub peer list")
    if chosen["other_sector_peers"]:
        warnings.append(
            f"{chosen['other_sector_peers']} peer(s) of {lt} sit outside {sector} and were not considered"
        )
    for leg in (lleg, sleg):
        w = _earnings_flag(leg, p)
        if w:
            warnings.append(w)
        for f in leg["flags"]:
            warnings.append(f"{leg['ticker']}: {f}")
    corr = (
        pair_correlation(prep.series[lt], prep.series[ct])
        if lt in prep.series and ct in prep.series
        else None
    )
    if corr is not None and corr < 0.3:
        warnings.append(f"LOW_CORRELATION: 60d correlation {corr:.2f}; they may not move together")
    lm, sm = prep.metrics[lt], prep.metrics[ct]
    bl, bs = lm.get("beta_60d"), sm.get("beta_60d")
    alt_longs = [
        {"ticker": t, "score": round(s, 2), "strength": prep.metrics[t]["strength"]}
        for s, t, *_ in scored_leaders
        if t not in (lt, ct)
    ][:3]
    alt_shorts = [
        {"ticker": t, "score": round(w, 2), "strength": prep.metrics[t]["strength"], "source": s}
        for w, t, _, _, s in chosen["cands"][1:4]
    ]
    blocked = [{"ticker": t, "strength": prep.metrics[t]["strength"]} for t in info["blocked_top"]]
    return {
        "sector": sector,
        "pair": {
            "long": lleg,
            "short": sleg,
            "competitor_source": src,
            "stats": {
                "strength_gap": round(gap, 1),
                "spread_20d": None
                if None in (lm["ret_20d"], sm["ret_20d"])
                else round(lm["ret_20d"] - sm["ret_20d"], 2),
                "spread_60d": None
                if None in (lm["ret_60d"], sm["ret_60d"])
                else round(lm["ret_60d"] - sm["ret_60d"], 2),
                "correlation_60d": None if corr is None else round(corr, 3),
                "beta_long": None if bl is None else round(bl, 2),
                "beta_short": None if bs is None else round(bs, 2),
            },  # fmt: skip
            "alternates": {"longs": alt_longs, "shorts": alt_shorts},
            "blocked_by_event_blackout": blocked,
            "warnings": warnings,
        },
    }


def size_pairs(pairs: list[dict[str, Any]], params: BookParams) -> dict[str, Any]:
    """Dollar-neutral estimates. Not instructions: Trader View's real limits govern."""
    n = len(pairs)
    long_total = short_total = net_beta = 0.0
    if n:
        target = params.portfolio_usd * params.gross_target_pct / 100 / (2 * n)
    for pr in pairs:
        lg, sg = pr["long"], pr["short"]
        cap_l, cap_s = (
            params.adv_cap_pct * lg["adv_usd_20d"],
            params.adv_cap_pct * sg["adv_usd_20d"],
        )
        leg = min(target, cap_l, cap_s, params.portfolio_usd * params.max_leg_pct / 100)
        bl, bs = pr["stats"]["beta_long"], pr["stats"]["beta_short"]
        neutral = None
        if bl and bs:
            neutral = round(leg * min(max(bl / bs, 0.5), 1.5), 2)
        pr["sizing_estimate"] = {
            "leg_usd_dollar_neutral": round(leg, 2),
            "short_usd_if_beta_neutral": neutral,
            "liquidity_cap_binding": leg
            < min(target, params.portfolio_usd * params.max_leg_pct / 100),
            "est_liquidity_cap_usd": {"long": round(cap_l), "short": round(cap_s)},
            "note": "ESTIMATE using the 1% of 20-day dollar volume assumption; Trader View's real limits govern.",
        }
        if leg < min(target, params.portfolio_usd * params.max_leg_pct / 100):
            pr["warnings"].append(
                f"LIQUIDITY_CAP: legs limited to ${leg:,.0f} (target ${target:,.0f}) by the 1% of 20d $ volume estimate"
            )
        long_total += leg
        short_total += leg
        if bl is not None and bs is not None:
            net_beta += (leg * bl - leg * bs) / params.portfolio_usd
    return {
        "long_usd": round(long_total, 2),
        "short_usd": round(short_total, 2),
        "gross_pct_of_portfolio": round((long_total + short_total) / params.portfolio_usd * 100, 2),
        "net_pct_of_portfolio": round((long_total - short_total) / params.portfolio_usd * 100, 2),
        "estimated_net_beta": round(net_beta, 3),
        "gross_limit_pct": 200,
    }


def build_book(
    store: Store, today: date, params: BookParams, prep: Prepared | None = None
) -> dict[str, Any]:  # fmt: skip
    prep = prep or prepare(store, today, params)
    leaders = [t for s in prep.sectors.values() for t in s["prelim"]]
    mapped = load_competitor_map(store, leaders)
    need = set(leaders)
    for lt in leaders:
        need |= {c for c, _ in competitor_candidates(prep, lt, mapped)[0]}
    ctx = load_context(store, sorted(need), today)
    results = [select_pair(prep, sec, ctx, mapped) for sec in TARGET_SECTORS]
    pairs = [r["pair"] for r in results if r["pair"]]
    for r in results:
        if r["pair"]:
            r["pair"]["sector"] = r["sector"]
    missing = [{"sector": r["sector"], "reason": r["reason"]} for r in results if not r["pair"]]
    book = size_pairs(pairs, params)
    warnings = [
        "Research ranking only. Not a recommendation and not a trade instruction.",
        "Verify every ticker is available (and shortable) in Trader View before relying on it.",
    ]
    if missing:
        warnings.append(f"{len(missing)} sector(s) have no valid pair this week")
    if abs(book["estimated_net_beta"]) > 0.05:
        warnings.append(f"Estimated net beta {book['estimated_net_beta']:+.2f} of portfolio value")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": STRATEGY,
        "disclaimer": DISCLAIMER,
        "scoring_week": {
            "start": params.week_start.isoformat(),
            "end": params.week_end.isoformat(),
        },
        "params": {
            k: (v.isoformat() if isinstance(v, date) else v) for k, v in vars(params).items()
        },
        "pairs": pairs,
        "sectors_without_pair": missing,
        "book": book,
        "screened_out_counts": prep.screened_out,
        "population_by_sector": {s: v["population"] for s, v in prep.sectors.items()},
        "warnings": warnings,
    }


def render_markdown(b: dict[str, Any]) -> str:
    w = b["scoring_week"]
    lines = [
        f"# Leaders & laggards book: scoring week {w['start']} to {w['end']}",
        f"_{b['strategy']}_",
        "",
    ]
    for pr in b["pairs"]:
        lg, sg, st = pr["long"], pr["short"], pr["stats"]
        lines += [
            f"## {pr['sector']}",
            f"- **LONG candidate {lg['ticker']}** ({lg['company_name']}) ${lg['last_price']}: {lg['why']}",
            f"- **SHORT candidate {sg['ticker']}** ({sg['company_name']}) ${sg['last_price']}: {sg['why']}",
            f"- Competitor source {pr['competitor_source']}; strength gap {st['strength_gap']}, 60d spread {st['spread_60d']}%, "
            f"60d correlation {st['correlation_60d']}, beta {st['beta_long']} vs {st['beta_short']}",
        ]
        z = pr.get("sizing_estimate")
        if z:
            lines.append(
                f"- Size estimate (dollar-neutral): ${z['leg_usd_dollar_neutral']:,.0f} per leg; "
                f"beta-neutral short ${z['short_usd_if_beta_neutral']}"
            )
        ctxs = []
        for leg in (lg, sg):
            c = leg["context"]
            bits = [f"{k}={v}" for k, v in c.items()]
            if leg["earnings_date"]:
                bits.append(f"earnings={leg['earnings_date']}")
            ctxs.append(
                f"{leg['ticker']}: " + (", ".join(bits) if bits else "no extra context yet")
            )
        lines.append("- Context: " + " | ".join(ctxs))
        alts = pr["alternates"]
        lines.append(
            "- Alternates: longs "
            + (", ".join(a["ticker"] for a in alts["longs"]) or "none")
            + "; shorts "
            + (", ".join(a["ticker"] for a in alts["shorts"]) or "none")
        )
        for x in pr["warnings"]:
            lines.append(f"  - ! {x}")
        lines.append("")
    for m in b["sectors_without_pair"]:
        lines.append(f"## {m['sector']}\n- No pair this week: {m['reason']}\n")
    bk = b["book"]
    lines += [
        "## Book totals (estimates)",
        f"- Long ${bk['long_usd']:,.0f} / short ${bk['short_usd']:,.0f}; gross {bk['gross_pct_of_portfolio']}% "
        f"(limit {bk['gross_limit_pct']}%), net {bk['net_pct_of_portfolio']}%, est. net beta {bk['estimated_net_beta']}",
        "",
    ]
    lines += [f"- {x}" for x in b["warnings"]]
    lines.append(f"\n_{b['disclaimer']}_")
    return "\n".join(lines)
