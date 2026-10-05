"""Politician trades from the public Capitol Trades website (HTML, no key, read-only).

Capitol Trades republishes the STOCK Act filings that members of Congress file with the House Clerk
and the Senate. The site is server-rendered, so a plain GET returns a table we can parse. Rules we
follow: robots.txt is checked first, requests are rate limited by the shared HttpClient, the user
agent is honest, and the page count is capped. Trades are filed up to 45 days late, so this is a
slow, weak signal; amounts are ranges, so only the midpoint is kept.
"""

import re
import urllib.robotparser
from datetime import date, timedelta
from html import unescape

from app.earnings.models import PoliticianTrade
from app.services.providers.base import HttpClient, ProviderError

BASE = "https://www.capitoltrades.com"
PAGE_SIZE = 96
MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_ROW = re.compile(r"<tr\b.*?</tr>", re.S)
_CELL = re.compile(r"<td\b.*?</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_DATE = re.compile(r"(\d{1,2})\s*([A-Za-z]{3,9})\.?\s*(\d{4})")
_SIZE = re.compile(r"([\d.]+)\s*([KkMm]?)\s*[–\-]\s*([\d.]+)\s*([KkMm]?)")


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", html))).strip()


def parse_capitol_date(s: str) -> date | None:
    m = _DATE.search(s)
    if not m or m.group(2)[:3].lower() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    except ValueError:
        return None


def _money(num: str, unit: str) -> float:
    return float(num) * {"k": 1e3, "m": 1e6}.get(unit.lower(), 1.0)


def parse_size_mid(s: str) -> float | None:
    """'1K-15K' -> 8000, '250K-500K' -> 375000, '25M-50M' -> 37.5M (unit may be on one side)."""
    m = _SIZE.search(s)
    if not m:
        return None
    lo_n, lo_u, hi_n, hi_u = m.groups()
    hi_u = hi_u or lo_u
    return (_money(lo_n, lo_u or hi_u) + _money(hi_n, hi_u)) / 2


def parse_capitol_html(html: str) -> list[dict]:
    """One dict per table row: ticker, politician, chamber, side, amount_mid, traded, published."""
    out = []
    for row in _ROW.findall(html):
        cells = _CELL.findall(row)
        if len(cells) < 9:
            continue
        tk = re.search(r"issuer-ticker[^>]*>\s*([A-Z0-9.\-/]+):[A-Z]{2}\b", cells[1])
        name = re.search(r"<h2[^>]*>\s*(?:<a[^>]*>)?(.*?)(?:</a>)?\s*</h2>", cells[0], re.S)
        side = _text(cells[6]).lower()
        if not tk or not name or side not in {"buy", "sell"}:
            continue  # bonds, funds without tickers, headers, exchanges
        chamber = re.search(r"chamber--(house|senate)", cells[0])
        out.append(
            {
                "ticker": tk.group(1).replace(".", "-").replace("/", "-"),
                "politician": _text(name.group(1)),
                "chamber": chamber.group(1) if chamber else None,
                "side": side,
                "amount_mid": parse_size_mid(_text(cells[7])),
                "published": parse_capitol_date(_text(cells[2])),
                "traded": parse_capitol_date(_text(cells[3])),
                "owner": _text(cells[5]),
            }
        )
    return out


async def _robots_ok(http: HttpClient, path: str) -> bool:
    try:
        txt = await http.get_text(f"{BASE}/robots.txt", ttl=24 * 3600)
    except ProviderError:
        return True  # no robots file reachable: nothing forbids it
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(txt.splitlines())
    return rp.can_fetch(http.user_agent, f"{BASE}{path}")


async def load_capitol_trades(
    http: HttpClient, today: date, lookback_days: int = 75, max_pages: int = 30
) -> dict[str, list[PoliticianTrade]]:
    """Newest-first pages until trades are older than the lookback. Raises ProviderError."""
    if not await _robots_ok(http, "/trades"):
        raise ProviderError("capitol trades: robots.txt disallows this crawler")
    cutoff = today - timedelta(days=lookback_days)
    by_ticker: dict[str, list[PoliticianTrade]] = {}
    for page in range(1, max_pages + 1):
        html = await http.get_text(
            f"{BASE}/trades", params={"pageSize": PAGE_SIZE, "page": page}, ttl=6 * 3600
        )
        rows = parse_capitol_html(html)
        if not rows:
            if page == 1:
                raise ProviderError("capitol trades: no table rows found (page layout changed?)")
            break
        for r in rows:
            by_ticker.setdefault(r["ticker"], []).append(
                PoliticianTrade(
                    politician=r["politician"],
                    chamber=r["chamber"],
                    side=r["side"],
                    amount_mid_usd=r["amount_mid"],
                    transaction_date=r["traded"],
                    disclosure_date=r["published"],
                )
            )
        newest_on_page = max((r["published"] for r in rows if r["published"]), default=None)
        oldest_on_page = min((r["published"] for r in rows if r["published"]), default=None)
        if (oldest_on_page or newest_on_page or today) < cutoff:
            break
    return by_ticker
