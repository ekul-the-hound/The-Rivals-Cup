"""Pure price/liquidity metrics (pandas/numpy). Inputs are lists of bar dicts."""

from typing import Any

import numpy as np
import pandas as pd

WINDOWS = (1, 5, 20, 60)


def close_series(bars: list[dict[str, Any]]) -> pd.Series:
    """Adjusted close (falls back to close), indexed by bar_date, ascending."""
    rows = {
        str(b["bar_date"])[:10]: b.get("adj_close")
        if b.get("adj_close") is not None
        else b.get("close")
        for b in bars
    }
    s = pd.Series(rows, dtype="float64").dropna().sort_index()
    return s


def trailing_returns(
    close: pd.Series, windows: tuple[int, ...] = WINDOWS
) -> dict[int, float | None]:
    out: dict[int, float | None] = {}
    for n in windows:
        out[n] = float(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None
    return out


def relative(a: dict[int, float | None], b: dict[int, float | None]) -> dict[int, float | None]:
    """a minus b per window (e.g. long-leg return minus short-leg return)."""
    return {n: (a[n] - b[n]) if a.get(n) is not None and b.get(n) is not None else None for n in a}


def correlation(a: pd.Series, b: pd.Series, window: int = 60) -> tuple[float | None, int]:
    """Correlation of daily returns over the last `window` overlapping returns."""
    df = pd.concat([a.pct_change(), b.pct_change()], axis=1, join="inner").dropna().tail(window)
    if len(df) < 20:
        return None, len(df)
    c = df.iloc[:, 0].corr(df.iloc[:, 1])
    return (None if np.isnan(c) else float(c)), len(df)


def adv_dollar(bars: list[dict[str, Any]], window: int = 20) -> float | None:
    rows = sorted(bars, key=lambda b: str(b["bar_date"]))[-window:]
    vals = [b["close"] * b["volume"] for b in rows if b.get("close") and b.get("volume")]
    return float(np.mean(vals)) if len(vals) >= max(5, window // 2) else None


def adv_shares(bars: list[dict[str, Any]], window: int = 20) -> float | None:
    rows = sorted(bars, key=lambda b: str(b["bar_date"]))[-window:]
    vals = [b["volume"] for b in rows if b.get("volume")]
    return float(np.mean(vals)) if len(vals) >= max(5, window // 2) else None


def estimate_liquidity_cap(adv_usd: float | None, pct: float) -> float | None:
    """ESTIMATED max leg size = pct of 20d ADV. Not a WSR rule; WSR's real limits are unverified."""
    return None if adv_usd is None else round(adv_usd * pct, 2)


def daily_vol(close: pd.Series, window: int = 20) -> float | None:
    """Standard deviation of daily returns over the last `window` returns."""
    r = close.pct_change().dropna().tail(window)
    return float(r.std(ddof=1)) if len(r) >= 10 else None


def beta(a: pd.Series, market: pd.Series, window: int = 60, min_obs: int = 40) -> float | None:
    df = (
        pd.concat([a.pct_change(), market.pct_change()], axis=1, join="inner").dropna().tail(window)
    )
    if len(df) < min_obs:
        return None
    var = df.iloc[:, 1].var(ddof=1)
    return None if not var or np.isnan(var) else float(df.iloc[:, 0].cov(df.iloc[:, 1]) / var)


def spread_vol(a: pd.Series, b: pd.Series, window: int = 60) -> float | None:
    """Std of daily (a - b) return differences."""
    df = pd.concat([a.pct_change(), b.pct_change()], axis=1, join="inner").dropna().tail(window)
    if len(df) < 20:
        return None
    return float((df.iloc[:, 0] - df.iloc[:, 1]).std(ddof=1))


def max_abs_daily_move(close: pd.Series, window: int = 60) -> float | None:
    r = close.pct_change().dropna().tail(window)
    return float(r.abs().max()) if len(r) else None
