"""Pair inspection: read-only research summary for a LONG leg and a SHORT leg (manual entry)."""

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.config import Settings
from app.db.store import Store
from app.models.enums import DataStatus
from app.services.features.metrics import (
    adv_dollar,
    adv_shares,
    close_series,
    correlation,
    estimate_liquidity_cap,
    relative,
    trailing_returns,
)  # fmt: skip
from app.services.pairs.blackout import evaluate_leg, evaluate_pair, scoring_week
from app.services.validation.freshness import age_status, price_status


class PairInspection(BaseModel):
    as_of: datetime
    long_ticker: str
    short_ticker: str
    relationship: list[str]
    returns: dict[str, dict[int, float | None]]
    spread_returns_long_minus_short: dict[int, float | None]
    vs_sector_etf: dict[str, dict[int, float | None]]
    vs_spy: dict[str, dict[int, float | None]]
    correlation_60d: float | None
    correlation_obs: int
    market: dict[str, dict[str, Any]]
    blackout: dict[str, Any]
    catalysts: dict[str, list[dict[str, Any]]]
    news: dict[str, list[dict[str, Any]]]
    macro: dict[str, Any]
    sector_context: dict[str, Any]
    missing_or_stale: list[str]
    liquidity_warnings: list[str]


class PairNotFound(ValueError):
    pass


def _bars(store: Store, sid: str, limit: int = 130):
    rows = store.select(
        "market_bars",
        eq={"security_id": sid, "timeframe": "1D"},
        order="bar_date",
        desc=True,
        limit=limit,
    )
    return sorted(rows, key=lambda r: str(r["bar_date"]))


