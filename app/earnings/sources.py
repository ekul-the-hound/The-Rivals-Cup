"""Data collectors for the earnings scan. Every collector is best effort: it returns None or an
empty result with a reason instead of raising, so one broken feed never sinks the whole scan.

Free sources only: Finnhub free tier (earnings history, analyst recommendations), SEC EDGAR (Form 4
insider filings and 13F holdings of big institutions), Google News RSS, Yahoo Finance prices and
options, and an optional politician-trades JSON feed you point at with POLITICIAN_TRADES_URL.
"""

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Any

from app.earnings.models import (
    Headline,
    HistoryStats,
    InsiderSummary,
    InstitutionHolding,
    PoliticianTrade,
)
from app.services.providers.base import HttpClient, ProviderError

FINNHUB = "https://finnhub.io/api/v1"
KNOWN_HOLDERS = {
    1364742: "BlackRock",
    102909: "Vanguard Group",
    93751: "State Street",
    1067983: "Berkshire Hathaway",
}


def _d(v: Any) -> date | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "T" in fmt else s[:10], fmt).date()
        except ValueError:
            continue
    return None


# ----------------------------------------------------------------------------------------------
# Earnings history (Finnhub free tier) -> beat rate and earnings-day reactions
# ----------------------------------------------------------------------------------------------
async def finnhub_history(http: HttpClient, key: str, ticker: str, today: date) -> list[dict]:
    """Past report dates with actual vs estimated EPS (about two years)."""
    data = await http.get_json(
        f"{FINNHUB}/calendar/earnings",
        params={
            "symbol": ticker,
            "from": (today - timedelta(days=760)).isoformat(),
            "to": (today - timedelta(days=1)).isoformat(),
            "token": key,
        },
        ttl=12 * 3600,
    )
    out = []
    for r in (data or {}).get("earningsCalendar", []) or []:
        d = _d(r.get("date"))
        if d:
            out.append(
                {
                    "date": d,
                    "hour": (r.get("hour") or "").lower(),
                    "eps_actual": r.get("epsActual"),
                    "eps_estimate": r.get("epsEstimate"),
                }
            )
    return sorted(out, key=lambda x: x["date"], reverse=True)


def pair_surprises_with_filings(rows: list[dict], filing_dates: list[date]) -> list[dict]:
    """Join Finnhub quarterly actual/estimate rows with SEC 8-K Item 2.02 dates.

    The report date is the first earnings 8-K filed 1..100 days after the fiscal period ends.
    """
    out, used = [], set()
    for r in rows:
        period, act, est = _d(r.get("period")), r.get("actual"), r.get("estimate")
        if not period or act is None or est is None:
            continue
        hit = next(
            (d for d in sorted(filing_dates) if d not in used and 0 < (d - period).days <= 100),
            None,
        )
        if hit:
            used.add(hit)
            out.append({"date": hit, "hour": "", "eps_actual": act, "eps_estimate": est})
    return sorted(out, key=lambda x: x["date"], reverse=True)


async def history_with_fallback(
    http: HttpClient, key: str, sec: Any, cik: int | None, ticker: str, today: date
) -> list[dict]:
    """Finnhub calendar history; if the free tier returns no actuals, rebuild it from
    Finnhub /stock/earnings (last quarters) plus SEC 8-K Item 2.02 report dates."""
    events = await finnhub_history(http, key, ticker, today)
    if any(e["eps_actual"] is not None and e["eps_estimate"] is not None for e in events):
        return events
    if sec is None or cik is None:
        return events
    rows = await http.get_json(
        f"{FINNHUB}/stock/earnings",
        params={"symbol": ticker, "limit": 8, "token": key},
        ttl=12 * 3600,
    )
    filings = await sec.recent_filings(cik, since=today - timedelta(days=800), forms=("8-K",))
    dates = [f.filed_at for f in filings if "2.02" in f.items]
    return pair_surprises_with_filings(rows if isinstance(rows, list) else [], dates) or events


