"""Alpha Vantage free tier (key required, about 25 calls/day): market-wide earnings calendar (one
call) and earnings-call transcripts (one call per company and quarter).

  https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=3month   (CSV)
  https://www.alphavantage.co/query?function=EARNINGS_CALL_TRANSCRIPT&symbol=XYZ&quarter=2026Q2

A rate-limit notice comes back as HTTP 200 with a "Note"/"Information" field; it is raised as an
error so jobs stop instead of storing nothing silently. Jobs also cap calls per run.
"""

import csv
import io
from datetime import date

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError, ProviderUnavailable
from app.services.providers.finnhub import EarningsEvent

URL = "https://www.alphavantage.co/query"


class Transcript(BaseModel):
    ticker: str
    fiscal_quarter: str
    segment_count: int
    excerpt: str


def _f(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


class AlphaVantageProvider:
    name = "alpha_vantage"

    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        sleep=None,
    ) -> None:
        if not api_key:
            raise ProviderUnavailable("ALPHA_VANTAGE_API_KEY not set (free key: alphavantage.co)")
        self._key = api_key
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name, user_agent="wsr-research/0.1", per_second=0.2, timeout=timeout,
            cache=FileCache(cache_dir), transport=transport, **kw,
        )  # fmt: skip

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _check(text: str) -> None:
        head = text[:400]
        if text.lstrip().startswith("{") and any(
            k in head for k in ("Note", "Information", "Error Message")
        ):
            raise ProviderError(f"alpha_vantage: {head[:200]}")

    async def earnings_calendar(self, horizon: str = "3month") -> list[EarningsEvent]:
        params = {"function": "EARNINGS_CALENDAR", "horizon": horizon, "apikey": self._key}
        text = await self.http.get_text(URL, params=params, ttl=12 * 3600)
        try:
            self._check(text)
        except ProviderError:
            self.http.forget(URL, params)
            raise
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            try:
                d = date.fromisoformat((r.get("reportDate") or "")[:10])
            except ValueError:
                continue
            sym = (r.get("symbol") or "").strip().upper().replace(".", "-")
            if sym:
                fe = r.get("fiscalDateEnding") or ""
                out.append(
                    EarningsEvent(
                        ticker=sym, earnings_date=d, eps_estimate=_f(r.get("estimate")),
                        fiscal_period_end=date.fromisoformat(fe[:10]) if fe[:4].isdigit() else None,
                    )
                )  # fmt: skip
        return out

    async def transcript(
        self, symbol: str, quarter: str, max_chars: int = 6000
    ) -> Transcript | None:
        data = await self.http.get_json(
            URL,
            params={
                "function": "EARNINGS_CALL_TRANSCRIPT",
                "symbol": symbol,
                "quarter": quarter,
                "apikey": self._key,
            },
            ttl=30 * 86400,
        )
        if not isinstance(data, dict):
            return None
        if any(k in data for k in ("Note", "Information", "Error Message")):
            raise ProviderError(
                "alpha_vantage: "
                + str(
                    next(
                        v for k, v in data.items() if k in ("Note", "Information", "Error Message")
                    )
                )[:200]
            )
        segs = data.get("transcript") or []
        if not segs:
            return None
        # skip the operator's greeting/logistics so the excerpt starts at management's remarks
        body = [s for s in segs if str(s.get("speaker", "")).strip().lower() != "operator"] or segs
        text = " ".join(f"{s.get('speaker', '')}: {s.get('content', '')}".strip() for s in body)
        return Transcript(
            ticker=symbol, fiscal_quarter=quarter, segment_count=len(segs), excerpt=text[:max_chars]
        )
