"""Hand-calculated checks of the ESTIMATED WSR score formula (audit regression tests).

    V(d) = cash + long market value - short liabilities
    r(d) = [V(d) - V(d-1)] / V(d-1)
    R    = [V(N) - 1,000,000] / 1,000,000 * 100
    DD   = sqrt( sum(min(r(d), 0)^2) / N ) * 100      (N = every scheduled trading day, not annualized)
    PlayerScore = R - 0.5 * DD

Every expected number below was worked out by hand in the comment above it.
"""

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.memory import InMemoryDB
from app.services.scoring.wsr import (
    START_VALUE,
    LegPos,
    estimate_portfolio,
    estimate_round,
    scheduled_days,
    score_from_values,
)

WEEK = date(2026, 9, 28)  # Monday, no holidays
DAYS = scheduled_days(WEEK)


def _dt(d: date, h: int = 14) -> datetime:
    return datetime(d.year, d.month, d.day, h, 0, tzinfo=UTC)


def _bars(closes: list[float], days: list[date] = DAYS, vol: int = 1_000_000) -> list[dict]:
    return [
        {"bar_date": d.isoformat(), "close": c, "adj_close": c, "volume": vol, "timeframe": "1D"}
        for d, c in zip(days, closes, strict=True)
    ]


def _long(q=1000, px=100.0, t="AAA", d=WEEK) -> LegPos:
    return LegPos(ticker=t, side="LONG", quantity=q, entry_price=px, entered_at=_dt(d))


def _short(q=1000, px=100.0, t="BBB", d=WEEK) -> LegPos:
    return LegPos(ticker=t, side="SHORT", quantity=q, entry_price=px, entered_at=_dt(d))


# ----------------------------------------------------------- score_from_values
def test_all_positive_days_have_zero_downside_and_score_equals_return():
    # +1%, +1%, +1%, +1%, +1% compounding: V5 = 1e6 * 1.01^5
    v = [START_VALUE * 1.01**i for i in range(6)]
    s = score_from_values(v, 5)
    assert s["DD"] == 0.0
    assert s["R"] == pytest.approx((1.01**5 - 1) * 100, abs=1e-9)  # 5.101005
    assert s["player_score"] == pytest.approx(s["R"], abs=1e-12)


def test_one_negative_day():
    # V: 1,000,000 -> 1,010,000 (+1%) -> 989,800 (-2%) -> 989,800 -> 989,800 -> 989,800
    # r2 = (989,800 - 1,010,000) / 1,010,000 = -0.02 ; only that day enters the numerator
    # DD = sqrt(0.02^2 / 5) * 100 = 0.02/sqrt(5)*100 = 0.894427191
    v = [1_000_000, 1_010_000, 989_800, 989_800, 989_800, 989_800]
    s = score_from_values(v, 5)
    assert s["DD"] == pytest.approx(0.02 / math.sqrt(5) * 100, abs=1e-9)
    assert s["R"] == pytest.approx(-1.02, abs=1e-9)
    assert s["player_score"] == pytest.approx(-1.02 - 0.5 * 0.894427191, abs=1e-8)  # -1.467213596


def test_multiple_negative_days_sum_squares_not_returns():
    # r = -1%, -2%, -3% on three days, +0.5% and 0 on the others.
    # numerator = 0.0001 + 0.0004 + 0.0009 = 0.0014 ; /5 = 0.00028 ; sqrt = 0.016733200531
    v = [1_000_000.0]
    for r in (-0.01, -0.02, -0.03, 0.005, 0.0):
        v.append(v[-1] * (1 + r))
    s = score_from_values(v, 5)
    assert s["DD"] == pytest.approx(math.sqrt(0.0014 / 5) * 100, abs=1e-9)  # 1.6733200531
    assert s["DD"] == pytest.approx(1.6733200531, abs=1e-9)


def test_positive_days_never_enter_the_downside_numerator():
    # a huge +10% day must not change DD relative to the same path without it
    base = score_from_values([1e6, 990_000, 990_000, 990_000, 990_000, 990_000], 5)
    with_gain = score_from_values([1e6, 990_000, 1_089_000, 1_089_000, 1_089_000, 1_089_000], 5)
    assert with_gain["DD"] == pytest.approx(base["DD"], abs=1e-12)


def test_flat_days_are_zero_return_and_zero_downside():
    s = score_from_values([START_VALUE] * 6, 5)
    assert s == {"R": 0.0, "DD": 0.0, "player_score": 0.0}


def test_downside_deviation_is_not_annualized_and_uses_n_not_observed_days():
    # one -1% day with only 1 day observed but N=5: DD = sqrt(0.0001/5)*100 (NOT /1, NOT * sqrt(252))
    s = score_from_values([1_000_000, 990_000], 5)
    assert s["DD"] == pytest.approx(math.sqrt(0.0001 / 5) * 100, abs=1e-12)  # 0.4472135955
    assert s["DD"] < 1.0  # an annualized figure would be > 7


def test_return_floor_at_minus_100():
    s = score_from_values([1e6, 500_000, -2_000_000], 5)
    assert s["R"] == -100.0
    assert s["player_score"] == pytest.approx(-100.0 - 0.5 * s["DD"], abs=1e-9)


def test_four_day_holiday_week_uses_n_equals_four():
    labor_day_week = date(2026, 9, 7)
    assert len(scheduled_days(labor_day_week)) == 4
    s = score_from_values([1e6, 990_000], len(scheduled_days(labor_day_week)))
    assert s["DD"] == pytest.approx(math.sqrt(0.0001 / 4) * 100, abs=1e-12)  # 0.5