def reaction_for(
    event_date: date, hour: str, dates: list[date], closes: list[float]
) -> float | None:
    """Close-to-close move that contains the market's first reaction to the report."""
    i = bisect_left(dates, event_date)
    if i >= len(dates) or dates[i] != event_date or i < 1:
        return None
    if hour in ("bmo", "dmh"):
        return closes[i] / closes[i - 1] - 1
    if hour == "amc":
        return closes[i + 1] / closes[i] - 1 if i + 1 < len(dates) else None
    return closes[i + 1] / closes[i - 1] - 1 if i + 1 < len(dates) else None  # time unknown


def history_stats(events: list[dict], dates: list[date], closes: list[float]) -> HistoryStats:
    scored = [e for e in events if e["eps_actual"] is not None and e["eps_estimate"] is not None]
    last8 = scored[:8]
    stats = HistoryStats(events_used=len(last8))
    if last8:
        stats.beat_rate = round(
            sum(e["eps_actual"] > e["eps_estimate"] for e in last8) / len(last8), 4
        )
        stats.avg_surprise_pct = round(
            sum(
                (e["eps_actual"] - e["eps_estimate"]) / max(abs(e["eps_estimate"]), 0.01)
                for e in last8
            )
            / len(last8)
            * 100,
            2,
        )
    moves = []
    for e in events[:12]:
        r = reaction_for(e["date"], e["hour"], dates, closes)
        if r is not None:
            moves.append(r)
    stats.reactions = len(moves)
    if moves:
        mean_abs = sum(abs(m) for m in moves) / len(moves)
        ups = [m for m in moves if m > 0]
        downs = [-m for m in moves if m < 0]
        stats.p_up_hist = round(len(ups) / len(moves), 4)
        stats.mean_abs_move = round(mean_abs, 4)
        if ups and mean_abs:
            stats.up_mag = round(min(1.6, max(0.6, (sum(ups) / len(ups)) / mean_abs)), 3)
        if downs and mean_abs:
            stats.down_mag = round(min(1.6, max(0.6, (sum(downs) / len(downs)) / mean_abs)), 3)
        stats.last_moves = [round(m, 4) for m in moves[:8]]
    return stats


# ----------------------------------------------------------------------------------------------
# Analysts (Finnhub recommendation trend)
# ----------------------------------------------------------------------------------------------
def analyst_scores(recs: list[Any]) -> tuple[float | None, float | None, str]:
    """(level, trend, detail). level in [-1, 1] from the newest month; trend is newest minus the
    oldest of up to the last three months."""
    recs = sorted(recs, key=lambda r: r.period, reverse=True)

    def score(r: Any) -> float | None:
        n = r.strong_buy + r.buy + r.hold + r.sell + r.strong_sell
        if n < 3:
            return None
        return (2 * r.strong_buy + r.buy - r.sell - 2 * r.strong_sell) / (2 * n)

    if not recs:
        return None, None, "no analyst data"
    cur = score(recs[0])
    if cur is None:
        return None, None, "fewer than 3 analysts"
    r0 = recs[0]
    detail = (
        f"{r0.strong_buy} strong buy / {r0.buy} buy / {r0.hold} hold / {r0.sell} sell / "
        f"{r0.strong_sell} strong sell ({r0.period})"
    )
    older = [score(r) for r in recs[1:3]]
    older = [x for x in older if x is not None]
    trend = (cur - older[-1]) if older else None
    return cur, trend, detail


