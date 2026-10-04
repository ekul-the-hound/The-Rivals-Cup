"""Print or save the weekly leaders & laggards candidate book.

  python -m scripts.leaders_laggards                       (latest stored book, else computes live)
  python -m scripts.leaders_laggards --live --format json --output data/exports/book.json
  python -m scripts.leaders_laggards --mock                 (offline demo on synthetic data)

Strategy: in each target sector, the strongest stock is the long candidate and the weakest of its
direct competitors is the short candidate. Read-only research: it never places, queues or records a
trade, and it does not confirm that Trader View lists or allows shorting any symbol.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.config.clock import utcnow
from app.db.store import SupabaseStore, create_supabase_client
from app.jobs import PROFILES, run_jobs
from app.services.leaders.book import BookParams, build_book, render_markdown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--live", action="store_true", help="recompute instead of reading the stored book"
    )
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--output")
    ap.add_argument("--portfolio-usd", type=float)
    ap.add_argument("--gross-pct", type=float, help="total gross target (both legs), max 180")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args(argv)
    today = utcnow().date()
    if a.mock:
        from scripts._runtime import build_context

        ctx = build_context(True)
        ctx.settings = ctx.settings.model_copy(
            update={
                "leaders_min_competitors": 1,
                "leaders_min_adv_usd": 1.0,
                "leaders_min_price": 1.0,
            }
        )
        for prof in ("universe", "deepmarket", "leaders"):
            asyncio.run(run_jobs(ctx, PROFILES[prof]))
        store, settings = ctx.db, ctx.settings
    else:
        from app.config import get_settings

        settings = get_settings()
        store = SupabaseStore(
            create_supabase_client(
                settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
            )
        )
    params = BookParams.from_settings(settings, today)
    if a.portfolio_usd:
        params.portfolio_usd = a.portfolio_usd
    if a.gross_pct:
        params.gross_target_pct = min(a.gross_pct, 180.0)
    book = None
    if not a.live and not (a.portfolio_usd or a.gross_pct):
        rows = store.select("leader_laggard_books", order="scoring_week_start", desc=True, limit=1)
        book = rows[0]["book"] if rows else None
    if book is None:
        book = build_book(store, today, params)
    text = render_markdown(book) if a.format == "md" else json.dumps(book, indent=2, default=str)
    if a.output:
        out = Path(a.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote the book ({len(book['pairs'])} pairs) to {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
