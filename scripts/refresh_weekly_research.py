"""python -m scripts.refresh_weekly_research [--profile sunday|daily|monday|full] [--jobs a,b] [--mock]

Refreshes free-data research tables. Research only: no orders, no broker, no Trader View.
"""

import argparse
import asyncio
from datetime import date

from app.jobs import JOBS, PROFILES, run_jobs
from app.services.pairs.blackout import scoring_week
from app.services.pairs.candidates import monday_candidates
from scripts._runtime import build_context


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="sunday")
    ap.add_argument("--jobs", help="comma-separated job names (overrides --profile)")
    ap.add_argument("--week", help="Monday YYYY-MM-DD of the scoring week (default: auto)")
    ap.add_argument("--mock", action="store_true", help="offline synthetic data, in-memory DB")
    a = ap.parse_args(argv)
    names = a.jobs.split(",") if a.jobs else PROFILES[a.profile]
    ctx = build_context(a.mock, date.fromisoformat(a.week) if a.week else None)
    try:
        results = await run_jobs(ctx, names)
    finally:
        await ctx.providers.aclose()
    for r in results:
        print(
            f"{r.status:<9} {r.job:<34} read={r.rows_read:<5} wrote={r.rows_written:<5} {r.message or ''}"
        )
        for w in r.warnings[:5]:
            print(f"            ! {w}")
    if "build_weekly_event_blackout_list" in names:
        start = (ctx.week_start or scoring_week(ctx.today)[0]).isoformat()
        c = monday_candidates(ctx.db, start)
        print(
            f"\nDefault Monday candidates for week {start}: {len(c['included'])} included, {len(c['excluded'])} excluded"
        )
        for x in c["excluded"]:
            print(f"  EXCLUDED {x['pair']}: {'; '.join(x['reasons'])}")
    unknown = [n for n in names if n not in JOBS]
    return 1 if unknown or any(r.status == "FAILED" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