def inspect_pair(
    store: Store,
    settings: Settings,
    long_t: str,
    short_t: str,
    now: datetime,
    size_usd: float | None = None,
) -> PairInspection:
    today = now.date()
    size = size_usd or settings.default_leg_size_usd
    secs = {s["ticker"]: s for s in store.select("securities")}
    for t in (long_t.upper(), short_t.upper()):
        if t not in secs:
            raise PairNotFound(f"{t} is not in the securities table")
    L, S = secs[long_t.upper()], secs[short_t.upper()]
    legs = {"LONG": L, "SHORT": S}
    comps = {c["security_id"]: c for c in store.select("companies")}
    etf_by_sector = {m["sector"]: m["etf_security_id"] for m in store.select("sector_etf_mappings")}
    id_to_t = {s["id"]: s["ticker"] for s in secs.values()}
    issues: list[str] = []

    # --- relationship ---
    rel: list[str] = []
    same_sector = bool(L.get("sector")) and L.get("sector") == S.get("sector")
    rel.append(
        f"Sectors: {L['ticker']}={L.get('sector')}, {S['ticker']}={S.get('sector')} ({'same sector' if same_sector else 'DIFFERENT sectors'})"
    )
    pair_row = None
    members = store.select("peer_pair_members")
    by_pair: dict[str, set[str]] = {}
    for m in members:
        by_pair.setdefault(m["pair_id"], set()).add(m["security_id"])
    for p in store.select("peer_pairs"):
        if by_pair.get(p["id"]) == {L["id"], S["id"]}:
            pair_row = p
    rel.append(
        f"Curated pair: {pair_row['name']} (status {pair_row['status']})"
        if pair_row
        else "Curated pair: NOT in peer_pairs (ad-hoc combination)"
    )
    etf_id = etf_by_sector.get(L.get("sector") or "")
    etf_tk = id_to_t.get(etf_id) if etf_id else None
    rel.append(f"Sector ETF reference: {etf_tk or 'none mapped'}")
    for lbl, s in legs.items():
        c = comps.get(s["id"], {})
        if c.get("wiki_industry") or c.get("wiki_summary"):
            rel.append(
                f"{lbl} {s['ticker']} (Wikipedia, static context): industry={c.get('wiki_industry')}; {str(c.get('wiki_summary') or '')[:140]}"
            )
        else:
            issues.append(f"{s['ticker']}: no Wikipedia company context")

    # --- prices / returns ---
    bars = {k: _bars(store, s["id"]) for k, s in legs.items()}
    etf_bars = _bars(store, etf_id) if etf_id else []
    spy = secs.get("SPY")
    spy_bars = _bars(store, spy["id"]) if spy else []
    closes = {k: close_series(b) for k, b in bars.items()}
    rets = {
        s["ticker"]: trailing_returns(closes[k])
        if len(closes[k])
        else dict.fromkeys((1, 5, 20, 60))
        for k, s in legs.items()
    }
    spread = relative(rets[L["ticker"]], rets[S["ticker"]])
    etf_r = trailing_returns(close_series(etf_bars)) if etf_bars else {}
    spy_r = trailing_returns(close_series(spy_bars)) if spy_bars else {}
    vs_etf = {t: relative(r, etf_r) if etf_r else dict.fromkeys(r) for t, r in rets.items()}
    vs_spy = {t: relative(r, spy_r) if spy_r else dict.fromkeys(r) for t, r in rets.items()}
    corr, n = correlation(closes["LONG"], closes["SHORT"])
    if corr is None:
        issues.append(f"correlation unavailable ({n} overlapping returns)")

    # --- market / liquidity ---
    market, liq_warn = {}, []
    for k, s in legs.items():
        b = bars[k]
        tk = s["ticker"]
        last = b[-1] if b else None
        st = price_status(date.fromisoformat(str(last["bar_date"])[:10]) if last else None, today)
        if st != DataStatus.AVAILABLE:
            issues.append(
                f"{tk}: prices {st.value}" + (f" (latest {last['bar_date']})" if last else "")
            )
        liq = store.select(
            "liquidity_metrics", eq={"security_id": s["id"]}, order="as_of_date", desc=True, limit=1
        )
        adv = adv_dollar(b)
        cap = estimate_liquidity_cap(adv, settings.wsr_est_adv_pct_cap)
        if liq and age_status(liq[0]["as_of_date"], "liquidity", now) == DataStatus.STALE:
            issues.append(f"{tk}: liquidity metric stale ({liq[0]['as_of_date']})")
        c = comps.get(s["id"], {})
        if not c.get("market_cap_usd"):
            issues.append(f"{tk}: market cap unavailable")
        market[tk] = {
            "last_close": last.get("close") if last else None, "last_bar_date": last["bar_date"] if last else None,
            "last_volume": last.get("volume") if last else None, "adv_20d_usd": round(adv, 0) if adv else None,
            "adv_20d_shares": round(adv_shares(b) or 0) or None, "est_leg_cap_usd": cap, "market_cap_usd": c.get("market_cap_usd"),
        }  # fmt: skip
        if cap is None:
            liq_warn.append(f"{tk}: cannot estimate liquidity cap (insufficient volume data)")
        elif size > cap:
            liq_warn.append(
                f"{tk}: planned ${size:,.0f} EXCEEDS estimated cap ${cap:,.0f} ({settings.wsr_est_adv_pct_cap:.1%} of 20d ADV ${adv:,.0f})"
            )
    liq_warn.append(
        "Estimate only: WSR's actual liquidity/exposure limits are NOT verified; check Trader View before entry."
    )

    # --- blackout ---
    start, end = scoring_week(today)
    rows = {
        r["security_id"]: r
        for r in store.select("security_event_blackouts", eq={"week_start": start})
    }
    leg_results = [evaluate_leg(s["ticker"], rows.get(s["id"]), start, end, now) for s in (L, S)]
    ov = None
    if pair_row:
        ovs = store.select(
            "pair_blackout_overrides", eq={"pair_id": pair_row["id"], "week_start": start}, limit=1
        )
        ov = ovs[0] if ovs else None
    elig = evaluate_pair(leg_results, ov)
    for leg in leg_results:
        issues += [
            w
            for w in leg.warnings
            if "never manually verified" in w or "older than" in w or "no blackout row" in w
        ]
    blackout = {
        "week": f"{start} to {end}", "eligible_for_default_monday_list": elig.eligible,
        "blocked_reasons": elig.blocked_reasons, "override_reason": elig.override_reason,
        "warnings": elig.warnings, "status": "CLEAR" if elig.eligible and not elig.blocked_reasons else ("OVERRIDDEN" if elig.eligible else "BLACKOUT"),
    }  # fmt: skip

    # --- catalysts / news ---
    cats, news = {}, {}
    for s in (L, S):
        cs = store.select(
            "corporate_catalysts",
            eq={"security_id": s["id"]},
            gte={"event_date": today - timedelta(days=30)},
            order="event_date",
            desc=True,
            limit=8,
        )
        cats[s["ticker"]] = [
            {
                "type": c["catalyst_type"],
                "headline": c["headline"],
                "date": c["event_date"],
                "source": c.get("source_url"),
            }
            for c in cs
        ]
        ns = store.select(
            "news_items", eq={"security_id": s["id"]}, order="published_at", desc=True, limit=5
        )
        news[s["ticker"]] = [
            {
                "headline": x["headline"],
                "publisher": x.get("publisher_name"),
                "published_at": x.get("published_at"),
                "quality": x.get("evidence_quality"),
                "confirmed": x.get("link_confirmed"),
            }
            for x in ns
        ]
        if not ns:
            issues.append(f"{s['ticker']}: no recent news items stored (security-specific)")

    # --- macro / sector context ---
    macro_rows = store.select("macro_context_snapshots", order="as_of_date", desc=True, limit=1)
    macro = (
        {
            k: macro_rows[0].get(k)
            for k in (
                "as_of_date",
                "dgs2",
                "dgs10",
                "yield_curve_10y2y",
                "fed_funds",
                "vix",
                "regime",
                "data_status",
            )
        }
        if macro_rows
        else {}
    )
    if not macro_rows:
        issues.append("macro context missing")
    elif macro.get("data_status") != "AVAILABLE":
        issues.append(f"macro context {macro.get('data_status')}")
    mk = store.select("market_context_snapshots", order="snapshot_date", desc=True, limit=1)
    sector_ctx: dict[str, Any] = {}
    if mk:
        m = mk[0]
        sector_ctx = {"snapshot_date": m.get("snapshot_date"), "spy_1d": m.get("spy_return_1d"), "breadth": m.get("breadth"), "sector_etf": etf_tk, "sector_etf_returns": (m.get("sector_returns") or {}).get(etf_tk or "")}  # fmt: skip
    else:
        issues.append("market context snapshot missing")
    if (
        etf_bars
        and price_status(date.fromisoformat(str(etf_bars[-1]["bar_date"])[:10]), today)
        != DataStatus.AVAILABLE
    ):
        issues.append(f"{etf_tk}: sector ETF prices stale")
    if not etf_bars:
        issues.append(f"sector ETF {etf_tk or '(none)'}: no price history")
    return PairInspection(
        as_of=now, long_ticker=L["ticker"], short_ticker=S["ticker"], relationship=rel, returns=rets,
        spread_returns_long_minus_short=spread, vs_sector_etf=vs_etf, vs_spy=vs_spy,
        correlation_60d=corr, correlation_obs=n, market=market, blackout=blackout, catalysts=cats, news=news,
        macro=macro, sector_context=sector_ctx, missing_or_stale=list(dict.fromkeys(issues)), liquidity_warnings=liq_warn,
    )  # fmt: skip


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1%}"


