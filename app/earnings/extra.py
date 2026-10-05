"""Extra free sources: Finnhub company news, SEC 8-K event risk, FINRA short interest, and the
(unofficial) Nasdaq.com earnings calendar used to cross-check the consensus EPS.

Same rules as sources.py: best effort, never raise into the scan, and every figure is labelled with
where it came from.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.earnings.models import ConsensusInfo, FilingEvent, Headline, ShortInterestInfo
from app.earnings.sources import FINNHUB, score_headline
from app.services.providers.base import HttpClient

NASDAQ_CAL = "https://api.nasdaq.com/api/calendar/earnings"

# 8-K item numbers that matter before a report, with a crude tilt (negative = bearish).
ITEM_TILT = {
    "4.02": (-1.0, "non-reliance on prior financials (possible restatement)"),
    "3.01": (-1.0, "listing-standard notice"),
    "1.03": (-1.0, "bankruptcy"),
    "2.06": (-0.6, "material impairment"),
    "2.04": (-0.6, "debt acceleration"),
    "5.02": (-0.4, "officer or director change"),
    "2.05": (-0.3, "restructuring costs"),
    "5.07": (0.0, "shareholder vote"),
    "2.02": (0.0, "results release (check the date: the report may already be out)"),
    "1.01": (0.0, "material agreement"),
    "8.01": (0.0, "other event"),
    "7.01": (0.0, "Reg FD disclosure"),
}


# ---- news -------------------------------------------------------------------------------------
async def finnhub_company_news(
    http: HttpClient, key: str, ticker: str, today: date, days: int = 7
) -> list[Headline]:
    data = await http.get_json(
        f"{FINNHUB}/company-news",
        params={
            "symbol": ticker,
            "from": (today - timedelta(days=days)).isoformat(),
            "to": today.isoformat(),
            "token": key,
        },
        ttl=3600,
    )
    out: list[Headline] = []
    for r in data if isinstance(data, list) else []:
        title = str(r.get("headline") or "").strip()
        if not title:
            continue
        ts = r.get("datetime")
        out.append(
            Headline(
                title=title,
                publisher=r.get("source") or None,
                published_at=datetime.fromtimestamp(ts) if isinstance(ts, (int, float)) else None,
                url=r.get("url") or None,
                score=score_headline(title),
            )
        )
    return out


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:80]


def _naive(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.min
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo else ts


def merge_headlines(*lists: list[Headline], limit: int = 15) -> list[Headline]:
    seen: set[str] = set()
    out: list[Headline] = []
    for hl in lists:
        for h in hl:
            k = _key(h.title)
            if k and k not in seen:
                seen.add(k)
                out.append(h)
    out.sort(key=lambda h: _naive(h.published_at), reverse=True)
    return out[:limit]


# ---- SEC 8-K ----------------------------------------------------------------------------------
async def sec_8k_events(sec: Any, cik: int, today: date, days: int) -> list[FilingEvent]:
    filings = await sec.recent_filings(cik, since=today - timedelta(days=days), forms=("8-K",))
    out = []
    for f in sorted(filings, key=lambda x: x.filed_at, reverse=True)[:12]:
        notes = [ITEM_TILT[i][1] for i in f.items if i in ITEM_TILT and ITEM_TILT[i][0] < 0]
        out.append(FilingEvent(form="8-K", filed=f.filed_at, items=f.items, note="; ".join(notes)))
    return out


def filing_tilt(events: list[FilingEvent]) -> float:
    """Sum of item tilts, capped to [-1, 0]: 8-Ks can warn but they rarely predict a pop."""
    t = sum(ITEM_TILT[i][0] for e in events for i in e.items if i in ITEM_TILT)
    return max(-1.0, min(0.0, t))


# ---- FINRA short interest ---------------------------------------------------------------------
async def short_interest_map(finra: Any, today: date) -> dict[str, ShortInterestInfo]:
    rows = await finra.latest_short_interest(today)
    out: dict[str, ShortInterestInfo] = {}
    for r in rows:
        out[r.ticker] = ShortInterestInfo(
            settlement_date=r.settlement_date,
            shares=r.short_interest_shares,
            change_pct=r.change_percent,
            days_to_cover=r.days_to_cover,
        )
    return out


def short_interest_flag(si: ShortInterestInfo | None) -> str | None:
    if si and si.days_to_cover and si.days_to_cover >= 6:
        return (
            f"High short interest ({si.days_to_cover:.1f} days to cover): a beat can squeeze the stock "
            "up, a miss can accelerate the drop"
        )
    return None


# ---- Nasdaq calendar (unofficial) -------------------------------------------------------------
def _eps(v: Any) -> float | None:
    s = str(v or "").strip()
    if not s or s.upper() in {"N/A", "--"}:
        return None
    neg = s.startswith("(") or s.startswith("-")
    s = re.sub(r"[^0-9.]", "", s)
    try:
        return -float(s) if neg else float(s)
    except ValueError:
        return None


async def nasdaq_consensus(http: HttpClient, day: date) -> dict[str, dict[str, Any]]:
    data = await http.get_json(NASDAQ_CAL, params={"date": day.isoformat()}, ttl=6 * 3600)
    rows = ((data or {}).get("data") or {}).get("rows") or []
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper().replace(".", "-")
        if not sym:
            continue
        t = str(r.get("time") or "")
        n = re.sub(r"\D", "", str(r.get("noOfEsts") or ""))
        out[sym] = {
            "eps": _eps(r.get("epsForecast")),
            "n": int(n) if n else None,
            "last_year_eps": _eps(r.get("lastYearEPS")),
            "session": "bmo" if "pre" in t else "amc" if "after" in t else None,
        }
    return out


def build_consensus(finnhub_eps: float | None, nas: dict[str, Any] | None) -> ConsensusInfo | None:
    if finnhub_eps is None and not nas:
        return None
    nas = nas or {}
    c = ConsensusInfo(
        finnhub_eps=finnhub_eps,
        nasdaq_eps=nas.get("eps"),
        n_estimates=nas.get("n"),
        last_year_eps=nas.get("last_year_eps"),
    )
    if c.finnhub_eps is not None and c.nasdaq_eps is not None:
        base = max(abs(c.finnhub_eps), abs(c.nasdaq_eps), 0.05)
        c.disagreement_pct = round(abs(c.finnhub_eps - c.nasdaq_eps) / base * 100, 1)
    return c


def consensus_flag(c: ConsensusInfo | None) -> str | None:
    if c and c.disagreement_pct is not None and c.disagreement_pct >= 10:
        return (
            f"Consensus EPS sources disagree: Finnhub {c.finnhub_eps:+.2f} vs Nasdaq "
            f"{c.nasdaq_eps:+.2f} ({c.disagreement_pct:.0f}% apart). A 'beat' depends on which "
            "number the market uses."
        )
    return None
