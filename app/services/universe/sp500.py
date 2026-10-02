"""S&P 500 constituents by GICS sector, from Wikipedia's public list (or a CSV you supply).

Research universe only. Membership is a convenience list, NOT a statement that a stock is eligible
for the Rival Cup; eligibility must be verified in Trader View.
"""

import csv
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel

from app.services.providers.base import ProviderError

API = "https://en.wikipedia.org/w/api.php"
PAGE = "List of S&P 500 companies"
DEFAULT_SECTORS = ("Health Care", "Industrials", "Financials", "Utilities", "Real Estate")


class Constituent(BaseModel):
    ticker: str
    name: str
    sector: str
    industry: str | None = None


def normalize_ticker(raw: str) -> str:
    """Yahoo and SEC use a dash for share classes (BRK.B -> BRK-B)."""
    return raw.strip().upper().replace(".", "-").replace(" ", "")


class _Tables(HTMLParser):
    """Collects every <table> as a list of rows of cell texts (stdlib only, no lxml)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("sup", "style", "script") and self._cell is not None:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth = max(0, self._depth - 1)
        elif (
            self._depth == 1
            and tag in ("td", "th")
            and self._cell is not None
            and self._row is not None
        ):
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None
        elif tag in ("sup", "style", "script") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def _col(header: list[str], *names: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for n in names:
        for i, h in enumerate(low):
            if h == n or h.startswith(n):
                return i
    return None


def parse_constituents_html(html: str) -> list[Constituent]:
    """Find the table with Symbol + GICS Sector columns and return its rows."""
    p = _Tables()
    p.feed(html)
    for rows in p.tables:
        if not rows:
            continue
        header = rows[0]
        i_sym = _col(header, "symbol", "ticker")
        i_sec = _col(header, "gics sector", "sector")
        if i_sym is None or i_sec is None:
            continue
        i_name = _col(header, "security", "company", "name")
        i_ind = _col(header, "gics sub-industry", "sub-industry", "industry")
        out = []
        for r in rows[1:]:
            if len(r) <= max(i_sym, i_sec):
                continue
            tk = normalize_ticker(r[i_sym])
            if not tk:
                continue
            out.append(
                Constituent(
                    ticker=tk,
                    name=r[i_name] if i_name is not None and i_name < len(r) else tk,
                    sector=r[i_sec].strip(),
                    industry=(r[i_ind].strip() or None)
                    if i_ind is not None and i_ind < len(r)
                    else None,
                )
            )
        if out:
            return out
    raise ProviderError("wikipedia: S&P 500 constituents table not found (page layout changed?)")


def load_csv(path: str | Path) -> list[Constituent]:
    """CSV fallback. Columns (case-insensitive): ticker|symbol, name|security, sector|gics sector,
    industry|gics sub-industry."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rdr = csv.DictReader(fh)
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rdr]
    out = []
    for r in rows:
        tk = normalize_ticker(r.get("ticker") or r.get("symbol") or "")
        sector = r.get("sector") or r.get("gics sector") or ""
        if tk and sector:
            out.append(
                Constituent(
                    ticker=tk,
                    name=r.get("name") or r.get("security") or tk,
                    sector=sector,
                    industry=r.get("industry") or r.get("gics sub-industry") or None,
                )
            )
    if not out:
        raise ValueError(f"{path}: no usable rows (need ticker and sector columns)")
    return out


async def fetch_constituents(wiki) -> list[Constituent]:
    """One cached MediaWiki API call through the rate-limited Wikipedia provider."""
    data = await wiki.http.get_json(
        API,
        params={
            "action": "parse", "page": PAGE, "prop": "text", "format": "json",
            "formatversion": 2, "disableeditsection": 1, "redirects": 1,
        },
        ttl=86400,
    )  # fmt: skip
    html = (data.get("parse") or {}).get("text")
    if not isinstance(html, str):
        raise ProviderError("wikipedia: unexpected response for S&P 500 list")
    return parse_constituents_html(html)


def select_sectors(items: list[Constituent], sectors: tuple[str, ...]) -> list[Constituent]:
    wanted = {s.lower() for s in sectors}
    seen: set[str] = set()
    out = []
    for c in items:
        if c.sector.lower() in wanted and c.ticker not in seen:
            seen.add(c.ticker)
            out.append(c)
    return sorted(out, key=lambda c: (c.sector, c.ticker))
