"""Yahoo Finance adapter. UNOFFICIAL and replaceable: JSON chart endpoint only, no page scraping.

Not an authority for filings, corporate actions, or WSR-specific limits.
"""

from datetime import UTC, date, datetime, timedelta

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SUMMARY = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"


class Bar(BaseModel):
    bar_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    adj_close: float | None = None
    volume: int | None = None


class History(BaseModel):
    symbol: str
    bars: list[Bar]
    last_price: float | None = None
    last_price_at: datetime | None = None
    previous_close: float | None = None


class YahooProfile(BaseModel):
    market_cap: float | None = None
    sector: str | None = None
    industry: str | None = None


class YahooProvider:
    name = "yahoo_finance"
    unofficial = True

    def __init__(
        self,
        user_agent: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        sleep=None,
    ) -> None:
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name,
            user_agent=user_agent,
            per_second=1.0,
            timeout=timeout,
            cache=FileCache(cache_dir),
            transport=transport,
            **kw,
        )

    async def healthcheck(self) -> bool:
        return bool((await self.daily_history("SPY", "5d")).bars)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def daily_history(self, symbol: str, range_: str = "6mo") -> History:
        data = await self.http.get_json(
            CHART.format(symbol=symbol),
            params={"range": range_, "interval": "1d", "includeAdjustedClose": "true"},
            ttl=4 * 3600,
        )
        chart = data.get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise ProviderError(f"yahoo: no chart data for {symbol}: {chart.get('error')}")
        res = chart["result"][0]
        meta = res.get("meta", {})
        offset = timedelta(seconds=meta.get("gmtoffset", 0))
        q = res.get("indicators", {}).get("quote", [{}])[0]
        adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])
        bars: list[Bar] = []
        for i, ts in enumerate(res.get("timestamp") or []):
            close = (q.get("close") or [])[i] if i < len(q.get("close") or []) else None
            if close is None:
                continue
            g = lambda k, i=i: (q.get(k) or [None] * (i + 1))[i]  # noqa: E731
            bars.append(
                Bar(
                    bar_date=(datetime.fromtimestamp(ts, UTC) + offset).date(),
                    open=g("open"),
                    high=g("high"),
                    low=g("low"),
                    close=close,
                    adj_close=adj[i] if i < len(adj) else None,
                    volume=int(g("volume")) if g("volume") is not None else None,
                )
            )
        t = meta.get("regularMarketTime")
        return History(
            symbol=symbol,
            bars=bars,
            last_price=meta.get("regularMarketPrice"),
            last_price_at=datetime.fromtimestamp(t, UTC) if t else None,
            previous_close=meta.get("chartPreviousClose") or meta.get("previousClose"),
        )

    async def profile(self, symbol: str) -> YahooProfile | None:
        """Best effort: this endpoint often needs a crumb and may refuse; callers must tolerate None."""
        try:
            data = await self.http.get_json(
                SUMMARY.format(symbol=symbol),
                params={"modules": "price,summaryProfile"},
                ttl=24 * 3600,
            )
            r = data["quoteSummary"]["result"][0]
        except (ProviderError, KeyError, IndexError, TypeError):
            return None
        return YahooProfile(
            market_cap=(r.get("price", {}).get("marketCap") or {}).get("raw"),
            sector=r.get("summaryProfile", {}).get("sector"),
            industry=r.get("summaryProfile", {}).get("industry"),
        )
