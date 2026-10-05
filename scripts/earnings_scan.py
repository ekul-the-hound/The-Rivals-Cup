"""Weekly earnings scan: this week's and next week's reports with P(up), Monte Carlo and signals.

  python -m scripts.earnings_scan --mock                 (offline demo on synthetic data)
  python -m scripts.earnings_scan                        (live: needs free keys, see docs/earnings.md)
  python -m scripts.earnings_scan --tickers KO,PEP --week next
  python -m scripts.earnings_scan --format json --output data/generated/earnings/week.json
  python -m scripts.earnings_scan --live-check           (probe every data source, print OK/FAIL)
  python -m scripts.earnings_scan --score                (grade past predictions against outcomes)

Read-only research: it never places, queues or records a trade. Probabilities are estimates from a
hand-weighted model that has not been backtested.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, timedelta
from pathlib import Path

from app.config import Settings, get_settings
from app.config.clock import utcnow
from app.earnings import calibration
from app.earnings.mock import POLITICIAN_URL, EarningsMockWorld
from app.earnings.service import SNAPSHOT, local_today, render_table, run_scan, save_scan
from app.services.providers.registry import build_providers


def _mock_settings() -> Settings:
    return Settings(
        sec_user_agent="Mock Runner mock@example.com",
        finnhub_api_key="mock-key",
        alpha_vantage_api_key="mock-key",
        options_iv_enabled=True,
        politician_trades_url=POLITICIAN_URL,
        earnings_deep_top_n=6,
        provider_cache_dir="",
        app_env="development",
    )


async def live_check(settings: Settings) -> int:
    """Probe each source with one tiny request. Never prints keys."""
    providers = build_providers(settings)
    today = utcnow().date()
    out: list[tuple[str, bool, str]] = []

    async def probe(name: str, coro) -> None:
        try:
            res = await coro
            out.append((name, bool(res), "ok" if res else "reachable but returned nothing"))
        except Exception as exc:
            out.append((name, False, f"{type(exc).__name__}: {str(exc)[:120]}"))

    def missing(name: str, key: str) -> None:
        out.append((name, False, providers.unavailable.get(key, "not configured")))

    if providers.finnhub:
        await probe(
            "finnhub calendar",
            providers.finnhub.earnings_calendar(today, today + timedelta(days=7)),
        )
        await probe("finnhub analysts", providers.finnhub.recommendations("AAPL"))
    else:
        missing("finnhub", "finnhub")
    if providers.alpha_vantage:
        await probe("alpha vantage calendar", providers.alpha_vantage.earnings_calendar("3month"))
    else:
        missing("alpha vantage", "alpha_vantage")
    if providers.sec:
        await probe("sec edgar (ticker map)", providers.sec.ticker_cik_map())
        from app.earnings.sources import InstitutionalFeed

        inst = InstitutionalFeed(providers.sec, settings.earnings_13f_cik_list[:1], 1)
        await inst.load(today)
        out.append(("sec 13f (first holder)", not inst.errors and bool((inst._data or {}).get(settings.earnings_13f_cik_list[0])),
                    "; ".join(inst.errors) or "ok"))  # fmt: skip
    else:
        missing("sec edgar", "sec")
    if providers.yahoo:
        await probe("yahoo prices", providers.yahoo.daily_history("SPY", "1mo"))
        if settings.options_iv_enabled:
            await probe("yahoo options", providers.yahoo.options_snapshot("SPY"))
    if providers.news:
        await probe("google news", providers.news.search("Apple stock earnings", days=7))
    if settings.politician_trades_url:
        from app.earnings.sources import PoliticianFeed
        from app.services.providers.base import HttpClient

        http = HttpClient(
            "politicians", user_agent=settings.effective_web_user_agent, per_second=1.0
        )
        feed = PoliticianFeed(http, settings.politician_trades_url)
        await feed.load()
        out.append(
            ("politician feed", not feed.error and bool(feed._by_ticker), feed.error or "ok")
        )
        await http.aclose()
    else:
        out.append(("politician feed", False, "POLITICIAN_TRADES_URL not set (optional)"))
    await providers.aclose()
    width = max(len(n) for n, _, _ in out)
    for name, ok, note in out:
        print(f"{'OK  ' if ok else 'FAIL'} {name:<{width}}  {note}")
    needed = [ok for n, ok, _ in out if n.startswith(("finnhub", "yahoo prices", "sec edgar"))]
    return 0 if needed and all(needed) else 1


async def main_async(a: argparse.Namespace) -> int:
    if a.mock:
        settings, today = _mock_settings(), utcnow().astimezone(UTC).date()
        world = EarningsMockWorld(today)
        transport = world.transport()
        providers = build_providers(settings, transport, sleep=lambda _x: asyncio.sleep(0))
    else:
        settings, transport = get_settings(), None
        today = local_today(settings)
        providers = build_providers(settings)
    if a.live_check:
        return await live_check(settings)
    try:
        if a.score:
            if not providers.yahoo:
                print("Yahoo provider unavailable", file=sys.stderr)
                return 1
            res = await calibration.score_log(
                providers.yahoo, today, base_p_up=settings.earnings_base_p_up
            )
            print(json.dumps(res, indent=2))
            return 0
        if a.paths:
            settings = settings.model_copy(update={"earnings_mc_paths": a.paths})
        logged = len(calibration.read_log())
        scan = await run_scan(
            settings, providers, today,
            tickers=[t for t in a.tickers.split(",") if t.strip()] if a.tickers else None,
            max_tickers=a.max_tickers, deep=not a.no_deep, everything=a.all, mock=a.mock,
            transport=transport, logged_predictions=logged,
        )  # fmt: skip
    finally:
        await providers.aclose()
    if a.format == "json":
        text = json.dumps(scan.model_dump(mode="json"), indent=1)
    else:
        text = render_table(scan, a.week)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"wrote {a.output}")
    else:
        print(text)
    if not a.no_save:
        path = save_scan(scan, SNAPSHOT.with_name("mock.json") if a.mock else SNAPSHOT)
        print(f"snapshot saved: {path}")
        if not a.mock:
            print(f"{calibration.log_predictions(scan)} predictions logged for later scoring")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mock", action="store_true", help="offline synthetic demo (no keys, no network)"
    )
    ap.add_argument("--live-check", action="store_true", help="probe each data source and exit")
    ap.add_argument(
        "--score", action="store_true", help="grade logged predictions against outcomes"
    )
    ap.add_argument(
        "--tickers", help="comma-separated tickers to analyse (must report in the window)"
    )
    ap.add_argument("--week", choices=["this", "next", "both"], default="both")
    ap.add_argument("--max-tickers", type=int, help="override EARNINGS_MAX_TICKERS")
    ap.add_argument(
        "--all", action="store_true", help="analyse every reporter, not just the S&P 500"
    )
    ap.add_argument("--no-deep", action="store_true", help="skip the peer read-through second pass")
    ap.add_argument("--paths", type=int, help="Monte Carlo paths per report (default 10000)")
    ap.add_argument("--format", choices=["table", "json"], default="table")
    ap.add_argument("--output", help="write the output to this file instead of printing")
    ap.add_argument("--no-save", action="store_true", help="do not write the dashboard snapshot")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