# ----------------------------------------------------------------------------------------------
# News: transparent keyword lexicon (no model, easy to audit)
# ----------------------------------------------------------------------------------------------
POSITIVE = {
    "beat", "beats", "tops", "exceeds", "surge", "surges", "soars", "jumps", "rallies", "raises",
    "raised", "upgrade", "upgraded", "upgrades", "record", "strong", "outperform", "bullish",
    "boost", "boosts", "growth", "accelerates", "wins", "approval", "approved", "buyback",
    "outperforms", "optimistic", "rebound", "rebounds", "expands", "dividend",
}  # fmt: skip
NEGATIVE = {
    "miss", "misses", "missed", "plunge", "plunges", "falls", "drops", "slump", "slumps", "cut",
    "cuts", "downgrade", "downgraded", "downgrades", "weak", "warns", "warning", "lawsuit",
    "probe", "investigation", "bearish", "layoffs", "recall", "fraud", "underperform", "decline",
    "declines", "slashes", "tumbles", "sinks", "delay", "delays", "shortfall", "default", "halt",
}  # fmt: skip
_WORD = re.compile(r"[a-z']+")


def score_headline(title: str) -> int:
    words = _WORD.findall(title.lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    return (pos > neg) - (neg > pos)


def to_headlines(stories: list[Any], limit: int = 12) -> list[Headline]:
    out = []
    for s in stories[:limit]:
        out.append(
            Headline(
                title=s.headline,
                publisher=s.publisher_name,
                published_at=s.published_at,
                url=s.url,
                score=score_headline(s.headline),
            )
        )
    return out


# ----------------------------------------------------------------------------------------------
# Insiders: SEC Form 4 (company officers, directors, 10% owners)
# ----------------------------------------------------------------------------------------------
async def insider_summary(
    sec: Any, cik: int, today: date, lookback_days: int, max_filings: int = 24
) -> InsiderSummary:
    filings = await sec.recent_filings(
        cik, since=today - timedelta(days=lookback_days), forms=("4",)
    )
    filings = sorted(filings, key=lambda f: f.filed_at, reverse=True)[:max_filings]
    summaries = await asyncio.gather(*(sec.form4_summary(f) for f in filings))
    out = InsiderSummary(filings_seen=len(filings))
    buyers: set[str] = set()
    sellers: set[str] = set()
    for s in summaries:
        if not s:
            continue
        who = s.owner or "unknown"
        for tx in s.transactions:
            code = tx.get("code")
            val = float(tx.get("value_usd") or 0.0)
            if code == "P":  # open-market purchase
                out.open_market_buys += 1
                out.buy_value_usd += abs(val)
                buyers.add(who)
            elif code == "S":  # open-market sale (often pre-planned, so weighed lightly)
                out.open_market_sells += 1
                out.sell_value_usd += abs(val)
                sellers.add(who)
    out.buyers, out.sellers = len(buyers), len(sellers)
    out.buy_value_usd = round(out.buy_value_usd, 2)
    out.sell_value_usd = round(out.sell_value_usd, 2)
    if len(filings) == max_filings:
        out.note = f"capped at the newest {max_filings} Form 4 filings"
    return out


# ----------------------------------------------------------------------------------------------
# Politicians: optional JSON feed (House/Senate Stock Watcher style) or paid Finnhub endpoint
# ----------------------------------------------------------------------------------------------
_AMT = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(?:-|to)?\s*\$?\s*([\d,]+(?:\.\d+)?)?")


def parse_amount_mid(text: Any) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    m = _AMT.search(str(text or ""))
    if not m:
        return None
    lo = float(m.group(1).replace(",", ""))
    hi = float(m.group(2).replace(",", "")) if m.group(2) else lo
    return (lo + hi) / 2


def normalize_politician_record(r: dict) -> tuple[str, PoliticianTrade] | None:
    ticker = str(r.get("ticker") or r.get("symbol") or "").strip().upper().replace(".", "-")
    if not ticker or ticker in {"--", "N/A", "NA"}:
        return None
    kind = str(r.get("type") or r.get("transaction_type") or r.get("transactionType") or "").lower()
    if "purchase" in kind or kind.startswith("buy"):
        side = "buy"
    elif "sale" in kind or "sell" in kind:
        side = "sell"
    else:
        return None
    name = (
        r.get("representative")
        or r.get("senator")
        or r.get("politician")
        or r.get("name")
        or r.get("senator_name")
        or "unknown"
    )
    chamber = r.get("chamber") or (
        "senate" if "senator" in r else "house" if "representative" in r else None
    )
    amount = r.get("amount") or r.get("amount_range")
    if amount is None and r.get("amountFrom") is not None:
        amount = (float(r["amountFrom"]) + float(r.get("amountTo") or r["amountFrom"])) / 2
    return ticker, PoliticianTrade(
        politician=str(name).strip(),
        chamber=chamber,
        side=side,
        amount_mid_usd=parse_amount_mid(amount),
        transaction_date=_d(r.get("transaction_date") or r.get("transactionDate")),
        disclosure_date=_d(r.get("disclosure_date") or r.get("filingDate")),
    )


class PoliticianFeed:
    """Loads one JSON feed per run. Disabled (with a reason) when no URL is configured."""

    def __init__(
        self, http: HttpClient | None, url: str, web: bool = False, today: date | None = None
    ) -> None:
        self.http, self.url, self.web, self.today = http, url, web, today
        self._by_ticker: dict[str, list[PoliticianTrade]] | None = None
        self.error: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.http and (self.url or self.web))

    async def load(self) -> None:
        if self._by_ticker is not None or not self.configured:
            return
        self._by_ticker = {}
        if not self.url:  # no JSON feed configured: read the public Capitol Trades pages
            from app.earnings.capitol import load_capitol_trades

            try:
                self._by_ticker = await load_capitol_trades(self.http, self.today or date.today())
            except ProviderError as exc:
                self.error = str(exc)
            return
        try:
            data = await self.http.get_json(self.url, ttl=6 * 3600)
        except ProviderError as exc:
            self.error = str(exc)
            return
        if isinstance(data, dict):
            data = data.get("data") or data.get("transactions") or []
        if not isinstance(data, list):
            self.error = "politician feed is not a list of records"
            return
        for r in data:
            if isinstance(r, dict) and (n := normalize_politician_record(r)):
                self._by_ticker.setdefault(n[0], []).append(n[1])

    def for_ticker(
        self, ticker: str, today: date, lookback_days: int = 120
    ) -> list[PoliticianTrade]:
        cutoff = today - timedelta(days=lookback_days)
        rows = (self._by_ticker or {}).get(ticker.upper(), [])
        keep = [t for t in rows if (t.transaction_date or t.disclosure_date or date.min) >= cutoff]
        return sorted(keep, key=lambda t: t.transaction_date or date.min, reverse=True)[:25]


