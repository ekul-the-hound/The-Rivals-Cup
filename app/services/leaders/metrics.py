"""Price-history features for ranking. Pure functions over stored daily bars (no I/O).

All returns are in percent. Adjusted closes (dividend-adjusted) drive returns and risk; the raw close
is the price. Downside deviation follows the WSR score's form (daily, root-mean-square of negative
returns), because that is the risk the weekly score actually penalizes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np


@dataclass
class Series:
    ticker: str
    dates: list[date]
    close: list[float]
    adj: list[float]
    volume: list[float]
    dividends: list[tuple[date, float]] = field(default_factory=list)
    splits: list[tuple[date, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.dates)

    def as_of(self, d: date) -> Series:
        """Copy containing only bars dated on or before `d` (no future data in a dated calculation)."""
        k = sum(1 for x in self.dates if x <= d)
        return Series(
            self.ticker, self.dates[:k], self.close[:k], self.adj[:k], self.volume[:k],
            [x for x in self.dividends if x[0] <= d], [x for x in self.splits if x[0] <= d],
        )  # fmt: skip


def _d(v: Any) -> date:
    return date.fromisoformat(str(v)[:10])


def series_to_row(
    ticker: str, bars: list[tuple[date, float, float | None, float | None]],
    dividends: list[tuple[date, float]], splits: list[tuple[date, str]],
) -> dict[str, Any]:  # fmt: skip
    """bars: (date, close, adj_close, volume). Builds the universe_price_history row."""
    bars = sorted(bars, key=lambda b: b[0])
    return {
        "ticker": ticker,
        "as_of": bars[-1][0].isoformat(),
        "first_date": bars[0][0].isoformat(),
        "n_bars": len(bars),
        "series": {
            "d": [b[0].isoformat() for b in bars],
            "c": [round(float(b[1]), 4) for b in bars],
            "a": [round(float(b[2] if b[2] is not None else b[1]), 4) for b in bars],
            "v": [float(b[3] or 0) for b in bars],
        },
        "dividends": [[d.isoformat(), round(float(a), 6)] for d, a in dividends],
        "splits": [[d.isoformat(), r] for d, r in splits],
    }


def series_from_row(row: dict[str, Any]) -> Series | None:
    s = row.get("series") or {}
    if not s.get("d"):
        return None
    return Series(
        ticker=row["ticker"],
        dates=[_d(x) for x in s["d"]],
        close=[float(x) for x in s["c"]],
        adj=[float(x) for x in s.get("a") or s["c"]],
        volume=[float(x) for x in s.get("v") or [0] * len(s["d"])],
        dividends=[(_d(d), float(a)) for d, a in row.get("dividends") or []],
        splits=[(_d(d), str(r)) for d, r in row.get("splits") or []],
    )


def _ret(a: np.ndarray, n: int) -> float | None:
    if len(a) <= n or a[-1 - n] <= 0:
        return None
    return float((a[-1] / a[-1 - n] - 1) * 100)


def _daily_returns(a: np.ndarray) -> np.ndarray:
    return a[1:] / a[:-1] - 1


def _beta_corr(
    s: Series, bench: Series | None, n: int = 60
) -> tuple[float | None, float | None]:  # fmt: skip
    if bench is None:
        return None, None
    bm = dict(zip(bench.dates, bench.adj, strict=True))
    pairs = [(a, bm[d]) for d, a in zip(s.dates, s.adj, strict=True) if d in bm]
    if len(pairs) < 30:
        return None, None
    x = np.array([p[0] for p in pairs])
    y = np.array([p[1] for p in pairs])
    rx, ry = _daily_returns(x)[-n:], _daily_returns(y)[-n:]
    if len(rx) < 25 or float(np.var(ry)) == 0 or float(np.var(rx)) == 0:
        return None, None
    cov = float(np.cov(rx, ry)[0, 1])
    return cov / float(np.var(ry, ddof=1)), float(np.corrcoef(rx, ry)[0, 1])


def pair_correlation(a: Series, b: Series, n: int = 60) -> float | None:
    bm = dict(zip(b.dates, b.adj, strict=True))
    pairs = [(x, bm[d]) for d, x in zip(a.dates, a.adj, strict=True) if d in bm]
    if len(pairs) < 30:
        return None
    xa = np.array([p[0] for p in pairs])
    xb = np.array([p[1] for p in pairs])
    ra, rb = _daily_returns(xa)[-n:], _daily_returns(xb)[-n:]
    if len(ra) < 25 or float(np.std(ra)) == 0 or float(np.std(rb)) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def estimate_next_ex_dividend(divs: list[tuple[date, float]]) -> date | None:
    """ESTIMATE: last ex-date plus the typical gap between payments. Yahoo's free chart feed carries
    past dividends only, so the next date is projected, never known."""
    if len(divs) < 2:
        return None
    dates = sorted(d for d, _ in divs)
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:], strict=False))
    gap = gaps[len(gaps) // 2]
    if gap < 20 or gap > 400:
        return None
    nxt = dates[-1] + timedelta(days=gap)
    while nxt < dates[-1] + timedelta(days=1):
        nxt += timedelta(days=gap)
    return nxt


def compute_metrics(
    s: Series, market: Series | None, sector_etf: Series | None, today: date
) -> dict[str, Any]:  # fmt: skip
    s = s.as_of(today)  # never use bars dated after the calculation date
    market = market.as_of(today) if market else None
    sector_etf = sector_etf.as_of(today) if sector_etf else None
    a = np.array(s.adj, dtype=float)
    c = np.array(s.close, dtype=float)
    n = len(a)
    out: dict[str, Any] = {
        "ticker": s.ticker,
        "n_bars": n,
        "last_date": s.dates[-1].isoformat(),
        "stale_days": (today - s.dates[-1]).days,
        "last_close": float(c[-1]),
    }
    for k, d in (("ret_5d", 5), ("ret_20d", 20), ("ret_60d", 60), ("ret_120d", 120)):
        out[k] = _ret(a, d)
    for k, w in (("ma50_dist", 50), ("ma200_dist", 200)):
        out[k] = float((a[-1] / a[-w:].mean() - 1) * 100) if n >= w else None
    look = a[-252:]
    lo, hi = float(look.min()), float(look.max())
    out["pos_52w"] = float((a[-1] - lo) / (hi - lo)) if hi > lo and n >= 60 else None
    r = _daily_returns(a)
    r60 = r[-60:]
    out["vol_60d"] = float(np.std(r60, ddof=1) * math.sqrt(252) * 100) if len(r60) >= 30 else None
    out["downside_dev_60d"] = (
        float(math.sqrt(float(np.mean(np.minimum(r60, 0.0) ** 2))) * 100)
        if len(r60) >= 30
        else None
    )
    if len(a) >= 61:
        w = a[-61:]
        peak = np.maximum.accumulate(w)
        out["max_dd_60d"] = float(((w / peak) - 1).min() * 100)
    else:
        out["max_dd_60d"] = None
    dd = out["downside_dev_60d"]
    out["ret_per_downside_60d"] = (
        out["ret_60d"] / max(dd, 0.25) if out["ret_60d"] is not None and dd is not None else None
    )
    out["max_abs_move_20d"] = float(np.abs(r[-20:]).max() * 100) if len(r) >= 20 else None
    out["beta_60d"], out["corr_market_60d"] = _beta_corr(s, market)
    for k, d in (("rel_20d_vs_sector", 20), ("rel_60d_vs_sector", 60)):
        e = np.array(sector_etf.adj, dtype=float) if sector_etf else None
        er = _ret(e, d) if e is not None else None
        out[k] = (
            (out[f"ret_{d}d"] - er) if er is not None and out[f"ret_{d}d"] is not None else None
        )
    # a zero-volume day is $0 traded and stays in the mean (dropping it would overstate liquidity)
    adv_dollar = [p * v for p, v in zip(s.close[-20:], s.volume[-20:], strict=True) if v >= 0]
    out["adv_usd_20d_hist"] = float(np.mean(adv_dollar)) if adv_dollar else None
    year_ago = s.dates[-1] - timedelta(days=365)
    ttm = sum(amt for d, amt in s.dividends if d > year_ago)
    out["div_ttm"] = ttm
    out["div_yield_pct"] = float(ttm / c[-1] * 100) if ttm and c[-1] else 0.0
    out["last_ex_dividend"] = s.dividends[-1][0].isoformat() if s.dividends else None
    nxt = estimate_next_ex_dividend(s.dividends)
    out["next_ex_dividend_est"] = nxt.isoformat() if nxt else None
    out["days_since_split"] = (today - s.splits[-1][0]).days if s.splits else None
    return out


def _downside_dev(r: np.ndarray) -> float:
    neg = np.minimum(r, 0.0)
    return float(np.sqrt(np.mean(neg**2))) if len(r) else 0.0


def spread_stats(long_s: Series, short_s: Series, n_days: int = 120) -> dict[str, Any] | None:
    """Behaviour of a long/short pair's daily spread (long return minus short return, percent).

    Dollar-neutral and rebalanced daily, which is the simplest honest picture of the pair. Weekly
    figures use the last 26 non-overlapping 5-day blocks and apply the WSR score form
    (mean minus half the downside deviation). Descriptive of the past only; not a forecast.
    """
    bm = dict(zip(short_s.dates, short_s.adj, strict=True))
    pairs = [(d, a, bm[d]) for d, a in zip(long_s.dates, long_s.adj, strict=True) if d in bm]
    if len(pairs) < 70:
        return None
    la = np.array([p[1] for p in pairs])
    sa = np.array([p[2] for p in pairs])
    spread = (_daily_returns(la) - _daily_returns(sa)) * 100
    out: dict[str, Any] = {"n_days": int(len(spread))}
    for label, n in (("60d", 60), ("120d", n_days)):
        x = spread[-n:]
        if len(x) < n // 2:
            continue
        cum = np.cumsum(x)
        peak = np.maximum.accumulate(cum)
        out[f"mean_daily_{label}"] = round(float(np.mean(x)), 3)
        out[f"vol_daily_{label}"] = round(float(np.std(x, ddof=1)), 3)
        out[f"downside_dev_{label}"] = round(_downside_dev(x), 3)
        out[f"cum_{label}"] = round(float(cum[-1]), 2)
        out[f"max_dd_{label}"] = round(float(np.min(cum - peak)), 2)
    blocks = len(spread) // 5
    if blocks >= 8:
        wk = spread[len(spread) - blocks * 5 :].reshape(blocks, 5).sum(axis=1)[-26:]
        out["weeks"] = int(len(wk))
        out["wk_mean"] = round(float(np.mean(wk)), 2)
        out["wk_downside_dev"] = round(_downside_dev(wk), 2)
        out["wk_score"] = round(float(np.mean(wk)) - 0.5 * _downside_dev(wk), 2)
        out["wk_hit_rate"] = round(float(np.mean(wk > 0)), 2)
        out["wk_worst"] = round(float(np.min(wk)), 2)
        out["last_week"] = round(float(wk[-1]), 2)
    return out
