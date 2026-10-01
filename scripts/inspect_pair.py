"""python -m scripts.inspect_pair LONG SHORT [--size-usd N] [--json] [--mock]

Read-only. Prints a research summary for manual evaluation. Does not create or imply any trade.
"""

import argparse
import asyncio

from app.jobs import PROFILES, run_jobs
from app.services.pairs.inspect import PairNotFound, inspect_pair, render_text
from scripts._runtime import build_context


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("long", help="ticker of the LONG leg")
    ap.add_argument("short", help="ticker of the SHORT leg")
    ap.add_argument("--size-usd", type=float, help="planned leg size for the liquidity-cap check")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--mock", action="store_true", help="populate an in-memory DB from mock providers first"
    )
    a = ap.parse_args(argv)
    ctx = build_context(a.mock)
    try:
        if a.mock:
            await run_jobs(ctx, PROFILES["sunday"])
        res = inspect_pair(ctx.db, ctx.settings, a.long, a.short, ctx.now, a.size_usd)
    except PairNotFound as exc:
        print(f"error: {exc}")
        return 2
    finally:
        await ctx.providers.aclose()
    print(res.model_dump_json(indent=2) if a.json else render_text(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