async def finnhub_congress(
    http: HttpClient, key: str, ticker: str, today: date, lookback_days: int = 120
) -> list[PoliticianTrade]:
    data = await http.get_json(
        f"{FINNHUB}/stock/congressional-trading",
        params={
            "symbol": ticker,
            "from": (today - timedelta(days=lookback_days)).isoformat(),
            "to": today.isoformat(),
            "token": key,
        },
        ttl=6 * 3600,
    )
    out = []
    for r in (data or {}).get("data", []) or []:
        n = normalize_politician_record({**r, "ticker": r.get("symbol") or ticker})
        if n:
            out.append(n[1])
    return out


# ----------------------------------------------------------------------------------------------
# Institutions: SEC 13F-HR filings (BlackRock, Vanguard, ...). Quarterly and up to 45 days stale.
# ----------------------------------------------------------------------------------------------
_DROP = {
    "INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "HOLDINGS", "HLDGS",
    "GROUP", "THE", "NEW", "DEL", "COM", "SA", "NV", "LP", "CL", "CLASS",
}  # fmt: skip


def norm_name(name: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND "))
    toks = s.split()
    while toks and toks[-1] in _DROP:
        toks.pop()
    return " ".join(t for t in toks if t != "THE")


def parse_infotable(xml_text: str) -> dict[str, float]:
    """normalised issuer name -> common shares held (options and bonds excluded)."""
    out: dict[str, float] = {}
    try:
        for _, el in ET.iterparse(_Bytes(xml_text), events=("end",)):
            if not el.tag.endswith("infoTable"):
                continue
            f: dict[str, str] = {}
            for node in el.iter():
                f[node.tag.rsplit("}", 1)[-1]] = (node.text or "").strip()
            el.clear()
            if f.get("putCall") or f.get("sshPrnamtType", "SH") != "SH":
                continue
            try:
                shares = float(f.get("sshPrnamt", "0"))
            except ValueError:
                continue
            key = norm_name(f.get("nameOfIssuer", ""))
            if key:
                out[key] = out.get(key, 0.0) + shares
    except ET.ParseError:
        return out
    return out


class _Bytes:
    """File-like wrapper so ElementTree can stream a str."""

    def __init__(self, text: str) -> None:
        self._b = text.encode("utf-8")
        self._i = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._b) - self._i
        chunk = self._b[self._i : self._i + n]
        self._i += n
        return chunk


