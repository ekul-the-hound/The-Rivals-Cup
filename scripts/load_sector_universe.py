"""python -m scripts.load_sector_universe [--sectors "Health Care,Industrials,..."] [--csv FILE] [--apply]

Adds the S&P 500 stocks of the chosen GICS sectors to the `securities` research universe so the
free-data jobs and the sector scan can cover them. Default is a DRY RUN that writes nothing.

Research only: this only inserts rows into `securities` (existing rows are never changed). It does
not touch orders, brokers, WSR or Trader View, and it does NOT assert Rival Cup eligibility
(`wsr_eligibility` stays UNVERIFIED; verify every name in Trader View yourself).
"""

import argparse
import asyncio
from collections import Counter

from app.services.providers.base import ProviderError
from app.services.universe.sp500 import (
    DEFAULT_SECTORS,
    fetch_constituents,
    load_csv,
    select_sectors,
)
from scripts._runtime import build_context


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sectors", help="comma-separated GICS sector names", default=",".join(DEFAULT_SECTORS)
    )
    ap.add_argument("--csv", help="use this CSV (ticker,name,sector,industry) instead of Wikipedia")
    ap.add_argument("--apply", action="store_true", help="actually insert missing securities")
    a = ap.parse_args(argv)
    sectors = tuple(s.strip() for s in a.sectors.split(",") if s.strip())

    ctx = build_context(mock=False)
    try:
        try:
            items = (
                load_csv(a.csv)
                if a.csv
                else await fetch_constituents(ctx.providers.require("wiki"))
            )
        except (ProviderError, ValueError, OSError) as exc:
            print(f"error: could not get the constituent list: {exc}")
            print("tip: save a CSV with columns ticker,name,sector,industry and pass --csv FILE")
            return 2
        chosen = select_sectors(items, sectors)
        have = {s["ticker"] for s in ctx.db.select("securities")}
        new = [c for c in chosen if c.ticker not in have]
        print(f"{len(items)} constituents read; {len(chosen)} in {', '.join(sectors)}")
        for sec, n in sorted(Counter(c.sector for c in chosen).items()):
            print(f"  {sec:<24} {n:>3}  (new: {sum(1 for c in new if c.sector == sec)})")
        unknown = set(sectors) - {c.sector for c in chosen}
        if unknown:
            print(f"warning: no stocks found for: {', '.join(sorted(unknown))}")
        if not a.apply:
            print(f"\nDRY RUN: would add {len(new)} securities. Re-run with --apply to write them.")
            return 0
        rows = [
            {
                "ticker": c.ticker, "name": c.name, "security_type": "EQUITY", "sector": c.sector,
                "industry": c.industry, "is_etf": False, "is_benchmark": False, "is_active": True,
                "notes": "S&P 500 member per Wikipedia list; Rival Cup eligibility NOT verified",
            }
            for c in new
        ]  # fmt: skip
        wrote = ctx.db.upsert("securities", rows, "ticker", ignore_duplicates=True) if rows else 0
        print(
            f"\nAdded {wrote} securities ({len(chosen) - len(new)} already existed, left unchanged)."
        )
        print(
            "Next: python -m scripts.refresh_weekly_research --jobs refresh_universe,refresh_daily_prices"
        )
    finally:
        await ctx.providers.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
