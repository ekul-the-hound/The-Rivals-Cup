"""Sector scan: screen every stock in a sector, then every same-sector pair. READ-ONLY.

This is a SCREEN to decide which pairs deserve a human look, not a trade list and not the
PeerPairEngine score. Direction follows the engine's rule (long the stronger 20/60-day blend,
short the weaker). Nothing here is entered anywhere; you place and record any trade manually.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from math import sqrt
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.db.store import Store
from app.services.features.metrics import adv_dollar, close_series, trailing_returns
from app.services.pairs.blackout import evaluate_leg, scoring_week

NOTE = (
    "RESEARCH SCREEN ONLY. Not a trade list. Enter and record any trade manually in Trader View. "
    "Earnings/event dates, Rival Cup eligibility and peer relationships are NOT verified here."
)


@dataclass
class ScanParams:
    window: int = 60  # daily returns used for correlation and spread volatility
    min_bars: int = 61
    min_corr: float = 0.60
    leg_usd: float = 10_000.0
    adv_cap_pct: float = 0.01  # ESTIMATE of a per-leg liquidity cap, not a verified WSR rule
    top_n: int = 8
    max_per_stock: int = 2


class StockRow(BaseModel):
    ticker: str
    name: str | None
    industry: str | None
    ret_5d: float | None
    ret_20d: float | None
    ret_60d: float | None
    vs_etf_20d: float | None
    adv_20d_usd: float | None
    bars: int
    event_blocked: bool = False
    note: str | None = None


class PairRow(BaseModel):
    rank: int
    long: str
    short: str
    score: float
    corr_60d: float
    spread_20d: float  # long-leg 20d return minus short-leg 20d return
    spread_z: float  # that spread in units of the pair's own 20-day spread volatility
    same_industry: bool
    min_adv_usd: float
    est_cap_usd: float
    industries: str


class SectorScan(BaseModel):
    sector: str
    etf: str | None
    universe: int
    with_data: int
    missing_data: list[str]
    event_blocked: list[str]
    pairs_considered: int
    pairs_rejected: dict[str, int]
    leaders: list[StockRow]
    laggards: list[StockRow]
    pairs: list[PairRow]


class ScanResult(BaseModel):
    week_start: date
    week_end: date
    generated_at: datetime
    sectors: list[SectorScan]
    warnings: list[str]
    note: str = NOTE


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _bars(store: Store, sid: str, limit: int = 130) -> list[dict[str, Any]]:
    rows = store.select(
        "market_bars",
        eq={"security_id": sid, "timeframe": "1D"},
        order="bar_date",
        desc=True,
        limit=limit,
    )
    return sorted(rows, key=lambda r: str(r["bar_date"]))


def _blend(r: dict[int, float | None]) -> float | None:
    if r.get(20) is None:
        return None
    return 0.6 * r[20] + 0.4 * r[60] if r.get(60) is not None else r[20]


def scan_sector(
    store: Store,
    sector: str,
    now: datetime,
    week_start: date | None = None,
    p: ScanParams | None = None,
) -> SectorScan:
    p = p or ScanParams()
    start, end = scoring_week(week_start or now.date())
    if week_start is not None:
        end = week_start + timedelta(days=4)
    secs = [
        s
        for s in store.select("securities", eq={"is_active": True})
        if (s.get("sector") or "").lower() == sector.lower()
        and not s.get("is_etf")
        and (s.get("security_type") or "EQUITY") == "EQUITY"
    ]
    etf_ids = [
        m["etf_security_id"]
        for m in store.select("sector_etf_mappings")
        if (m.get("sector") or "").lower() == sector.lower() and m.get("is_primary", True)
    ]
    etf = next((s for s in store.select("securities") if etf_ids and s["id"] == etf_ids[0]), None)
    etf_r20 = None
    if etf:
        etf_close = close_series(_bars(store, etf["id"]))
        etf_r20 = trailing_returns(etf_close).get(20) if len(etf_close) else None
    blackouts = {
        r["security_id"]: r
        for r in store.select("security_event_blackouts", eq={"week_start": start})
    }

    closes: dict[str, pd.Series] = {}
    rows: dict[str, StockRow] = {}
    adv: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    blocked: list[str] = []
    for s in secs:
        tk = s["ticker"]
        bars = _bars(store, s["id"])
        c = close_series(bars)
        if len(c) < p.min_bars:
            missing.append(tk)
            continue
        r = trailing_returns(c)
        a = adv_dollar(bars)
        leg = evaluate_leg(tk, blackouts.get(s["id"]), start, end, now)
        if leg.blocked:
            blocked.append(tk)
        closes[tk] = c
        meta[tk] = {"sec": s, "r": r}
        if a is not None:
            adv[tk] = a
        rows[tk] = StockRow(
            ticker=tk, name=s.get("name"), industry=s.get("industry"), ret_5d=r.get(5),
            ret_20d=r.get(20), ret_60d=r.get(60),
            vs_etf_20d=(r[20] - etf_r20) if r.get(20) is not None and etf_r20 is not None else None,
            adv_20d_usd=a, bars=len(c), event_blocked=leg.blocked,
            note="; ".join(leg.reasons) or None,
        )  # fmt: skip

    rejected = {"event_blackout": 0, "low_correlation": 0, "illiquid": 0, "no_overlap": 0}
    cands: list[dict[str, Any]] = []
    tickers = sorted(closes)
    considered = 0
    if len(tickers) >= 2:
        frame = pd.DataFrame(closes).sort_index()
        rets = frame.pct_change().tail(p.window)
        corr = rets.corr(min_periods=40)
        for a_tk, b_tk in combinations(tickers, 2):
            considered += 1
            if a_tk in blocked or b_tk in blocked:
                rejected["event_blackout"] += 1
                continue
            c = corr.loc[a_tk, b_tk]
            if pd.isna(c):
                rejected["no_overlap"] += 1
                continue
            if c < p.min_corr:
                rejected["low_correlation"] += 1
                continue
            if (
                a_tk not in adv
                or b_tk not in adv
                or min(adv[a_tk], adv[b_tk]) * p.adv_cap_pct < p.leg_usd
            ):
                rejected["illiquid"] += 1
                continue
            ba, bb = _blend(meta[a_tk]["r"]), _blend(meta[b_tk]["r"])
            if ba is None or bb is None:
                rejected["no_overlap"] += 1
                continue
            lo, sh = (b_tk, a_tk) if bb > ba else (a_tk, b_tk)
            diff = (rets[lo] - rets[sh]).dropna()
            if len(diff) < 20 or diff.std(ddof=1) == 0:
                rejected["no_overlap"] += 1
                continue
            r_l, r_s = meta[lo]["r"].get(20), meta[sh]["r"].get(20)
            if r_l is None or r_s is None:
                rejected["no_overlap"] += 1
                continue
            spread = r_l - r_s
            z = spread / (float(diff.std(ddof=1)) * sqrt(20))
            ind_l, ind_s = meta[lo]["sec"].get("industry"), meta[sh]["sec"].get("industry")
            same = bool(ind_l and ind_l == ind_s)
            min_adv = min(adv[lo], adv[sh])
            score = 100 * (
                0.40 * _clamp((float(c) - 0.5) / 0.4)
                + 0.40 * _clamp(abs(z) / 3.0)
                + 0.10 * (1.0 if same else 0.0)
                + 0.10 * _clamp(min_adv * p.adv_cap_pct / (5 * p.leg_usd))
            )
            cands.append(
                {"long": lo, "short": sh, "score": score, "corr": float(c), "spread": spread, "z": z,
                 "same": same, "adv": min_adv, "ind": f"{ind_l or '?'} / {ind_s or '?'}"}
            )  # fmt: skip

    cands.sort(key=lambda d: (-d["score"], d["long"], d["short"]))
    used: dict[str, int] = {}
    picked: list[PairRow] = []
    for d in cands:
        if used.get(d["long"], 0) >= p.max_per_stock or used.get(d["short"], 0) >= p.max_per_stock:
            continue
        used[d["long"]] = used.get(d["long"], 0) + 1
        used[d["short"]] = used.get(d["short"], 0) + 1
        picked.append(
            PairRow(
                rank=len(picked) + 1, long=d["long"], short=d["short"], score=round(d["score"], 1),
                corr_60d=round(d["corr"], 3), spread_20d=round(d["spread"], 4),
                spread_z=round(d["z"], 2), same_industry=d["same"], min_adv_usd=round(d["adv"], 0),
                est_cap_usd=round(d["adv"] * p.adv_cap_pct, 0), industries=d["ind"],
            )
        )  # fmt: skip
        if len(picked) >= p.top_n:
            break

    ranked = sorted(
        (r for r in rows.values() if r.vs_etf_20d is not None), key=lambda r: r.vs_etf_20d or 0.0
    )
    return SectorScan(
        sector=sector, etf=etf["ticker"] if etf else None, universe=len(secs), with_data=len(closes),
        missing_data=sorted(missing), event_blocked=sorted(blocked), pairs_considered=considered,
        pairs_rejected=rejected, leaders=list(reversed(ranked[-5:])), laggards=ranked[:5], pairs=picked,
    )  # fmt: skip


def scan_sectors(
    store: Store,
    sectors: list[str],
    now: datetime,
    week_start: date | None = None,
    p: ScanParams | None = None,
) -> ScanResult:
    start, end = scoring_week(week_start or now.date())
    if week_start is not None:
        end = week_start + timedelta(days=4)
    out = [scan_sector(store, s, now, week_start, p) for s in sectors]
    warnings = [
        "Earnings and major-event dates are only known for names with a manual blackout row; "
        "check every shortlisted stock's earnings date before using it.",
        "Rival Cup eligibility is UNVERIFIED for every security; confirm each name in Trader View.",
        "Pairs are statistical matches only; no peer relationship has been recorded for them.",
    ]
    for s in out:
        if s.universe == 0:
            warnings.append(
                f"{s.sector}: no active stocks in the universe (run load_sector_universe)."
            )
        elif s.missing_data:
            warnings.append(
                f"{s.sector}: {len(s.missing_data)}/{s.universe} stocks have too little price history "
                "(run refresh_daily_prices)."
            )
    return ScanResult(
        week_start=start, week_end=end, generated_at=now, sectors=out, warnings=warnings
    )


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def render_text(res: ScanResult) -> str:
    L = [f"SECTOR SCAN  week {res.week_start} to {res.week_end}", res.note, ""]
    for s in res.sectors:
        L.append(
            f"=== {s.sector} (ETF {s.etf or 'n/a'}): {s.with_data}/{s.universe} stocks with data ==="
        )
        if s.event_blocked:
            L.append(f"  event in scoring week (excluded): {', '.join(s.event_blocked)}")
        if s.missing_data:
            L.append(f"  insufficient history: {', '.join(s.missing_data[:25])}"
                     + (" ..." if len(s.missing_data) > 25 else ""))  # fmt: skip
        L.append(
            "  20d vs sector ETF  leaders:  "
            + ", ".join(f"{r.ticker} {_pct(r.vs_etf_20d)}" for r in s.leaders)
        )
        L.append(
            "                     laggards: "
            + ", ".join(f"{r.ticker} {_pct(r.vs_etf_20d)}" for r in s.laggards)
        )
        L.append(
            f"  pairs checked {s.pairs_considered}; rejected: "
            + ", ".join(f"{k}={v}" for k, v in s.pairs_rejected.items())
        )
        for r in s.pairs:
            L.append(
                f"  #{r.rank} LONG {r.long} / SHORT {r.short}  score {r.score}  corr {r.corr_60d}  "
                f"20d spread {_pct(r.spread_20d)} (z {r.spread_z:+.2f})  est cap ${r.est_cap_usd:,.0f}  "
                f"{'same industry' if r.same_industry else r.industries}"
            )
        if not s.pairs:
            L.append("  no pairs passed the screen")
        L.append("")
    L += [f"! {w}" for w in res.warnings]
    return "\n".join(L)
