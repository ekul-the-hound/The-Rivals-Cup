"""python -m scripts.build_weekly_portfolio [--mock] [--max-pairs N] [--persist] [--json]

Builds the weekly peer-pair RESEARCH DRAFT. Read-only against market data; with --persist it only
writes pair_rankings / weekly_portfolios. Never creates orders, trades or positions.
"""

import argparse
import asyncio

from app.jobs import PROFILES, run_jobs
from app.services.pairs.params import EngineParams
from app.services.pairs.portfolio import build_weekly_portfolio, render_text
from scripts._runtime import build_context


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--mock", action="store_true", help="in-memory DB populated from mock providers"
    )
    ap.add_argument("--max-pairs", type=int, default=6, help="1-6 (hard cap 6)")
    ap.add_argument(
        "--leg-usd",
        type=float,
        default=10_000.0,
        help="intended manual leg size for liquidity checks",
    )
    ap.add_argument(
        "--persist", action="store_true", help="save the draft to Supabase (not applied in --mock)"
    )
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ctx = build_context(a.mock)
    try:
        if a.mock:
            await run_jobs(ctx, PROFILES["sunday"])
        p = EngineParams(max_pairs=min(max(a.max_pairs, 1), 6), intended_leg_notional_usd=a.leg_usd)
        pf = build_weekly_portfolio(ctx.db, ctx.now, p, db=ctx.db if a.persist else None)
    finally:
        await ctx.providers.aclose()
    print(pf.model_dump_json(indent=2) if a.json else render_text(pf))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