# ----------------------------------------------------------- estimate_round valuation
def test_long_short_valuation_hand_calculation():
    # long 1,000 @100 and short 1,000 @100 (each $100,000). Closes:
    #   AAA (long):  102, 101, 101, 101, 101        BBB (short): 101, 103, 103, 103, 103
    # day1: cash = 1e6 - 100,000 + 100,000 = 1,000,000 ; V = 1e6 + 102,000 - 101,000 = 1,001,000
    # day2: V = 1e6 + 101,000 - 103,000 = 998,000   -> r2 = (998,000-1,001,000)/1,001,000
    est = estimate_round(
        [_long(), _short()],
        {"AAA": _bars([102, 101, 101, 101, 101]), "BBB": _bars([101, 103, 103, 103, 103])},
        WEEK,
        DAYS[-1],
        dividends={"AAA": [], "BBB": []},
        leverage={"AAA": 1, "BBB": 1},
    )
    assert [d.value for d in est.daily] == [1_001_000.0, 998_000.0, 998_000.0, 998_000.0, 998_000.0]
    r2 = (998_000 - 1_001_000) / 1_001_000
    assert est.daily[1].daily_return == pytest.approx(r2)
    assert est.downside_deviation_pct == pytest.approx(math.sqrt(r2**2 / 5) * 100, abs=1e-6)
    assert est.total_return_pct == pytest.approx(-0.2)
    assert est.player_score == pytest.approx(-0.2 - 0.5 * est.downside_deviation_pct, abs=1e-5)
    assert est.label == "ESTIMATED"
    assert "authoritative" in est.authority.lower() and "estimate" in est.authority.lower()


def test_gross_is_long_plus_short_and_net_is_separate():
    est = estimate_round(
        [_long(q=1500), _short(q=500)],
        {"AAA": _bars([100.0] * 5), "BBB": _bars([100.0] * 5)},
        WEEK,
        DAYS[-1],
        dividends={"AAA": [], "BBB": []},
        leverage={"AAA": 1, "BBB": 1},
    )
    d = est.daily[-1]
    assert d.long_mv == 150_000.0 and d.short_mv == 50_000.0
    # gross = |long| + |short| = 200,000 on V = 1,000,000 -> 20% ; net would be 100,000 (10%)
    assert d.gross_pct_of_value == pytest.approx(20.0)
    assert est.manual_recorded_gross_usd == 200_000.0


def test_gross_limit_warning_at_200_percent():
    est = estimate_round(
        [_long(q=10_000, px=100.0), _short(q=10_000, px=100.0)],  # $1,000,000 each = 200% gross
        {"AAA": _bars([100.0] * 5), "BBB": _bars([100.0] * 5)},
        WEEK,
        DAYS[-1],
        dividends={"AAA": [], "BBB": []},
        leverage={"AAA": 1, "BBB": 1},
    )
    assert est.modeled_gross_exposure_peak_pct == pytest.approx(200.0)
    assert "GROSS_EXPOSURE_AT_LIMIT" in {w.code for w in est.warnings}


def test_return_floor_warning_when_book_is_wiped_out():
    est = estimate_round(
        [_short(q=20_000, px=100.0)],  # $2M short; price doubles -> V = 1e6 - 2M = -1M
        {"BBB": _bars([200.0] * 5)},
        WEEK,
        DAYS[-1],
        dividends={"BBB": []},
        leverage={"BBB": 1},
    )
    assert est.total_return_pct == -100.0
    assert "RETURN_FLOOR" in {w.code for w in est.warnings}


# ----------------------------------------------------------- weekly reset / manual vs model
def test_weekly_round_resets_to_one_million_and_excludes_prior_week_positions():
    prior_week = WEEK - timedelta(days=7)
    db = InMemoryDB(
        {
            "securities": [{"id": "s1", "ticker": "AAA", "leverage_factor": 1}],
            "manual_positions": [
                {  # opened LAST week and still open: must not carry into this round
                    "id": "p-old",
                    "portfolio_id": "pf",
                    "security_id": "s1",
                    "side": "LONG",
                    "quantity": 1000,
                    "avg_entry_price": 100.0,
                    "opened_at": _dt(prior_week).isoformat(),
                    "is_open": True,
                },
            ],
            "market_bars": [
                {"security_id": "s1", "timeframe": "1D", **b} for b in _bars([150.0] * 5)
            ],
        }
    )
    est = estimate_portfolio(db, "pf", WEEK, DAYS[-1])
    assert est.final_value == START_VALUE  # the +50% on last week's position is NOT counted
    assert est.positions_used == 0
    codes = {w.code for w in est.warnings}
    assert "PRIOR_WEEK_POSITIONS_EXCLUDED" in codes and "NO_POSITIONS" in codes
    assert est.label == "ESTIMATED"


def test_wiped_out_book_serializes_to_strict_json():
    # regression: V <= 0 used to produce gross_pct == inf, which strict JSON (API/MCP) cannot carry
    import json

    from fastapi.encoders import jsonable_encoder

    est = estimate_round(
        [_short(q=20_000, px=100.0)],
        {"BBB": _bars([200.0] * 5)},
        WEEK,
        DAYS[-1],
        dividends={"BBB": []},
        leverage={"BBB": 1},
    )
    json.dumps(jsonable_encoder(est), allow_nan=False)  # must not raise
    assert all(math.isfinite(d.gross_pct_of_value) for d in est.daily)
    assert "BOOK_VALUE_NON_POSITIVE" in {w.code for w in est.warnings}
