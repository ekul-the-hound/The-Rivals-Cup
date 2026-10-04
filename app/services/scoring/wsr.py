"""WSR-aware ESTIMATED score for one weekly round. Pure arithmetic over MANUALLY RECORDED positions.

This is NOT an order simulator: it never models orders, fills, stops, slippage or WSR behaviour. It
revalues positions you recorded (after entering them yourself in Trader View) at daily closes.
WSR / Trader View data and scoring are authoritative; every figure here is an ESTIMATE.

    V(d) = cash + market value of longs - market value of short liabilities
    r(d) = (V(d) - V(d-1)) / V(d-1)
    R    = (V(N) - 1,000,000) / 1,000,000 * 100                  (floored at -100)
    DD   = sqrt(sum(min(r(d), 0)^2) / N) * 100                   (N = ALL scheduled days; not annualized)
    PlayerScore = R - 0.5 * DD
"""

import math
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.db.store import Store
from app.services.features.metrics import adv_dollar, estimate_liquidity_cap

START_VALUE = 1_000_000.0
DD_WEIGHT = 0.5
GROSS_LIMIT_PCT = 200.0
GROSS_NEAR_PCT = 180.0
LIQ_CAP_PCT = 0.01
NON_POSITIVE_BOOK_GROSS_PCT = 1_000_000.0  # finite stand-in so JSON never carries Infinity
MAX_LEVERAGE = 2.0
LABEL = "ESTIMATED"
AUTHORITY = "WSR data authoritative. This is an estimate, not an official WSR score."
METHODOLOGY = (
    "ESTIMATED. V(d)=cash+long MV-short MV at unadjusted daily closes; weekly round starts at "
    "$1,000,000 with positions/cash reset; N counts all scheduled trading days; DD not annualized; "
    "PlayerScore=R-0.5*DD. WSR data authoritative."
)

# ASSUMPTION: 2026 NYSE full-day holidays (verify against the NYSE/WSR calendar; override via arg).
NYSE_HOLIDAYS_2026: frozenset[date] = frozenset(
    date(2026, m, d)
    for m, d in [
        (1, 1),
        (1, 19),
        (2, 16),
        (4, 3),
        (5, 25),
        (6, 19),
        (7, 3),
        (9, 7),
        (11, 26),
        (12, 25),
    ]
)


def scheduled_days(week_start: date, holidays: frozenset[date] | None = None) -> list[date]:
    hol = NYSE_HOLIDAYS_2026 if holidays is None else holidays
    return [
        d
        for d in (week_start + timedelta(days=i) for i in range(5))
        if d.weekday() < 5 and d not in hol
    ]


def score_from_values(values: list[float], n_days: int) -> dict[str, float]:
    """values = [V(0), V(1), ..., V(k)] with k <= n_days. Missing future days count as r=0 in N."""
    rets = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]
    down = sum(min(r, 0.0) ** 2 for r in rets)
    dd = math.sqrt(down / n_days) * 100 if n_days > 0 else 0.0
    r_pct = (values[-1] - START_VALUE) / START_VALUE * 100
    r_pct = max(r_pct, -100.0)
    return {"R": r_pct, "DD": dd, "player_score": r_pct - DD_WEIGHT * dd}


class Warning_(BaseModel):
    code: str
    message: str


class LegPos(BaseModel):
    ticker: str
    side: str
    quantity: float
    entry_price: float
    entered_at: datetime
    exit_price: float | None = None
    exited_at: datetime | None = None
    position_id: str | None = None
    pair_record_id: str | None = None


class DayRow(BaseModel):
    date: date
    value: float
    daily_return: float
    cash: float
    long_mv: float
    short_mv: float
    gross_pct_of_value: float


class ScoreEstimate(BaseModel):
    label: str = LABEL
    authority: str = AUTHORITY
    methodology: str = METHODOLOGY
    week_start: date
    as_of: date
    scheduled_days: int
    days_valued: int
    final_value: float
    total_return_pct: float
    downside_deviation_pct: float
    player_score: float
    daily: list[DayRow]
    modeled_gross_exposure_pct: float  # latest, mark-to-market in the round (modeled)
    modeled_gross_exposure_peak_pct: float
    manual_recorded_gross_usd: float  # cost basis of recorded open legs (manually recorded)
    manual_recorded_gross_pct_of_start: float
    liquidity: list[dict[str, Any]]
    warnings: list[Warning_]
    positions_used: int


