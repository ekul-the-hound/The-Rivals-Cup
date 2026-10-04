"""Strength / weakness scoring (0-100) from price-history metrics, ranked within one sector.

Strength = momentum (20d, 60d, 120d), trend (distance above the 50-day average, position in the
52-week range) and the quality of the path (60-day return per unit of downside deviation, the risk
the weekly score penalizes). Each input is a percentile rank among the sector's tradable names, so
the score always says "how strong versus its sector peers", not an absolute level.

Long score = strength minus penalties for parabolic or extremely volatile names.
Short score = weakness (100 - strength) minus penalties for squeeze risk, violent volatility, bounces,
and an estimated ex-dividend date inside the scoring week (a short pays the dividend).
Small, symmetric context adjustments (analyst consensus, revenue growth) apply only where that data
exists, and never more than a few points.
All of it is a research ranking, not a recommendation and not an instruction.
"""

from __future__ import annotations

from datetime import date
from typing import Any

STRENGTH_WEIGHTS: dict[str, float] = {
    "ret_20d": 0.30,
    "ret_60d": 0.30,
    "ret_120d": 0.15,
    "ma50_dist": 0.10,
    "pos_52w": 0.05,
    "ret_per_downside_60d": 0.10,
}


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float | None]:
    """0-100 percentile of each value among the non-missing ones (average rank for ties)."""
    present = sorted((v, k) for k, v in values.items() if v is not None)
    n = len(present)
    out: dict[str, float | None] = {k: None for k in values}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        pct = ((i + j) / 2 + 0.5) / n * 100
        for t in range(i, j + 1):
            out[present[t][1]] = round(pct, 2)
        i = j + 1
    return out


def strength_scores(metrics: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """metrics: ticker -> compute_metrics output for one sector's tradable population."""
    ranks = {
        f: percentile_ranks({t: m.get(f) for t, m in metrics.items()}) for f in STRENGTH_WEIGHTS
    }
    out: dict[str, dict[str, Any]] = {}
    for t in metrics:
        num = den = 0.0
        comp: dict[str, float | None] = {}
        for f, w in STRENGTH_WEIGHTS.items():
            r = ranks[f][t]
            comp[f] = r
            if r is not None:
                num += w * r
                den += w
        out[t] = {
            "strength": round(num / den, 2) if den >= 0.5 else None,
            "components": comp,
            "coverage": round(den, 2),
        }
    return out


def _pen(items: list[tuple[str, float]]) -> tuple[float, list[str]]:
    return sum(p for _, p in items), [f"{k} ({-p:+.0f})" for k, p in items if p]


def long_penalties(m: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    items: list[tuple[str, float]] = []
    flags: list[str] = []
    if (m.get("ret_20d") or 0) > 50:
        items.append(("PARABOLIC_20D", 10.0))
        flags.append("PARABOLIC_20D")
    if (m.get("vol_60d") or 0) > 80:
        items.append(("EXTREME_VOLATILITY", 8.0))
        flags.append("EXTREME_VOLATILITY")
    if (m.get("max_abs_move_20d") or 0) > 25:
        items.append(("LARGE_ONE_DAY_MOVE", 5.0))
        flags.append("LARGE_ONE_DAY_MOVE")
    total, text = _pen(items)
    return total, text, flags


def short_penalties(
    m: dict[str, Any], ctx: dict[str, Any], week: tuple[date, date]
) -> tuple[float, list[str], list[str]]:  # fmt: skip
    items: list[tuple[str, float]] = []
    flags: list[str] = []
    dtc = ctx.get("days_to_cover")
    if dtc is not None:
        if dtc >= 8:
            items.append(("SQUEEZE_RISK_DTC>=8", 15.0))
            flags.append("SQUEEZE_RISK")
        elif dtc >= 5:
            items.append(("SQUEEZE_RISK_DTC>=5", 8.0))
            flags.append("SQUEEZE_RISK")
        elif dtc >= 3:
            items.append(("DTC>=3", 3.0))
    if (m.get("vol_60d") or 0) > 90:
        items.append(("EXTREME_VOLATILITY", 10.0))
        flags.append("EXTREME_VOLATILITY")
    if (m.get("ret_5d") or 0) > 15 or (m.get("ret_20d") or 0) > 25:
        items.append(("RECENT_BOUNCE", 10.0))
        flags.append("RECENT_BOUNCE")
    nxt = m.get("next_ex_dividend_est")
    if nxt and week[0] <= date.fromisoformat(nxt) <= week[1]:
        q_yield = (m.get("div_yield_pct") or 0) / 4
        items.append(("EX_DIV_LIKELY_IN_WEEK", min(12.0, 3.0 + 6.0 * q_yield)))
        flags.append("EX_DIV_LIKELY_IN_WEEK")
    total, text = _pen(items)
    return total, text, flags


def context_adjustment(ctx: dict[str, Any], side: str) -> float:
    """Small tie-breaking nudge from analyst consensus (-2..+2) and revenue growth, if present."""
    adj = 0.0
    a = ctx.get("analyst_score")
    if a is not None:
        adj += 2.0 * a
    g = ctx.get("revenue_growth_pct")
    if g is not None:
        adj += 0.15 * max(min(g, 20.0), -10.0)
    return adj if side == "long" else -adj
