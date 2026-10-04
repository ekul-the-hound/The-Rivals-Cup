"""Finnhub free tier (key required, 60 calls/minute): analyst recommendation trends and the
earnings calendar. Price targets, estimates and transcripts are paid on Finnhub and are NOT used.

  https://finnhub.io/api/v1/stock/recommendation?symbol=XYZ
  https://finnhub.io/api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD[&symbol=XYZ]
"""

from datetime import date

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError, ProviderUnavailable

BASE = "https://finnhub.io/api/v1"


class Recommendation(BaseModel):
    ticker: str
    period: date
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0


class EarningsEvent(BaseModel):
    ticker: str
    earnings_date: date
    time_of_day: str | None = None
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    fiscal_period_end: date | None = None


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


class FinnhubProvider:
    name = "finnhub"

    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        sleep=None,
    ) -> None:
        if not api_key:
            raise ProviderUnavailable("FINNHUB_API_KEY not set (free key: finnhub.io)")
        self._key = api_key
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name, user_agent="wsr-research/0.1", per_second=0.9, timeout=timeout,
            cache=FileCache(cache_dir), transport=transport, **kw,
        )  # fmt: skip

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self.http.aclose()

    async def recommendations(self, symbol: str) -> list[Recommendation]:
        data = await self.http.get_json(
            f"{BASE}/stock/recommendation",
            params={"symbol": symbol, "token": self._key},
            ttl=12 * 3600,
        )
        if not isinstance(data, list):
            raise ProviderError(f"finnhub: unexpected recommendation payload for {symbol}")
        out = []
        for r in data:
            p = _d(r.get("period"))
            if p:
                out.append(
                    Recommendation(
                        ticker=symbol, period=p, strong_buy=r.get("strongBuy") or 0, buy=r.get("buy") or 0,
                        hold=r.get("hold") or 0, sell=r.get("sell") or 0, strong_sell=r.get("strongSell") or 0,
                    )
                )  # fmt: skip
        return out

    async def peers(self, symbol: str) -> list[str]:
        """Finnhub's peer list for a company (same industry group), excluding the company itself."""
        data = await self.http.get_json(
            f"{BASE}/stock/peers",
            params={"symbol": symbol, "token": self._key},
            ttl=14 * 86400,
        )
        if not isinstance(data, list):
            raise ProviderError(f"finnhub: unexpected peers payload for {symbol}")
        out: list[str] = []
        for t in data:
            tk = str(t).strip().upper().replace(".", "-")
            if tk and tk != symbol.upper() and tk not in out:
                out.append(tk)
        return out

    async def earnings_calendar(
        self, start: date, end: date, symbol: str | None = None
    ) -> list[EarningsEvent]:
        params = {"from": start.isoformat(), "to": end.isoformat(), "token": self._key}
        if symbol:
            params["symbol"] = symbol
        data = await self.http.get_json(f"{BASE}/calendar/earnings", params=params, ttl=6 * 3600)
        out = []
        for r in (data or {}).get("earningsCalendar", []) or []:
            d, sym = _d(r.get("date")), r.get("symbol")
            if d and sym:
                out.append(
                    EarningsEvent(
                        ticker=str(sym).upper().replace(".", "-"), earnings_date=d, time_of_day=r.get("hour") or None,
                        eps_estimate=r.get("epsEstimate"), revenue_estimate=r.get("revenueEstimate"),
                    )
                )  # fmt: skip
        return out