def _bar_maps(bars: list[dict[str, Any]]) -> tuple[dict[date, float], dict[date, float]]:
    close: dict[date, float] = {}
    ratio: dict[date, float] = {}
    for b in bars:
        d = date.fromisoformat(str(b["bar_date"])[:10])
        if b.get("close"):
            close[d] = float(b["close"])
            if b.get("adj_close"):
                ratio[d] = float(b["adj_close"]) / float(b["close"])
    return close, ratio


def _flag_str(w: list[Warning_], code: str, msg: str) -> None:
    if not any(x.code == code and x.message == msg for x in w):
        w.append(Warning_(code=code, message=msg))


def estimate_round(
    positions: list[LegPos],
    bars_by_ticker: dict[str, list[dict[str, Any]]],
    week_start: date,
    as_of: date,
    *,
    dividends: dict[str, list[tuple[date, float]]] | None = None,
    leverage: dict[str, float] | None = None,
    holidays: frozenset[date] | None = None,
    excluded_prior_positions: int = 0,
) -> ScoreEstimate:
    days = scheduled_days(week_start, holidays)
    n = len(days)
    warnings: list[Warning_] = []
    maps = {t: _bar_maps(b) for t, b in bars_by_ticker.items()}
    held = sorted({p.ticker for p in positions})
    if excluded_prior_positions:
        _flag_str(
            warnings,
            "PRIOR_WEEK_POSITIONS_EXCLUDED",
            f"{excluded_prior_positions} open position(s) recorded in an earlier round are excluded: "
            "each weekly round resets positions and cash to $1,000,000.",
        )
    if not positions:
        _flag_str(warnings, "NO_POSITIONS", "No manually recorded positions in this round.")

    # --- dividend / distribution data quality ---
    divs = dividends or {}
    for t in held:
        if t not in divs:
            _flag_str(
                warnings,
                "DIVIDENDS_NOT_CHECKED",
                f"{t}: no dividend/distribution data loaded; dividend adjustments NOT applied. "
                "Verify in Trader View.",
            )
        ratios = maps.get(t, ({}, {}))[1]
        ds = sorted(ratios)
        for a, b in zip(ds, ds[1:], strict=False):
            wk_end = week_start + timedelta(days=4)
            has_event = any(week_start <= x <= wk_end for x, _ in divs.get(t, []))
            if (
                week_start <= b <= wk_end
                and abs(ratios[b] / ratios[a] - 1) > 0.0005
                and not has_event
            ):
                _flag_str(
                    warnings,
                    "DIVIDEND_SUSPECTED",
                    f"{t}: adjusted/unadjusted close ratio changed on {b}; a dividend or "
                    "corporate action may not be reflected in this estimate.",
                )

    # --- ETF leverage ---
    for t in held:
        lev = (leverage or {}).get(t)
        if lev is not None and abs(lev) > MAX_LEVERAGE:
            _flag_str(warnings, "ETF_LEVERAGE_OVER_2X", f"{t}: leverage {lev:g}x exceeds 2x.")
        if lev is None:
            _flag_str(warnings, "LEVERAGE_UNKNOWN", f"{t}: leverage factor unknown.")

    values = [START_VALUE]
    rows: list[DayRow] = []
    peak_gross = 0.0
    last_gross = 0.0
    last_close: dict[str, float] = {}
    stale_flagged: set[tuple[str, date]] = set()

    for d in days:
        if d > as_of:
            break
        cash = START_VALUE
        long_mv = short_mv = 0.0
        any_mark = False
        for p in positions:
            e = p.entered_at.date()
            if e > d:
                continue
            sign = 1 if p.side == "LONG" else -1
            cash -= sign * p.quantity * p.entry_price
            exited = p.exited_at is not None and p.exited_at.date() <= d
            if exited and p.exit_price is not None and p.exited_at is not None:
                cash += sign * p.quantity * p.exit_price
            for ex, amt in divs.get(p.ticker, []):
                if e < ex <= d and not (p.exited_at and p.exited_at.date() < ex):
                    cash += sign * p.quantity * amt  # shorts pay the dividend
            if exited:
                continue
            closes = maps.get(p.ticker, ({}, {}))[0]
            px = closes.get(d)
            if px is None:
                prior = [x for x in closes if x <= d]
                if prior:
                    px = closes[max(prior)]
                    if (p.ticker, d) not in stale_flagged:
                        stale_flagged.add((p.ticker, d))
                        _flag_str(
                            warnings,
                            "STALE_MARK",
                            f"{p.ticker}: no close for {d}; last available close carried forward.",
                        )
                else:
                    px = p.entry_price
                    _flag_str(
                        warnings,
                        "NO_MARK_DATA",
                        f"{p.ticker}: no price data; valued at your recorded entry price.",
                    )
            else:
                any_mark = True
            last_close[p.ticker] = px
            if sign == 1:
                long_mv += p.quantity * px
            else:
                short_mv += p.quantity * px
        _ = any_mark
        v = cash + long_mv - short_mv
        if v > 0:
            gross_pct = (long_mv + short_mv) / v * 100
        else:
            gross_pct = NON_POSITIVE_BOOK_GROSS_PCT
            _flag_str(
                warnings,
                "BOOK_VALUE_NON_POSITIVE",
                f"Estimated portfolio value is not positive on {d}; gross exposure is not meaningful.",
            )
        rows.append(
            DayRow(
                date=d,
                value=round(v, 2),
                daily_return=(v - values[-1]) / values[-1],
                cash=round(cash, 2),
                long_mv=round(long_mv, 2),
                short_mv=round(short_mv, 2),
                gross_pct_of_value=round(gross_pct, 2),
            )
        )
        values.append(v)
        peak_gross = max(peak_gross, gross_pct)
        last_gross = gross_pct

    sc = score_from_values(values, n)
    if peak_gross >= GROSS_LIMIT_PCT:
        _flag_str(
            warnings,
            "GROSS_EXPOSURE_AT_LIMIT",
            f"Modeled gross exposure reached {peak_gross:.0f}% of portfolio value (limit 200%).",
        )
    elif peak_gross >= GROSS_NEAR_PCT:
        _flag_str(
            warnings,
            "GROSS_EXPOSURE_NEAR_LIMIT",
            f"Modeled gross exposure reached {peak_gross:.0f}% of portfolio value (limit 200%).",
        )
    if sc["R"] <= -100.0:
        _flag_str(warnings, "RETURN_FLOOR", "Total return floored at -100 points.")

    open_pos = [p for p in positions if p.exited_at is None]
    manual_gross = sum(p.quantity * p.entry_price for p in open_pos)
    if manual_gross / START_VALUE * 100 >= GROSS_NEAR_PCT:
        _flag_str(
            warnings,
            "MANUAL_GROSS_NEAR_LIMIT",
            f"Manually recorded gross (cost basis) is {manual_gross / START_VALUE * 100:.0f}% of "
            "$1,000,000 (limit 200%).",
        )

    liquidity: list[dict[str, Any]] = []
    for p in open_pos:
        adv = adv_dollar(bars_by_ticker.get(p.ticker, []), 20)
        cap = estimate_liquidity_cap(adv, LIQ_CAP_PCT)
        notional = p.quantity * p.entry_price
        status = "UNKNOWN" if cap is None else ("OVER_CAP" if notional > cap else "OK")
        liquidity.append(
            {
                "ticker": p.ticker,
                "position_notional_usd": round(notional, 2),
                "estimated_daily_cap_usd": cap,
                "status": status,
                "basis": "ESTIMATED 1% of trailing 20-day dollar volume; WSR data authoritative",
            }
        )
        if status == "OVER_CAP":
            _flag_str(
                warnings,
                "LIQUIDITY_CAP_EXCEEDED",
                f"{p.ticker}: ${notional:,.0f} exceeds estimated 1% ADV cap ${cap:,.0f} "
                "(WSR data authoritative).",
            )
        if status == "UNKNOWN":
            _flag_str(
                warnings, "LIQUIDITY_UNKNOWN", f"{p.ticker}: not enough volume history for a cap."
            )

    return ScoreEstimate(
        week_start=week_start,
        as_of=as_of,
        scheduled_days=n,
        days_valued=len(rows),
        final_value=round(values[-1], 2),
        total_return_pct=round(sc["R"], 6),
        downside_deviation_pct=round(sc["DD"], 6),
        player_score=round(sc["player_score"], 6),
        daily=rows,
        modeled_gross_exposure_pct=round(last_gross, 2),
        modeled_gross_exposure_peak_pct=round(peak_gross, 2),
        manual_recorded_gross_usd=round(manual_gross, 2),
        manual_recorded_gross_pct_of_start=round(manual_gross / START_VALUE * 100, 2),
        liquidity=liquidity,
        warnings=warnings,
        positions_used=len(positions),
    )


