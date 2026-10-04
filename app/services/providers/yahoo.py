"""Yahoo Finance adapter. UNOFFICIAL and replaceable: JSON chart endpoint only, no page scraping.

Not an authority for filings, corporate actions, or WSR-specific limits.
"""

from datetime import UTC, date, datetime, timedelta

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SUMMARY = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
OPTIONS = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"


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
    dividends: list[tuple[date, float]] = []
    splits: list[tuple[date, str]] = []
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

    async def daily_history(
        self, symbol: str, range_: str = "6mo", *, events: bool = False
    ) -> History:
        params = {"range": range_, "interval": "1d", "includeAdjustedClose": "true"}
        if events:
            params["events"] = "div,split"  # dividends and splits ride along on the same request
        data = await self.http.get_json(CHART.format(symbol=symbol), params=params, ttl=4 * 3600)
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
        ev = res.get("events") or {}
        divs = sorted(
            (
                (datetime.fromtimestamp(int(d.get("date", k)), UTC).date(), float(d["amount"]))
                for k, d in (ev.get("dividends") or {}).items()
                if d.get("amount") is not None
            ),
        )
        splits = sorted(
            (
                (
                    datetime.fromtimestamp(int(d.get("date", k)), UTC).date(),
                    f"{d.get('numerator')}:{d.get('denominator')}",
                )
                for k, d in (ev.get("splits") or {}).items()
            ),
        )
        return History(
            symbol=symbol,
            bars=bars,
            dividends=divs,
            splits=splits,
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

    async def options_snapshot(self, symbol: str) -> dict | None:
        """Best effort and unofficial: nearest-expiry at-the-money implied volatility and volume
        put/call ratio. Returns None whenever Yahoo refuses (it often requires a login crumb)."""
        try:
            data = await self.http.get_json(OPTIONS.format(symbol=symbol), ttl=6 * 3600)
            res = data["optionChain"]["result"][0]
            spot = res["quote"]["regularMarketPrice"]
            chain = res["options"][0]
        except (ProviderError, KeyError, IndexError, TypeError):
            return None
        calls, puts = chain.get("calls") or [], chain.get("puts") or []
        if not (calls or puts) or not spot:
            return None

        def atm_iv(side: list[dict]) -> float | None:
            ok = [c for c in side if c.get("impliedVolatility") and c.get("strike")]
            return (
                min(ok, key=lambda c: abs(c["strike"] - spot))["impliedVolatility"] if ok else None
            )

        ivs = [v for v in (atm_iv(calls), atm_iv(puts)) if v]
        cv = sum(c.get("volume") or 0 for c in calls)
        pv = sum(c.get("volume") or 0 for c in puts)
        exp = chain.get("expirationDate")
        return {
            "expiry": datetime.fromtimestamp(exp, UTC).date() if exp else None,
            "atm_implied_vol": round(sum(ivs) / len(ivs), 4) if ivs else None,
            "call_volume": cv,
            "put_volume": pv,
            "put_call_volume_ratio": round(pv / cv, 4) if cv else None,
        }
