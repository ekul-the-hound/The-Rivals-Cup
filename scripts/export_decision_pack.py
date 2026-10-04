"""Export a decision pack: everything Claude needs to PICK the leader/competitor pairs by judgment.

  python -m scripts.export_decision_pack [--leaders 12] [--pool 25] [--output data/exports/decision_pack.json]

The system is a data aggregator. This script dumps, per target sector, the strongest candidate
longs and, for each, a wide pool of possible direct competitors (Finnhub peers in any target
sector plus same-SEC-industry names), each with price strength, risk flags, short-interest context
and 60-day correlation to the leader. A person (or Claude) then chooses the pair.

Read-only research: it never places, queues or records a trade and does not confirm that
Trader View lists or allows shorting any symbol.
"""

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.config.clock import utcnow
from app.db.store import SupabaseStore, create_supabase_client
from app.services.leaders import SECTOR_ETF
from app.services.leaders.book import (
    BookParams,
    competitor_candidates,
    load_competitor_map,
    load_context,
    prepare,
)
from app.services.leaders.metrics import pair_correlation, spread_stats
from app.services.leaders.ranking import long_penalties, short_penalties

KEYS = (
    "ret_5d", "ret_20d", "ret_60d", "ret_120d", "ma50_dist", "pos_52w", "vol_60d",
    "downside_dev_60d", "max_dd_60d", "beta_60d", "rel_60d_vs_sector",
)  # fmt: skip


def _row(tk, prep, ctx, week):
    m, r = prep.metrics[tk], prep.rows[tk]
    lp, _, lflags = long_penalties(m)
    sp, _, sflags = short_penalties(m, ctx.get(tk, {}), week)
    return {
        "ticker": tk,
        "name": r.get("company_name"),
        "sector": r.get("sector"),
        "industry": r.get("industry"),
        "type": str(r.get("security_type")),
        "exchange": r.get("exchange"),
        "price": round(m["last_close"], 2),
        "adv_usd": round(m["adv_usd"]),
        "strength": m.get("strength"),
        "long_penalty": lp,
        "short_penalty": sp,
        "long_flags": lflags,
        "short_flags": sflags,
        "m": {k: (round(m[k], 2) if isinstance(m.get(k), float) else m.get(k)) for k in KEYS},
        "ctx": {k: v for k, v in ctx.get(tk, {}).items() if v is not None},
        "earnings": str(r.get("earnings_date_if_known") or "") or None,
        "blocked_event_blackout": tk in prep.blocked,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--leaders", type=int, default=15, help="candidate longs per sector")
    ap.add_argument("--pool", type=int, default=30, help="max same-industry names per leader")
    ap.add_argument("--output", default="data/exports/decision_pack.json")
    a = ap.parse_args(argv)
    today = utcnow().date()
    s = get_settings()
    store = SupabaseStore(
        create_supabase_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
    )
    params = BookParams.from_settings(s, today)
    prep = prepare(store, today, params)
    week = (params.week_start, params.week_end)

    top: dict[str, list[str]] = {}
    for sec, info in prep.sectors.items():
        ranked = [t for t in info["ranked"] if t not in prep.blocked]
        top[sec] = sorted(
            ranked[: a.leaders * 2],
            key=lambda t: prep.metrics[t]["strength"] - long_penalties(prep.metrics[t])[0],
            reverse=True,
        )[: a.leaders]
    leaders = [t for v in top.values() for t in v]
    mapped = load_competitor_map(store, leaders)

    pools: dict[str, list[dict]] = {}
    need = set(leaders)
    for lt in leaders:
        sec = prep.rows[lt]["sector"]
        out: list[dict] = []
        seen = {lt}
        for p in mapped.get(lt, []):  # Finnhub peers in ANY target sector
            if p in prep.metrics and p not in seen:
                out.append({"ticker": p, "source": "FINNHUB_PEERS"})
                seen.add(p)
        # same-SEC-industry names in the sector, nearest in dollar volume first
        ind, adv = prep.rows[lt].get("industry"), prep.metrics[lt]["adv_usd"]
        same = [
            (abs(prep.metrics[t]["adv_usd"] - adv), t)
            for t, r in prep.rows.items()
            if t not in seen and r["sector"] == sec and ind and r.get("industry") == ind
        ]
        for _, t in sorted(same)[: a.pool]:
            out.append({"ticker": t, "source": "SAME_SEC_INDUSTRY"})
            seen.add(t)
        # keep competitor_candidates' own fallback in play too (size-band names)
        for t, src in competitor_candidates(prep, lt, mapped)[0]:
            if t not in seen:
                out.append({"ticker": t, "source": src})
                seen.add(t)
        pools[lt] = out
        need |= {x["ticker"] for x in out}

    ctx = load_context(store, sorted(need), today)
    rows = {t: _row(t, prep, ctx, week) for t in need if t in prep.metrics}
    pack = {
        "generated_at": utcnow().isoformat(),
        "scoring_week": {"start": week[0].isoformat(), "end": week[1].isoformat()},
        "note": "Choose, per sector, one long (strong) and one short that is a DIRECT competitor "
        "of that long (weak). strength is a 0-100 sector percentile on price action only.",
        "sector_population": {k: v["population"] for k, v in prep.sectors.items()},
        "sector_etf": SECTOR_ETF,
        "candidate_longs": {sec: top[sec] for sec in top},
        "tickers": rows,
        "competitor_pools": {},
    }
    for lt, pool in pools.items():
        entries = []
        for x in pool:
            ct = x["ticker"]
            if ct not in rows or lt not in prep.series or ct not in prep.series:
                continue
            c = pair_correlation(prep.series[lt], prep.series[ct])
            entries.append(
                {
                    **x,
                    "corr_60d": None if c is None else round(c, 3),
                    "spread": spread_stats(prep.series[lt], prep.series[ct]),
                }
            )
        pack["competitor_pools"][lt] = entries
    # flat per-sector ranking for convenience; judgment still decides
    ranked: dict[str, list[dict]] = {}
    for sec, lts in top.items():
        flat = []
        for lt in lts:
            for e in pack["competitor_pools"].get(lt, []):
                sp = e.get("spread") or {}
                ct = e["ticker"]
                if rows[ct]["blocked_event_blackout"] or "wk_score" not in sp:
                    continue
                flat.append(
                    {
                        "long": lt,
                        "short": ct,
                        "source": e["source"],
                        "corr_60d": e["corr_60d"],
                        "strength_gap": round(rows[lt]["strength"] - rows[ct]["strength"], 1),
                        "wk_score": sp["wk_score"],
                        "wk_hit_rate": sp["wk_hit_rate"],
                        "wk_worst": sp["wk_worst"],
                        "cum_60d": sp.get("cum_60d"),
                        "max_dd_60d": sp.get("max_dd_60d"),
                        "last_week": sp["last_week"],
                        "short_flags": rows[ct]["short_flags"],
                    }
                )
        flat.sort(key=lambda r: r["wk_score"], reverse=True)
        ranked[sec] = flat[:40]
    pack["ranked_pairs_by_weekly_spread_score"] = ranked
    path = Path(a.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, indent=1, default=str), encoding="utf-8")
    n = sum(len(v) for v in pack["competitor_pools"].values())
    print(f"Wrote {path} ({len(rows)} tickers, {len(leaders)} candidate longs, {n} pairings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