def render_text(r: PairInspection) -> str:
    L, S = r.long_ticker, r.short_ticker
    o = [
        f"PAIR INSPECTION  LONG {L} / SHORT {S}   (as of {r.as_of:%Y-%m-%d %H:%M} UTC)",
        "RESEARCH ONLY - enter any trade manually in Trader View.",
        "",
    ]
    o += ["== Relationship =="] + [f"  {x}" for x in r.relationship]
    o += ["", "== Returns (1/5/20/60d) =="]
    for t, v in r.returns.items():
        o.append(f"  {t:<6}" + "  ".join(f"{n}d {_pct(x):>7}" for n, x in v.items()))
    o.append(
        f"  {'L-S':<6}"
        + "  ".join(f"{n}d {_pct(x):>7}" for n, x in r.spread_returns_long_minus_short.items())
    )
    o += ["", "== Relative strength (vs sector ETF | vs SPY) =="]
    for t in r.returns:
        o.append(
            f"  {t:<6}ETF "
            + " ".join(f"{_pct(x):>7}" for x in r.vs_sector_etf[t].values())
            + "   SPY "
            + " ".join(f"{_pct(x):>7}" for x in r.vs_spy[t].values())
        )
    o += [
        "",
        "== Correlation (60d daily returns) ==",
        f"  {r.correlation_60d if r.correlation_60d is None else round(r.correlation_60d, 3)}  (n={r.correlation_obs})",
    ]
    o += ["", "== Price / volume / liquidity =="]
    for t, m in r.market.items():
        o.append(
            f"  {t}: close {m['last_close']} ({m['last_bar_date']}), ADV20 ${m['adv_20d_usd'] or 0:,.0f}, est. leg cap ${m['est_leg_cap_usd'] or 0:,.0f}, mkt cap {('$' + format(m['market_cap_usd'], ',.0f')) if m['market_cap_usd'] else 'n/a'}"
        )
    b = r.blackout
    o += [
        "",
        f"== Event blackout ({b['week']}) ==",
        f"  STATUS: {b['status']}  (default Monday list: {'included' if b['eligible_for_default_monday_list'] else 'EXCLUDED'})",
    ]
    o += (
        [f"  ! {x}" for x in b["blocked_reasons"]]
        + ([f"  override reason: {b['override_reason']}"] if b["override_reason"] else [])
        + [f"  - {x}" for x in b["warnings"]]
    )
    o += ["", "== SEC / news catalysts (30d) =="]
    for t in (L, S):
        cs = r.catalysts[t]
        o.append(f"  {t}: " + ("none" if not cs else ""))
        o += [f"    [{c['type']}] {c['headline']} ({c['date']})" for c in cs]
        for n in r.news[t][:3]:
            o.append(f"    news [{n['quality']}] {n['headline']} - {n['publisher']}")
    o += ["", "== Macro / sector context =="]
    o.append(f"  macro: {r.macro or 'missing'}")
    o.append(f"  market/sector: {r.sector_context or 'missing'}")
    o += ["", "== Missing / stale data =="] + (
        [f"  - {x}" for x in r.missing_or_stale] or ["  none"]
    )
    o += ["", "== WSR liquidity-cap warnings (ESTIMATE) =="] + [
        f"  - {x}" for x in r.liquidity_warnings
    ]
    return "\n".join(o)
