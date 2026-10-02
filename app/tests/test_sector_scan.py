"""Universe parsing + sector scan. No network; synthetic prices."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from app.db.memory import InMemoryDB
from app.services.providers.base import ProviderError
from app.services.scan.sector_scan import ScanParams, render_text, scan_sectors
from app.services.universe.sp500 import (
    normalize_ticker,
    parse_constituents_html,
    select_sectors,
)

HTML = """
<html><body>
<table class="other"><tr><th>x</th></tr><tr><td>1</td></tr></table>
<table class="wikitable sortable" id="constituents">
<tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th><th>Headquarters</th></tr>
<tr><td><a href="#">MMM</a></td><td><a>3M</a></td><td>Industrials</td><td>Industrial Conglomerates</td><td>MN</td></tr>
<tr><td><a>BRK.B</a></td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td><td>NE</td></tr>
<tr><td><a>NEE</a></td><td>NextEra<sup>[1]</sup></td><td>Utilities</td><td>Electric Utilities</td><td>FL</td></tr>
<tr><td><a>AAPL</a></td><td>Apple</td><td>Information Technology</td><td>Hardware</td><td>CA</td></tr>
</tbody></table></body></html>
"""


def test_parse_and_select():
    items = parse_constituents_html(HTML)
    assert [c.ticker for c in items] == ["MMM", "BRK-B", "NEE", "AAPL"]
    assert items[2].name == "NextEra[1]" or items[2].name.startswith("NextEra")
    chosen = select_sectors(items, ("Industrials", "financials", "Utilities"))
    assert {c.ticker for c in chosen} == {"MMM", "BRK-B", "NEE"}
    assert normalize_ticker(" bf.b ") == "BF-B"


def test_parse_missing_table():
    with pytest.raises(ProviderError):
        parse_constituents_html("<html><table><tr><th>a</th></tr></table></html>")


def _bars(rets: np.ndarray, start: date, vol: float = 2_000_000, px: float = 100.0):
    out, p = [], px
    for i, r in enumerate(rets):
        p *= 1 + r
        d = start + timedelta(days=i)
        out.append(
            {
                "bar_date": d.isoformat(),
                "timeframe": "1D",
                "close": p,
                "adj_close": p,
                "volume": vol,
            }
        )
    return out


def _db(week: date):
    rng = np.random.default_rng(7)
    n = 90
    start = week - timedelta(days=n + 1)
    common = rng.normal(0, 0.01, n)
    secs, bars = [], []
    spec = {"AAA": 0.0, "BBB": 0.0, "CCC": 0.0, "DDD": 0.0}
    for i, (tk, _) in enumerate(spec.items()):
        idio = rng.normal(0, 0.003, n)
        if tk == "AAA":  # strong recent leader vs its peer BBB
            idio[-20:] += 0.004
        r = rng.normal(0, 0.01, n) if tk == "DDD" else common + idio  # DDD: unrelated name
        sid = f"s{i}"
        secs.append({"id": sid, "ticker": tk, "name": tk, "sector": "Utilities", "industry": "Electric" if tk != "CCC" else "Gas",
                     "is_etf": False, "security_type": "EQUITY", "is_active": True})  # fmt: skip
        for b in _bars(r, start):
            bars.append({**b, "security_id": sid})
    secs.append({"id": "etf", "ticker": "XLU", "name": "ETF", "sector": "Utilities", "is_etf": True,
                 "security_type": "ETF", "is_active": True})  # fmt: skip
    for b in _bars(common, start):
        bars.append({**b, "security_id": "etf"})
    return InMemoryDB(
        {
            "securities": secs,
            "market_bars": bars,
            "sector_etf_mappings": [
                {"sector": "Utilities", "etf_security_id": "etf", "is_primary": True}
            ],
            "security_event_blackouts": [],
        }
    )


def test_scan_ranks_correlated_pairs_and_is_read_only():
    week = date(2026, 9, 28)
    db = _db(week)
    before = {t: len(r) for t, r in db.data.items()}
    now = datetime(2026, 9, 30, 15, tzinfo=UTC)
    res = scan_sectors(
        db, ["Utilities", "Real Estate"], now, week, ScanParams(top_n=5, max_per_stock=3)
    )
    assert {t: len(r) for t, r in db.data.items()} == before  # nothing written
    util = res.sectors[0]
    assert util.universe == 4 and util.with_data == 4 and util.etf == "XLU"
    assert util.pairs, "correlated names should produce pairs"
    top = util.pairs[0]
    assert {top.long, top.short} <= {"AAA", "BBB", "CCC"}  # DDD is uncorrelated
    assert all("DDD" not in (p.long, p.short) for p in util.pairs)
    assert top.corr_60d >= 0.6 and top.est_cap_usd > 0
    assert res.sectors[1].universe == 0
    text = render_text(res)
    assert "RESEARCH SCREEN ONLY" in text and "no active stocks" in text


def test_event_blackout_excludes_stock_and_illiquid_filter():
    week = date(2026, 9, 28)
    db = _db(week)
    db.data["security_event_blackouts"] = [
        {
            "security_id": "s0",
            "week_start": week.isoformat(),
            "earnings_date_if_known": "2026-09-30",
        }
    ]
    now = datetime(2026, 9, 30, 15, tzinfo=UTC)
    res = scan_sectors(db, ["Utilities"], now, week, ScanParams(top_n=10, max_per_stock=5))
    s = res.sectors[0]
    assert s.event_blocked == ["AAA"]
    assert all("AAA" not in (p.long, p.short) for p in s.pairs)
    big = scan_sectors(db, ["Utilities"], now, week, ScanParams(leg_usd=10**9))
    assert big.sectors[0].pairs == [] and big.sectors[0].pairs_rejected["illiquid"] > 0
