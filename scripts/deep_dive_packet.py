"""Print or save the research packet for one or more tickers (all collected data, gaps listed).

  python -m scripts.deep_dive_packet --tickers MHLT,MIND --format md
  python -m scripts.deep_dive_packet --tickers MHLT --format json --output data/exports/packet.json
  python -m scripts.deep_dive_packet --mock --tickers MHLT          (offline demo)

Read-only. The output is research input for you to read or paste into Claude; it is not a trade
instruction and does not confirm WSR/Trader View availability.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.db.store import SupabaseStore, create_supabase_client
from app.jobs import PROFILES, run_jobs
from app.services.deep_dive.packet import build_packet, render_markdown
from app.services.universe.repo import load_universe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--output")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args(argv)
    if a.mock:
        from scripts._runtime import build_context

        ctx = build_context(True)
        ctx.settings = ctx.settings.model_copy(update={"deep_dive_tickers": a.tickers})
        for prof in ("universe", "deepmarket", "deepdive"):
            asyncio.run(run_jobs(ctx, PROFILES[prof]))
        store = ctx.db
    else:
        from app.config import get_settings

        s = get_settings()
        store = SupabaseStore(
            create_supabase_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
        )
    uni = load_universe(store)
    packets = []
    for tk in [x for x in a.tickers.split(",") if x.strip()]:
        try:
            packets.append(build_packet(store, tk, uni))
        except KeyError:
            print(f"{tk}: not in security_master (run the universe jobs first)")
    text = (
        "\n\n---\n\n".join(render_markdown(p) for p in packets)
        if a.format == "md"
        else json.dumps(packets, indent=2, default=str)
    )
    if a.output:
        out = Path(a.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {len(packets)} packets to {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
