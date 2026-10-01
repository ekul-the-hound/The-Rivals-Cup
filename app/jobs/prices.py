"""refresh_daily_prices: Yahoo daily OHLCV/adjusted close, latest quote, 20d ADV liquidity."""

from datetime import time

from app.jobs._util import active_securities
from app.services.features.metrics import adv_dollar, adv_shares, estimate_liquidity_cap
from app.services.ingestion.runner import JobContext, JobResult
from app.services.providers.base import ProviderError


async def refresh_daily_prices(ctx: JobContext) -> JobResult:
    yahoo = ctx.providers.require("yahoo")
    rows_in = rows_out = 0
    warnings: list[str] = []
    drop_today = ctx.local_now.time() < time(16, 0)  # today's bar may still be forming
    for s in active_securities(ctx.db):
        have = ctx.db.select(
            "market_bars", columns="bar_date", eq={"security_id": s["id"]}, limit=1
        )
        try:
            hist = await yahoo.daily_history(s["ticker"], "1mo" if have else "6mo")
        except ProviderError as exc:
            warnings.append(f"{s['ticker']}: {exc}")
            continue
        bars = [b for b in hist.bars if not (drop_today and b.bar_date >= ctx.today)]
        rows_in += len(bars)
        rows = [
            {
                "security_id": s["id"], "bar_date": b.bar_date, "timeframe": "1D", "open": b.open,
                "high": b.high, "low": b.low, "close": b.close, "adj_close": b.adj_close,
                "volume": b.volume, "source": "yahoo_finance", "data_status": "AVAILABLE",
                "retrieved_at": ctx.now,
            }
            for b in bars
        ]  # fmt: skip
        rows_out += ctx.db.upsert("market_bars", rows, "security_id,bar_date,timeframe,source")
        if hist.last_price and hist.last_price_at:
            rows_out += ctx.db.upsert(
                "daily_quotes",
                [{
                    "security_id": s["id"], "quote_ts": hist.last_price_at, "price": hist.last_price,
                    "previous_close": hist.previous_close, "source": "yahoo_finance",
                    "data_status": "AVAILABLE", "retrieved_at": ctx.now,
                }],
                "security_id,quote_ts,source",
            )  # fmt: skip
        dicts = [b.model_dump() for b in bars]
        adv = adv_dollar(dicts)
        if adv is None:
            warnings.append(f"{s['ticker']}: not enough volume history for ADV")
            continue
        rows_out += ctx.db.upsert(
            "liquidity_metrics",
            [{
                "security_id": s["id"], "as_of_date": bars[-1].bar_date, "adv_20d_usd": round(adv, 2),
                "adv_20d_shares": round(adv_shares(dicts) or 0, 0),
                "est_max_position_usd": estimate_liquidity_cap(adv, ctx.settings.wsr_est_adv_pct_cap),
                "method": "adv_pct_v1", "data_status": "AVAILABLE", "evidence_quality": "DERIVED",
            }],
            "security_id,as_of_date,method",
        )  # fmt: skip
    return JobResult(
        job="refresh_daily_prices", rows_read=rows_in, rows_written=rows_out, warnings=warnings
    )
