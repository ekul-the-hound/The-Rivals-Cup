"""FRED provider. Whitelisted series only: broad rate / volatility regime context."""

from datetime import date

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError, ProviderUnavailable

ALLOWED_SERIES = ("DGS2", "DGS10", "T10Y2Y", "FEDFUNDS", "VIXCLS")


class Observation(BaseModel):
    series_id: str
    obs_date: date
    value: float


class FredProvider:
    name = "fred"

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
            raise ProviderUnavailable("FRED_API_KEY not set")
        self._key = api_key
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name,
            user_agent="wsr-research/0.1",
            per_second=2.0,
            timeout=timeout,
            cache=FileCache(cache_dir),
            transport=transport,
            **kw,
        )

    async def healthcheck(self) -> bool:
        return (await self.latest("DGS10")) is not None

    async def aclose(self) -> None:
        await self.http.aclose()

    async def latest(self, series_id: str) -> Observation | None:
        if series_id not in ALLOWED_SERIES:
            raise ValueError(f"series {series_id} is not enabled")
        data = await self.http.get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": self._key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 10,
            },
            ttl=12 * 3600,
        )
        for o in data.get("observations", []):
            if o.get("value") not in (None, ".", ""):
                return Observation(
                    series_id=series_id,
                    obs_date=date.fromisoformat(o["date"]),
                    value=float(o["value"]),
                )
        return None

    async def latest_all(self) -> tuple[dict[str, Observation], dict[str, str]]:
        """Never fails the whole batch on one series (e.g. VIXCLS unavailable)."""
        got: dict[str, Observation] = {}
        errors: dict[str, str] = {}
        for s in ALLOWED_SERIES:
            try:
                obs = await self.latest(s)
            except ProviderError as exc:
                errors[s] = str(exc)
                continue
            if obs:
                got[s] = obs
            else:
                errors[s] = "no observations"
        return got, errors
