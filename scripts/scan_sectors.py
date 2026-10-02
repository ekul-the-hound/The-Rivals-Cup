"""python -m scripts.scan_sectors [--sectors "Health Care,Industrials,..."] [--top N] [--json] [--csv FILE] [--mock]

READ-ONLY sector scan over data already in your database. Screens every stock in each sector, then
every same-sector pair, and prints a ranked shortlist. It writes nothing to the database and
creates no order, trade or position. Enter and record any trade manually in Trader View.
"""

import argparse
import asyncio
import csv

from app.jobs import PROFILES, run_jobs
from app.services.scan.sector_scan import ScanParams, render_text, scan_sectors
from app.services.universe.sp500 import DEFAULT_SECTORS
from scripts._runtime import build_context


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sectors", default=",".join(DEFAULT_SECTORS), help="comma-separated sector names"
    )
    ap.add_argument("--top", type=int, default=8, help="pairs to keep per sector")
    ap.add_argument("--min-corr", type=float, default=0.60)
    ap.add_argument(
        "--leg-usd", type=float, default=10_000.0, help="planned leg size for the liquidity screen"
    )
    ap.add_argument("--week", help="Monday YYYY-MM-DD of the scoring week (default: auto)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", help="also write the shortlisted pairs to this CSV file")
    ap.add_argument("--mock", action="store_true", help="offline synthetic data, in-memory DB")
    a = ap.parse_args(argv)
    from datetime import date

    ctx = build_context(a.mock)
    try:
        if a.mock:
            await run_jobs(ctx, PROFILES["sunday"])
        res = scan_sectors(
            ctx.db,
            [s.strip() for s in a.sectors.split(",") if s.strip()],
            ctx.now,
            date.fromisoformat(a.week) if a.week else None,
            ScanParams(top_n=a.top, min_corr=a.min_corr, leg_usd=a.leg_usd),
        )
    finally:
        await ctx.providers.aclose()
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "sector",
                    "rank",
                    "long",
                    "short",
                    "score",
                    "corr_60d",
                    "spread_20d",
                    "spread_z",
                    "same_industry",
                    "est_cap_usd",
                ]
            )
            for s in res.sectors:
                for r in s.pairs:
                    w.writerow([s.sector, r.rank, r.long, r.short, r.score, r.corr_60d, r.spread_20d, r.spread_z, r.same_industry, r.est_cap_usd])  # fmt: skip
    print(res.model_dump_json(indent=2) if a.json else render_text(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
