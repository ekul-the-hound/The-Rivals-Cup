"""Nasdaq Trader symbol directories (official, free): the active U.S. exchange-listed symbol list.

  https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   (Nasdaq-listed)
  https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    (NYSE, NYSE American, NYSE Arca, Cboe BZX, IEX)

Pipe-delimited text with a header row and a "File Creation Time" trailer. Parsing is
header-driven so a reordered column does not break it. Being on these lists says a symbol is
listed on a U.S. exchange; it says nothing about Rival Cup / Trader View availability.
"""

import re
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
EXCHANGE_CODES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}
_TRAILER = re.compile(r"File Creation Time:\s*(\d{8})\s*(\d{2}):?(\d{2})")


class Listing(BaseModel):
    symbol: str  # as published (ACT / Nasdaq symbol); may contain '.' or '$'
    name: str
    exchange: str
    listing_source: str  # nasdaqtrader:nasdaqlisted | nasdaqtrader:otherlisted
    test_issue: bool = False
    etf: bool = False
    financial_status: str | None = None
    market_category: str | None = None
    cqs_symbol: str | None = None


class ListingFile(BaseModel):
    source: str
    created_at: datetime | None = None
    rows: list[Listing]


def _yn(v: str | None) -> bool:
    return (v or "").strip().upper() == "Y"


def parse_listing_file(text: str, source: str) -> ListingFile:
    """Parse one directory file. Raises ProviderError when the header is not recognisable."""
    lines = [ln.rstrip("\r") for ln in text.splitlines() if ln.strip()]
    if not lines or "|" not in lines[0]:
        raise ProviderError(f"nasdaq_trader: {source}: not a symbol directory file")
    header = [h.strip() for h in lines[0].split("|")]
    idx = {h.lower(): i for i, h in enumerate(header)}

    def col(*names: str) -> int | None:
        return next((idx[n] for n in names if n in idx), None)

    i_sym = col("symbol", "act symbol", "nasdaq symbol")
    i_name = col("security name")
    if i_sym is None or i_name is None:
        raise ProviderError(f"nasdaq_trader: {source}: unexpected header {header[:4]}")
    i_exch, i_cat = col("exchange"), col("market category")
    i_test, i_etf = col("test issue"), col("etf")
    i_fin, i_cqs = col("financial status"), col("cqs symbol")
    created: datetime | None = None
    rows: list[Listing] = []
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            m = _TRAILER.search(ln)
            if m:
                mmddyyyy, hh, mm = m.groups()
                try:
                    created = datetime(
                        int(mmddyyyy[4:]), int(mmddyyyy[:2]), int(mmddyyyy[2:4]), int(hh), int(mm),
                        tzinfo=UTC,
                    )  # fmt: skip
                except ValueError:
                    created = None
            continue
        f = [c.strip() for c in ln.split("|")]
        if len(f) <= max(i_sym, i_name):
            continue
        symbol = f[i_sym].upper()
        if not symbol:
            continue
        code = f[i_exch].upper() if i_exch is not None and i_exch < len(f) else ""
        rows.append(
            Listing(
                symbol=symbol,
                name=f[i_name],
                exchange=EXCHANGE_CODES.get(code, code or "NASDAQ")
                if i_exch is not None
                else "NASDAQ",
                listing_source=source,
                test_issue=_yn(f[i_test]) if i_test is not None and i_test < len(f) else False,
                etf=_yn(f[i_etf]) if i_etf is not None and i_etf < len(f) else False,
                financial_status=(f[i_fin] or None)
                if i_fin is not None and i_fin < len(f)
                else None,
                market_category=(f[i_cat] or None)
                if i_cat is not None and i_cat < len(f)
                else None,
                cqs_symbol=(f[i_cqs] or None) if i_cqs is not None and i_cqs < len(f) else None,
            )
        )
    if not rows:
        raise ProviderError(f"nasdaq_trader: {source}: no symbols parsed")
    return ListingFile(source=source, created_at=created, rows=rows)


class NasdaqTraderProvider:
    name = "nasdaq_trader"

    def __init__(
        self,
        user_agent: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
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
        return bool((await self.nasdaq_listed()).rows)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def nasdaq_listed(self) -> ListingFile:
        text = await self.http.get_text(NASDAQ_LISTED_URL, ttl=6 * 3600)
        return parse_listing_file(text, "nasdaqtrader:nasdaqlisted")

    async def other_listed(self) -> ListingFile:
        text = await self.http.get_text(OTHER_LISTED_URL, ttl=6 * 3600)
        return parse_listing_file(text, "nasdaqtrader:otherlisted")

    async def all_listings(self) -> tuple[list[Listing], datetime | None]:
        """Both directories, de-duplicated by symbol (Nasdaq-listed wins). Also returns the
        newest 'File Creation Time' as the as-of time of the listing."""
        nq, other = await self.nasdaq_listed(), await self.other_listed()
        seen: dict[str, Listing] = {r.symbol: r for r in nq.rows}
        for r in other.rows:
            seen.setdefault(r.symbol, r)
        stamps = [t for t in (nq.created_at, other.created_at) if t]
        return list(seen.values()), (max(stamps) if stamps else None)