def _d(v: Any) -> date:
    return date.fromisoformat(str(v)[:10])


def _dt(v: Any) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def estimate_portfolio(
    store: Store,
    portfolio_id: str,
    week_start: date,
    as_of: date,
    holidays: frozenset[date] | None = None,
) -> ScoreEstimate:
    """READ-ONLY: load recorded legs for the round and bars, then estimate."""
    secs = {s["id"]: s for s in store.select("securities")}
    rows = store.select("manual_positions", eq={"portfolio_id": portfolio_id})
    in_round: list[LegPos] = []
    prior = 0
    end = week_start + timedelta(days=4)
    for r in rows:
        if not r.get("opened_at") or not r.get("avg_entry_price"):
            continue
        opened = _dt(r["opened_at"])
        if not (week_start <= opened.date() <= end):
            if r.get("is_open", True) and opened.date() < week_start:
                prior += 1
            continue
        s = secs.get(r["security_id"])
        if s is None:
            continue
        in_round.append(
            LegPos(
                ticker=s["ticker"],
                side=str(r["side"]),
                quantity=float(r["quantity"]),
                entry_price=float(r["avg_entry_price"]),
                entered_at=opened,
                exit_price=float(r["exit_price"]) if r.get("exit_price") else None,
                exited_at=_dt(r["closed_at"]) if r.get("closed_at") else None,
                position_id=str(r.get("id")),
                pair_record_id=r.get("manual_pair_record_id"),
            )
        )
    tickers = sorted({p.ticker for p in in_round})
    by_ticker = {s["ticker"]: s for s in secs.values()}
    bars: dict[str, list[dict[str, Any]]] = {}
    divs: dict[str, list[tuple[date, float]]] = {}
    all_div = store.select("dividend_events")
    div_secs = {d["security_id"] for d in all_div}
    lev: dict[str, float] = {}
    for t in tickers:
        s = by_ticker[t]
        b = store.select(
            "market_bars",
            eq={"security_id": s["id"], "timeframe": "1D"},
            order="bar_date",
            desc=True,
            limit=60,
        )
        bars[t] = sorted(b, key=lambda x: str(x["bar_date"]))
        if s["id"] in div_secs:
            divs[t] = [
                (_d(d["ex_date"]), float(d["amount_per_share"]))
                for d in all_div
                if d["security_id"] == s["id"]
            ]
        if s.get("leverage_factor") is not None:
            lev[t] = float(s["leverage_factor"])
    return estimate_round(
        in_round,
        bars,
        week_start,
        as_of,
        dividends=divs,
        leverage=lev,
        holidays=holidays,
        excluded_prior_positions=prior,
    )


def save_score_snapshot(db: Any, portfolio_id: str, est: ScoreEstimate) -> str:
    """Persist an ESTIMATE (is_estimate forced true by the DB). Writes score_snapshots only."""
    sid = str(uuid.uuid4())
    db.upsert(
        "score_snapshots",
        [
            {
                "id": sid,
                "portfolio_id": portfolio_id,
                "snapshot_at": datetime.now().astimezone().isoformat(),
                "estimated_score": est.player_score,
                "components": {
                    "label": LABEL,
                    "R": est.total_return_pct,
                    "DD": est.downside_deviation_pct,
                    "drawdown": est.downside_deviation_pct,
                    "week_start": est.week_start.isoformat(),
                    "as_of": est.as_of.isoformat(),
                    "modeled_gross_exposure_pct": est.modeled_gross_exposure_pct,
                },
                "warnings": [w.model_dump() for w in est.warnings],
                "methodology": METHODOLOGY,
                "is_estimate": True,
                "data_status": "UNVERIFIED",
            }
        ],
        "id",
    )
    return sid
