"""Daily-bar validation. Pure functions: no I/O, no provider calls.

Yahoo is unofficial, so every bar is checked before it can feed a ranking, a liquidity estimate or a
score. Invalid bars are DROPPED (never repaired or guessed); suspicious-but-possible bars (large
jumps, missing dates) are KEPT and reported so a person can look. Nothing here uses data dated after
`today` (no future leakage) and nothing here trades or recommends.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# A one-day move this large is flagged as SPLIT_LIKE_JUMP (a real split or a bad print: look at it).
SPLIT_LIKE_JUMP = 0.40
# This many consecutive missing WEEKDAYS between two bars is flagged (one holiday = 1 weekday).
MISSING_WEEKDAY_RUN = 3
# OHLC tolerance for rounding noise in vendor data.
OHLC_TOL = 1e-4


@dataclass(frozen=True)
class BarIssue:
    code: str  # INVALID_PRICE | INVALID_OHLC | INVALID_VOLUME | FUTURE_BAR | DUPLICATE_BAR |
    #            OUT_OF_ORDER | SPLIT_LIKE_JUMP | MISSING_DATES | MISSING_VOLUME
    detail: str
    bar_date: date | None = None
    dropped: bool = False


@dataclass
class BarCheck:
    clean: list[dict[str, Any]] = field(default_factory=list)
    issues: list[BarIssue] = field(default_factory=list)

    @property
    def dropped(self) -> int:
        return sum(1 for i in self.issues if i.dropped)

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def summary(self, ticker: str = "") -> str | None:
        if not self.issues:
            return None
        counts: dict[str, int] = {}
        for i in self.issues:
            counts[i.code] = counts.get(i.code, 0) + 1
        body = ", ".join(f"{c}x{n}" for c, n in sorted(counts.items()))
        return f"{ticker + ': ' if ticker else ''}bar issues ({body}); {self.dropped} dropped"


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _as_date(v: Any) -> date | None:
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _weekdays_between(a: date, b: date) -> int:
    """Weekdays strictly between a and b."""
    n, d = 0, a + timedelta(days=1)
    while d < b:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def validate_bars(bars: list[dict[str, Any]], today: date | None = None) -> BarCheck:
    """bars: dicts with bar_date, close and optionally open/high/low/adj_close/volume.

    Returns bars sorted ascending, de-duplicated (last one wins) and with invalid bars removed.
    """
    out = BarCheck()
    prev_in: date | None = None
    by_date: dict[date, dict[str, Any]] = {}
    for raw in bars:
        d = _as_date(raw.get("bar_date"))
        if d is None:
            out.issues.append(BarIssue("INVALID_PRICE", "unparseable bar_date", None, True))
            continue
        if prev_in is not None and d < prev_in:
            out.issues.append(BarIssue("OUT_OF_ORDER", f"{d} after {prev_in}", d))
        prev_in = d if prev_in is None or d > prev_in else prev_in
        if today is not None and d > today:
            out.issues.append(BarIssue("FUTURE_BAR", f"{d} is after {today}", d, True))
            continue
        close = _num(raw.get("close"))
        if close is None or close <= 0:
            out.issues.append(BarIssue("INVALID_PRICE", f"close={raw.get('close')!r}", d, True))
            continue
        adj = _num(raw.get("adj_close"))
        if raw.get("adj_close") is not None and (adj is None or adj <= 0):
            out.issues.append(BarIssue("INVALID_PRICE", f"adj_close={raw['adj_close']!r}", d, True))
            continue
        o, h, lo = (_num(raw.get(k)) for k in ("open", "high", "low"))
        given = [
            (k, v) for k, v in (("open", o), ("high", h), ("low", lo)) if raw.get(k) is not None
        ]
        if any(v is None or v <= 0 for _, v in given):
            out.issues.append(BarIssue("INVALID_OHLC", "non-positive or non-finite OHLC", d, True))
            continue
        if h is not None and lo is not None and h < lo * (1 - OHLC_TOL):
            out.issues.append(BarIssue("INVALID_OHLC", f"high {h} < low {lo}", d, True))
            continue
        top = h if h is not None else None
        bot = lo if lo is not None else None
        if (top is not None and close > top * (1 + OHLC_TOL)) or (
            bot is not None and close < bot * (1 - OHLC_TOL)
        ):
            out.issues.append(
                BarIssue("INVALID_OHLC", f"close {close} outside [{bot}, {top}]", d, True)
            )
            continue
        vol_raw = raw.get("volume")
        if vol_raw is None:
            out.issues.append(BarIssue("MISSING_VOLUME", "volume missing", d))
        else:
            vol = _num(vol_raw)
            if vol is None or vol < 0:
                out.issues.append(BarIssue("INVALID_VOLUME", f"volume={vol_raw!r}", d, True))
                continue
        if d in by_date:
            out.issues.append(BarIssue("DUPLICATE_BAR", f"{d} appears more than once", d))
        by_date[d] = raw
    ordered = [by_date[d] for d in sorted(by_date)]
    prev: dict[str, Any] | None = None
    for b in ordered:
        d = _as_date(b["bar_date"])
        assert d is not None
        if prev is not None:
            pd_ = _as_date(prev["bar_date"])
            assert pd_ is not None
            for key in ("close", "adj_close"):
                a, c = _num(prev.get(key)), _num(b.get(key))
                if a and c and abs(c / a - 1) >= SPLIT_LIKE_JUMP:
                    out.issues.append(
                        BarIssue("SPLIT_LIKE_JUMP", f"{key} {a:g} -> {c:g} on {d}", d)
                    )
                    break
            gap = _weekdays_between(pd_, d)
            if gap >= MISSING_WEEKDAY_RUN:
                out.issues.append(
                    BarIssue("MISSING_DATES", f"{gap} weekdays missing between {pd_} and {d}", d)
                )
        prev = b
    out.clean = ordered
    return out
