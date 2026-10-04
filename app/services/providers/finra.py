"""FINRA public files (free, no key): equity short interest and daily short-sale volume.

  https://cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv    short interest, twice a month
  https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt     daily short-sale volume

Parsing is header-driven and delimiter-sniffed so a small format change does not break it.
Short interest is published ~7 business days after its settlement date (mid-month and month-end).
"""

import csv
import io
import re
from datetime import date, timedelta

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError

SHORT_INTEREST_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{d}.csv"
SHORT_VOLUME_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"


class ShortInterestRow(BaseModel):
    ticker: str
    settlement_date: date
    short_interest_shares: float | None = None
    previous_short_interest_shares: float | None = None
    change_percent: float | None = None
    avg_daily_volume: float | None = None
    days_to_cover: float | None = None


class ShortVolumeRow(BaseModel):
    ticker: str
    trade_date: date
    short_volume: float | None = None
    short_exempt_volume: float | None = None
    total_volume: float | None = None

    @property
    def ratio(self) -> float | None:
        if self.total_volume:
            return round((self.short_volume or 0) / self.total_volume, 6)
        return None


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _num(v: str | None) -> float | None:
    try:
        s = (v or "").replace(",", "").strip()
        return float(s) if s else None
    except ValueError:
        return None


def _rows(text: str) -> list[dict[str, str]]:
    sample = text[:4000]
    delim = max([",", "|", "\t"], key=sample.count)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if r]
    if len(rows) < 2:
        raise ProviderError("finra: file has no data rows")
    header = [_norm(h) for h in rows[0]]
    return [dict(zip(header, [c.strip() for c in r], strict=False)) for r in rows[1:]]


def _pick(r: dict[str, str], *names: str) -> str | None:
    for n in names:
        if n in r and r[n] != "":
            return r[n]
    return None


def parse_short_interest(text: str, settlement: date) -> list[ShortInterestRow]:
    out: list[ShortInterestRow] = []
    for r in _rows(text):
        sym = _pick(r, "symbolcode", "symbol", "issuesymbolidentifier")
        if not sym:
            continue
        sd = _pick(r, "settlementdate")
        try:
            sdate = date.fromisoformat(sd[:10]) if sd and "-" in sd else settlement
        except ValueError:
            sdate = settlement
        out.append(
            ShortInterestRow(
                ticker=sym.upper().replace(".", "-"),
                settlement_date=sdate,
                short_interest_shares=_num(
                    _pick(
                        r, "currentshortpositionquantity", "currentshortinterest", "shortinterest"
                    )
                ),
                previous_short_interest_shares=_num(
                    _pick(r, "previousshortpositionquantity", "previousshortinterest")
                ),
                change_percent=_num(_pick(r, "changepercent", "percentchange")),
                avg_daily_volume=_num(_pick(r, "averagedailyvolumequantity", "averagedailyvolume")),
                days_to_cover=_num(_pick(r, "daystocoverquantity", "daystocover")),
            )
        )
    if not out:
        raise ProviderError("finra: short interest file had no recognisable symbol column")
    return out


def parse_short_volume(text: str, trade_date: date) -> list[ShortVolumeRow]:
    out: list[ShortVolumeRow] = []
    for r in _rows(text):
        sym = _pick(r, "symbol")
        if not sym:
            continue
        d = _pick(r, "date")
        try:
            td = (
                date(int(d[:4]), int(d[4:6]), int(d[6:8]))
                if d and d.isdigit() and len(d) == 8
                else trade_date
            )
        except ValueError:
            td = trade_date
        out.append(
            ShortVolumeRow(
                ticker=sym.upper().replace(".", "-"),
                trade_date=td,
                short_volume=_num(_pick(r, "shortvolume")),
                short_exempt_volume=_num(_pick(r, "shortexemptvolume")),
                total_volume=_num(_pick(r, "totalvolume")),
            )
        )
    if not out:
        raise ProviderError("finra: short volume file had no recognisable symbol column")
    return out


def candidate_settlement_dates(today: date, n: int = 6) -> list[date]:
    """Recent mid-month and month-end settlement dates (rolled back to a weekday), newest first."""
    out: list[date] = []
    y, m = today.year, today.month
    for _ in range(4):
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        for d in (nxt - timedelta(days=1), date(y, m, 15)):
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            if d < today:
                out.append(d)
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return sorted(set(out), reverse=True)[:n]


class FinraProvider:
    name = "finra"

    def __init__(
        self,
        user_agent: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
        sleep=None,
    ) -> None:
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name, user_agent=user_agent, per_second=1.0, timeout=timeout,
            cache=FileCache(cache_dir), transport=transport, **kw,
        )  # fmt: skip

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self.http.aclose()

    async def latest_short_interest(self, today: date) -> list[ShortInterestRow]:
        """Newest published file among recent settlement dates (a missing file is skipped)."""
        for d in candidate_settlement_dates(today):
            try:
                text = await self.http.get_text(
                    SHORT_INTEREST_URL.format(d=d.strftime("%Y%m%d")), ttl=12 * 3600
                )
            except ProviderError as exc:
                if exc.status in (403, 404):
                    continue
                raise
            return parse_short_interest(text, d)
        raise ProviderError("finra: no short interest file found for recent settlement dates")

    async def latest_short_volume(self, today: date) -> list[ShortVolumeRow]:
        """Newest daily short-sale volume file (looks back up to 6 weekdays)."""
        d, tried = today, 0
        while tried < 6:
            d -= timedelta(days=1)
            if d.weekday() >= 5:
                continue
            tried += 1
            try:
                text = await self.http.get_text(
                    SHORT_VOLUME_URL.format(d=d.strftime("%Y%m%d")), ttl=6 * 3600
                )
            except ProviderError as exc:
                if exc.status in (403, 404):
                    continue
                raise
            return parse_short_volume(text, d)
        raise ProviderError("finra: no daily short volume file found in the last 6 weekdays")
