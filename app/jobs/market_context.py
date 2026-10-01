"""refresh_market_context: SPY/QQQ/IWM + sector ETF returns derived from stored bars."""

from app.jobs._util import active_securities, load_bars
from app.models.enums import DataStatus
from app.services.features.metrics import close_series, trailing_returns
from app.services.ingestion.runner import JobContext, JobResult
from app.services.validation.freshness import price_status


async def refresh_market_context(ctx: JobContext) -> JobResult:
    etfs = {s["ticker"]: s for s in active_securities(ctx.db) if s["is_etf"]}
    rets, latest = {}, {}
    for tk, s in etfs.items():
        close = close_series(load_bars(ctx.db, s["id"], 70))
        if len(close):
            rets[tk] = trailing_returns(close, (1, 5, 20))
            latest[tk] = close.index[-1]
    if "SPY" not in rets:
        return JobResult(
            job="refresh_market_context", warnings=["SPY bars missing; run refresh_daily_prices"]
        )
    sectors = {
        tk: {f"r{n}": v for n, v in r.items()} for tk, r in rets.items() if tk.startswith("XL")
    }
    ordered = sorted(sectors.items(), key=lambda kv: kv[1]["r1"] if kv[1]["r1"] is not None else 0)
    spy_date = latest["SPY"]
    status = price_status(__import__("datetime").date.fromisoformat(spy_date), ctx.today)
    row = {
        "snapshot_date": spy_date, "snapshot_at": ctx.now,
        "spy_return_1d": rets["SPY"][1], "qqq_return_1d": rets.get("QQQ", {}).get(1),
        "iwm_return_1d": rets.get("IWM", {}).get(1), "sector_returns": sectors,
        "breadth": {
            "sectors_up_1d": sum(1 for v in sectors.values() if (v["r1"] or 0) > 0),
            "sectors_down_1d": sum(1 for v in sectors.values() if (v["r1"] or 0) < 0),
            "best_1d": ordered[-1][0] if ordered else None, "worst_1d": ordered[0][0] if ordered else None,
            "spy_5d": rets["SPY"][5], "spy_20d": rets["SPY"][20],
        },
        "source": "derived:yahoo_finance", "data_status": status if status != DataStatus.MISSING else "MISSING",
    }  # fmt: skip
    return JobResult(
        job="refresh_market_context",
        rows_read=len(rets),
        rows_written=ctx.db.upsert("market_context_snapshots", [row], "snapshot_date"),
    )
