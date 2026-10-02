"""Export the U.S.-listed target-sector research universe.

  python -m scripts.export_target_sector_universe --format csv --output data/exports/us_target_sector_universe.csv
  python -m scripts.export_target_sector_universe --format json --view pair_research --output out.json
  python -m scripts.export_target_sector_universe --mock --output /tmp/universe.csv   (offline demo)

Read-only: reads security_master and its stored view state. It does not refresh anything (run the
four universe jobs first) and does not contact WSR / Trader View.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.config.clock import utcnow
from app.db.store import SupabaseStore, create_supabase_client
from app.jobs import PROFILES, run_jobs
from app.models.universe import DISCLAIMER
from app.services.universe.export import export_records, render_csv, render_json
from app.services.universe.repo import filter_rows, load_universe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--format", choices=["csv", "json"], default="csv")
    ap.add_argument("--output", default="data/exports/us_target_sector_universe.csv")
    ap.add_argument("--view", choices=["all", "pair_research", "manual_review"], default="all")
    ap.add_argument("--sector", help="optional single sector code, e.g. HEALTH_CARE")
    ap.add_argument("--mock", action="store_true", help="offline synthetic data")
    a = ap.parse_args(argv)

    if a.mock:
        from scripts._runtime import build_context

        ctx = build_context(True)
        asyncio.run(run_jobs(ctx, PROFILES["universe"]))
        store = ctx.db
    else:
        from app.config import get_settings

        s = get_settings()
        store = SupabaseStore(
            create_supabase_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
        )
    now = utcnow()
    rows = filter_rows(load_universe(store), a.view, sector=a.sector)
    recs = export_records(rows, now)
    text = render_csv(recs) if a.format == "csv" else render_json(recs, now, a.view)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="")
    print(f"wrote {len(recs)} records ({a.view}) to {out}")
    print(DISCLAIMER)
    if not recs:
        print(
            "No records: run the universe jobs first (docs/universe_builder.md).", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
