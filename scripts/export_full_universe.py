"""Export the WHOLE screened universe so every name in the five sectors can be scanned.

  python -m scripts.export_full_universe [--outdir data/exports/universe]

Writes three files:
  universe_table.csv  one row per screened stock: sector, SEC industry, price, liquidity, strength,
                      returns, risk, short-interest context, penalty flags, earnings date
  prices_adj.csv      dividend-adjusted closes, one column per ticker, one row per date
  peers.csv           Finnhub peer links collected so far (ticker, peer)

A person (or Claude) can then cluster competitors, compute any pair's spread statistics and choose
the long and short in each sector. Read-only research: it never places, queues or records a trade
and does not confirm that Trader View lists or allows shorting any symbol.
"""

import argparse
import csv
from pathlib import Path

from app.config import get_settings
from app.config.clock import utcnow
from app.db.store import SupabaseStore, create_supabase_client
from app.services.leaders.book import BookParams, load_context, prepare
from app.services.leaders.ranking import long_penalties, short_penalties

COLS = [
    "ticker", "name", "sector", "industry", "type", "exchange", "price", "adv_usd", "strength",
    "ret_5d", "ret_20d", "ret_60d", "ret_120d", "ma50_dist", "pos_52w", "vol_60d",
    "downside_dev_60d", "max_dd_60d", "beta_60d", "rel_60d_vs_sector", "days_to_cover",
    "short_interest_change_pct", "short_volume_ratio_5d", "revenue_growth_pct", "analyst_score",
    "earnings", "long_penalty", "short_penalty", "long_flags", "short_flags", "event_blackout",
]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--outdir", default="data/exports/universe")
    a = ap.parse_args(argv)
    today = utcnow().date()
    s = get_settings()
    store = SupabaseStore(
        create_supabase_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
    )
    params = BookParams.from_settings(s, today)
    prep = prepare(store, today, params)
    week = (params.week_start, params.week_end)
    tickers = sorted(prep.metrics)
    ctx = load_context(store, tickers, today)

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "universe_table.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for tk in tickers:
            m, r, c = prep.metrics[tk], prep.rows[tk], ctx.get(tk, {})
            lp, _, lf = long_penalties(m)
            sp, _, sf = short_penalties(m, c, week)
            vals = {
                "ticker": tk,
                "name": r.get("company_name"),
                "sector": r.get("sector"),
                "industry": r.get("industry"),
                "type": str(r.get("security_type")),
                "exchange": r.get("exchange"),
                "price": round(m["last_close"], 2),
                "adv_usd": round(m["adv_usd"]),
                "strength": m.get("strength"),
                "earnings": str(r.get("earnings_date_if_known") or ""),
                "long_penalty": lp,
                "short_penalty": sp,
                "long_flags": "|".join(lf),
                "short_flags": "|".join(sf),
                "event_blackout": int(tk in prep.blocked),
            }
            for k in COLS:
                if k in vals:
                    continue
                v = m.get(k) if k in m else c.get(k)
                vals[k] = round(v, 3) if isinstance(v, float) else v
            w.writerow(["" if vals[k] is None else vals[k] for k in COLS])

    dates = sorted({d for tk in tickers if tk in prep.series for d in prep.series[tk].dates})
    idx = {d: i for i, d in enumerate(dates)}
    with (out / "prices_adj.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"] + tickers)
        cols: list[list[str]] = []
        for tk in tickers:
            col = [""] * len(dates)
            sr = prep.series.get(tk)
            if sr:
                for d, v in zip(sr.dates, sr.adj, strict=True):
                    col[idx[d]] = f"{v:.4f}"
            cols.append(col)
        for i, d in enumerate(dates):
            w.writerow([d.isoformat()] + [c[i] for c in cols])

    from app.services.leaders.book import fetch_for_tickers

    peers = fetch_for_tickers(store, "competitor_map", tickers, chunk=50)
    with (out / "peers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "peer"])
        for r in peers:
            w.writerow([r["ticker"], r["peer"]])
    by_sector = {}
    for tk in tickers:
        by_sector[prep.rows[tk]["sector"]] = by_sector.get(prep.rows[tk]["sector"], 0) + 1
    print(f"Wrote {out}: {len(tickers)} stocks {by_sector}; {len(dates)} dates; {len(peers)} peers")
    print(f"Screened out: {prep.screened_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