class InstitutionalFeed:
    def __init__(self, sec: Any, ciks: list[int], max_filings: int = 2) -> None:
        self.sec, self.ciks, self.max_filings = sec, ciks, max_filings
        self._data: dict[int, list[tuple[date | None, dict[str, float]]]] | None = None
        self.errors: list[str] = []

    @property
    def configured(self) -> bool:
        return bool(self.sec and self.ciks)

    async def _filing_table(self, cik: int, accession: str) -> str | None:
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
        idx = await self.sec.http.get_json(f"{base}/index.json", ttl=90 * 86400)
        items = (idx.get("directory") or {}).get("item") or []
        xmls = [
            i
            for i in items
            if str(i.get("name", "")).lower().endswith(".xml")
            and "primary_doc" not in str(i.get("name", "")).lower()
        ]
        if not xmls:
            return None
        xmls.sort(
            key=lambda i: (
                "infotable" in str(i["name"]).lower(),
                int(i.get("size") or 0) if str(i.get("size") or "0").isdigit() else 0,
            ),
            reverse=True,
        )
        return await self.sec.http.get_text(f"{base}/{xmls[0]['name']}", ttl=90 * 86400)

    async def load(self, today: date) -> None:
        if self._data is not None or not self.configured:
            return
        self._data = {}
        for cik in self.ciks:
            try:
                filings = await self.sec.recent_filings(
                    cik, since=today - timedelta(days=330), forms=("13F-HR",)
                )
                filings = sorted(filings, key=lambda f: f.filed_at, reverse=True)[
                    : self.max_filings
                ]
                tables = []
                for f in filings:
                    text = await self._filing_table(cik, f.accession_number)
                    if text:
                        tables.append((f.period_of_report, parse_infotable(text)))
                self._data[cik] = tables
            except (ProviderError, KeyError, ValueError, TypeError) as exc:
                self.errors.append(f"{KNOWN_HOLDERS.get(cik, cik)}: {exc}")

    def holdings(self, issuer_name: str) -> list[InstitutionHolding]:
        key = norm_name(issuer_name)
        out: list[InstitutionHolding] = []
        for cik, tables in (self._data or {}).items():
            if not tables or key not in tables[0][1]:
                continue
            latest = tables[0][1][key]
            prior = tables[1][1].get(key) if len(tables) > 1 else None
            chg = (latest - prior) / prior if prior else None
            out.append(
                InstitutionHolding(
                    institution=KNOWN_HOLDERS.get(cik, f"CIK {cik}"),
                    shares_latest=latest,
                    shares_prior=prior,
                    change_pct=round(chg, 4) if chg is not None else None,
                    period_latest=tables[0][0],
                )
            )
        return out


def dump_json(obj: Any) -> str:
    return json.dumps(obj, default=str, indent=2)
