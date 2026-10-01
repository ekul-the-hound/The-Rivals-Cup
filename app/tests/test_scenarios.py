"""End-to-end mock scenarios A-H, plus journal and estimator unit tests. All offline/synthetic."""

import copy
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.config.clock import utcnow
from app.services.journal.guard import JournalWriteGuard, JournalWriteViolation
from app.services.journal.records import (
    JournalError,
    LegEntry,
    ensure_portfolio,
    record_manual_entry,
    record_manual_exit,
    record_manual_pair,
)
from app.services.pairs.engine import PeerPairEngine
from app.services.pairs.loader import load_week_inputs
from app.services.pairs.params import EngineParams
from app.services.pairs.portfolio import build_weekly_portfolio
from app.services.pairs.review import approve_for_review
from app.services.scoring.wsr import (
    LegPos,
    estimate_portfolio,
    estimate_round,
    save_score_snapshot,
    scheduled_days,
    score_from_values,
)
from app.tests.mock_world import fresh_db
from app.tests.test_mcp import call, env_of, make_client

WEEK = date(2026, 9, 28)  # Monday, no US holidays that week
RESEARCH_BANNER = "Research only. Trades must be independently entered manually in Trader View. This system cannot place or manage trades."


@pytest.fixture
def db():
    return fresh_db()


def _code_set(e):
    return {r.code for r in e.ineligible_reasons}


def _evals(db):
    inputs, _, _ = load_week_inputs(db, utcnow())
    return {e.name: e for e in PeerPairEngine(EngineParams()).evaluate_all(inputs)}


def _sid(db, t):
    return next(s["id"] for s in db.data["securities"] if s["ticker"] == t)


def _dt(d: date, h=14) -> datetime:
    return datetime(d.year, d.month, d.day, h, 0, tzinfo=UTC)


def _bars(ticker, closes, days, vol=1_000_000):
    return [
        {"bar_date": d.isoformat(), "close": c, "adj_close": c, "volume": vol, "timeframe": "1D"}
        for d, c in zip(days, closes, strict=True)
    ]


# --------------------------------------------------------------- A
def test_A_five_pair_monday_portfolio(db):
    wp = build_weekly_portfolio(db, utcnow(), EngineParams())
    assert wp.status == "RESEARCH_DRAFT"
    assert len(wp.selected) == 5
    names = {s.name if hasattr(s, "name") else s["name"] for s in wp.selected}
    assert {"KO / PEP", "V / MA", "XOM / CVX", "UPS / FDX", "AMD / INTC"} == names
    # nothing was entered anywhere: no manual records, no trades
    assert not db.data.get("manual_positions") and not db.data.get("manual_trades")


# --------------------------------------------------------------- B / C / D
def test_B_earnings_event_blackout_excludes_pair(db):
    e = _evals(db)["HD / LOW"]
    assert not e.eligible and "EVENT_BLACKOUT" in _code_set(e)


def test_C_liquidity_excludes_pair(db):
    for b in db.data["market_bars"]:
        if b["security_id"] == _sid(db, "PFE"):
            b["volume"] = 100
    e = _evals(db)["MRK / PFE"]
    assert not e.eligible and "INSUFFICIENT_LIQUIDITY" in _code_set(e)


def test_D_poor_peer_relationship_excludes_pair(db):
    next(p for p in db.data["peer_pairs"] if p["name"] == "AMD / INTC")["relationship_quality"] = 2
    e = _evals(db)["AMD / INTC"]
    assert not e.eligible and "RELATIONSHIP_WEAK" in _code_set(e)


# --------------------------------------------------------------- E
def test_E_gross_exposure_warning_at_200_pct():
    days = scheduled_days(WEEK)
    pos = [
        LegPos(ticker="AAA", side="LONG", quantity=11_000, entry_price=100.0, entered_at=_dt(WEEK)),
        LegPos(
            ticker="BBB", side="SHORT", quantity=10_000, entry_price=100.0, entered_at=_dt(WEEK)
        ),
    ]
    bars = {"AAA": _bars("AAA", [100.0] * 5, days), "BBB": _bars("BBB", [100.0] * 5, days)}
    est = estimate_round(
        pos, bars, WEEK, days[-1], dividends={"AAA": [], "BBB": []}, leverage={"AAA": 1, "BBB": 1}
    )
    codes = {w.code for w in est.warnings}
    assert est.modeled_gross_exposure_pct == pytest.approx(210.0, abs=0.01)
    assert "GROSS_EXPOSURE_AT_LIMIT" in codes
    assert est.manual_recorded_gross_usd == 2_100_000  # tracked separately from modeled
    assert est.label == "ESTIMATED"
    near = estimate_round(
        pos[:1] + [pos[1].model_copy(update={"quantity": 8_000})], bars, WEEK, days[-1]
    )
    assert "GROSS_EXPOSURE_NEAR_LIMIT" in {w.code for w in near.warnings}


# --------------------------------------------------------------- F
def test_F_exact_drawdown_score_calculation():
    v = [1_000_000, 990_000, 1_009_800, 989_604, 989_604, 1_019_292.12]
    s = score_from_values(v, 5)
    # r2=-1%, r4=-2%; sum(min(r,0)^2)=0.0001+0.0004=0.0005 ; /5 = 0.0001 ; sqrt=0.01 -> DD=1.0
    assert s["DD"] == pytest.approx(1.0, abs=1e-9)
    assert s["R"] == pytest.approx(1.929212, abs=1e-9)
    assert s["player_score"] == pytest.approx(1.429212, abs=1e-9)


def test_F2_missing_days_count_in_denominator_and_floor():
    # 2 of 5 days observed: denominator is still 5
    s = score_from_values([1e6, 990_000, 980_100], 5)
    assert s["DD"] == pytest.approx((((0.01**2) * 2) / 5) ** 0.5 * 100, abs=1e-9)
    assert score_from_values([1e6, 1.0, -5e6], 5)["R"] == -100.0  # total-return floor


def test_F3_estimate_round_end_to_end_values():
    days = scheduled_days(WEEK)
    pos = [
        LegPos(ticker="AAA", side="LONG", quantity=1000, entry_price=100.0, entered_at=_dt(WEEK)),
        LegPos(ticker="BBB", side="SHORT", quantity=1000, entry_price=100.0, entered_at=_dt(WEEK)),
    ]
    bars = {
        "AAA": _bars("AAA", [101, 102, 101, 103, 104], days),
        "BBB": _bars("BBB", [99, 100, 101, 100, 98], days),
    }
    est = estimate_round(
        pos, bars, WEEK, days[-1], dividends={"AAA": [], "BBB": []}, leverage={"AAA": 1, "BBB": 1}
    )
    # V = 1e6 + 1000*(pxA - 100) - 1000*(pxB - 100)... short gains when price falls
    assert [d.value for d in est.daily] == [
        1_002_000.0,
        1_002_000.0,
        1_000_000.0,
        1_003_000.0,
        1_006_000.0,
    ]
    assert est.total_return_pct == pytest.approx(0.6)
    assert est.scheduled_days == 5 and est.days_valued == 5
    assert est.downside_deviation_pct == pytest.approx(
        ((0.002 / 1.002) ** 2 / 5) ** 0.5 * 100, abs=1e-6
    )


def test_F4_future_days_still_count_in_N_and_holiday_calendar():
    days = scheduled_days(WEEK)
    pos = [
        LegPos(ticker="AAA", side="LONG", quantity=1000, entry_price=100.0, entered_at=_dt(WEEK))
    ]
    est = estimate_round(
        pos,
        {"AAA": _bars("AAA", [99, 98], days[:2])},
        WEEK,
        days[1],
        dividends={"AAA": []},
        leverage={"AAA": 1},
    )
    assert est.scheduled_days == 5 and est.days_valued == 2
    assert len(scheduled_days(date(2026, 9, 7))) == 4  # Labor Day week: 4 scheduled days
    assert len(scheduled_days(WEEK, holidays=frozenset())) == 5


def test_estimator_dividends_liquidity_leverage_warnings():
    days = scheduled_days(WEEK)
    pos = [
        LegPos(ticker="AAA", side="LONG", quantity=1000, entry_price=100.0, entered_at=_dt(WEEK)),
        LegPos(ticker="BBB", side="SHORT", quantity=1000, entry_price=100.0, entered_at=_dt(WEEK)),
    ]
    hist = [WEEK - timedelta(days=30 - i) for i in range(25)]
    flat = {t: _bars(t, [100.0] * 30, hist + days, vol=10) for t in ("AAA", "BBB")}
    est = estimate_round(pos, flat, WEEK, days[-1], leverage={"AAA": 3.0, "BBB": 1.0})
    codes = {w.code for w in est.warnings}
    assert {"DIVIDENDS_NOT_CHECKED", "ETF_LEVERAGE_OVER_2X", "LIQUIDITY_CAP_EXCEEDED"} <= codes
    assert all("WSR data authoritative" in x["basis"] for x in est.liquidity)
    # dividend applied: long receives, short pays
    ex = WEEK + timedelta(days=2)
    est2 = estimate_round(
        pos,
        flat,
        WEEK,
        days[-1],
        dividends={"AAA": [(ex, 1.0)], "BBB": [(ex, 2.0)]},
        leverage={"AAA": 1, "BBB": 1},
    )
    assert est2.final_value == 1_000_000 + 1000 * 1.0 - 1000 * 2.0
    assert "DIVIDENDS_NOT_CHECKED" not in {w.code for w in est2.warnings}
    # suspected dividend from adj/close ratio change
    b = _bars("AAA", [100.0] * 5, days)
    for r in b[3:]:
        r["adj_close"] = 99.0
    est3 = estimate_round(pos[:1], {"AAA": b}, WEEK, days[-1])
    assert "DIVIDEND_SUSPECTED" in {w.code for w in est3.warnings}


# --------------------------------------------------------------- G
def _leg(t, side, px, q=100, d=WEEK):
    return LegEntry(
        ticker=t,
        side=side,
        quantity=q,
        entry_price=px,
        entered_at=_dt(d),
        stop_concept="thesis breaks",
        target_concept="spread converges",
    )  # noqa: E501


def test_G_manual_record_is_not_order_placement(db):
    pid = ensure_portfolio(db)
    pair = next(p for p in db.data["peer_pairs"] if p["name"] == "KO / PEP")
    # reviewing/approving a pair never creates a position
    approve_for_review(db, pair["id"], WEEK, utcnow(), eligible=True)  # review approval only
    assert not db.data.get("manual_positions") and not db.data.get("manual_trades")
    snapshot_pairs = copy.deepcopy(db.data["peer_pairs"])
    with pytest.raises(JournalError):  # explicit confirmation is mandatory
        record_manual_pair(
            db,
            portfolio_id=pid,
            name="KO/PEP",
            long_leg=_leg("KO", "LONG", 60),
            short_leg=_leg("PEP", "SHORT", 150),
            confirmed_entered_manually=False,
        )
    assert not db.data.get("manual_positions")
    out = record_manual_pair(
        db,
        portfolio_id=pid,
        name="KO/PEP",
        long_leg=_leg("KO", "LONG", 60),
        short_leg=_leg("PEP", "SHORT", 150),
        confirmed_entered_manually=True,
        peer_pair_id=pair["id"],
    )
    assert out["is_order"] is False and out["recorded"]
    # two legs recorded separately, linked by one manual pair record
    legs = db.data["manual_positions"]
    assert len(legs) == 2 and {x["side"] for x in legs} == {"LONG", "SHORT"}
    assert {x["manual_pair_record_id"] for x in legs} == {out["pair_record_id"]}
    assert all(x["source"] == "MANUAL" and x["data_status"] == "MANUAL" for x in legs)
    assert all(t["source"] == "MANUAL" and t["action"] == "OPEN" for t in db.data["manual_trades"])
    assert legs[0]["stop_concept"] and legs[0]["target_concept"]
    # recording never changes research state, and only journal tables were touched
    assert db.data["peer_pairs"] == snapshot_pairs
    assert not [t for t in db.data if "order" in t or "fill" in t or "execution" in t]
    # manual exit
    r = record_manual_exit(
        db,
        position_id=legs[0]["id"],
        exit_price=61.0,
        exited_at=_dt(WEEK + timedelta(days=2)),
        reason="target concept reached",
        confirmed_entered_manually=True,
    )
    assert r["is_order"] is False
    assert db.data["manual_positions"][0]["is_open"] is False
    with pytest.raises(JournalError):
        record_manual_exit(
            db,
            position_id=legs[0]["id"],
            exit_price=61.0,
            exited_at=_dt(WEEK),
            reason="again",
            confirmed_entered_manually=True,
        )
    record_manual_exit(
        db,
        position_id=legs[1]["id"],
        exit_price=149.0,
        exited_at=_dt(WEEK + timedelta(days=3)),
        reason="time",
        confirmed_entered_manually=True,
    )
    assert db.data["manual_pair_records"][0]["status"] == "CLOSED"


def test_G2_journal_guard_blocks_other_tables_and_forces_manual(db):
    g = JournalWriteGuard(db)
    for t in ("peer_pairs", "weekly_portfolios", "orders", "system_control_state", "pair_rankings"):
        with pytest.raises(JournalWriteViolation):
            g.upsert(t, [{"id": "x"}], "id")
    g.upsert("manual_trades", [{"id": "t1", "source": "WSR_IMPORT"}], "id")
    assert db.data["manual_trades"][0]["source"] == "MANUAL"
    with pytest.raises(JournalError):
        record_manual_entry(
            db, portfolio_id="p", leg=_leg("ZZZZ", "LONG", 10), confirmed_entered_manually=True
        )


def test_G3_estimator_from_journal_resets_per_round_and_saves_estimate(db):
    pid = ensure_portfolio(db)
    ko, pep = _sid(db, "KO"), _sid(db, "PEP")
    record_manual_pair(
        db,
        portfolio_id=pid,
        name="p",
        long_leg=_leg("KO", "LONG", 60.0, 100),
        short_leg=_leg("PEP", "SHORT", 150.0, 40),
        confirmed_entered_manually=True,
    )
    # an open leg from an earlier round is excluded (positions reset weekly)
    record_manual_entry(
        db,
        portfolio_id=pid,
        leg=_leg("KO", "LONG", 60.0, 100, d=WEEK - timedelta(days=7)),
        confirmed_entered_manually=True,
    )
    est = estimate_portfolio(db, pid, WEEK, WEEK + timedelta(days=4))
    assert est.positions_used == 2 and est.label == "ESTIMATED"
    assert "PRIOR_WEEK_POSITIONS_EXCLUDED" in {w.code for w in est.warnings}
    assert {ko, pep}  # tickers resolved via securities
    sid = save_score_snapshot(db, pid, est)
    snap = next(s for s in db.data["score_snapshots"] if s["id"] == sid)
    assert snap["is_estimate"] is True and "ESTIMATED" in snap["methodology"]


# --------------------------------------------------------------- H
def test_H_mcp_returns_read_only_candidate_data(db):
    before = copy.deepcopy(db.data)
    c = make_client(db)
    resp = call(c, "get_peer_pair_candidates", {"limit": 5})
    env = env_of(resp)
    assert env["research_only"] is True
    assert env["data"]["candidates"]
    text = json.dumps(env).lower()
    for bad in ("order_id", "submitted", "filled", "execute"):
        assert bad not in text
    assert db.data == before  # nothing written
    # no write-capable tools are exposed
    names = {
        t["name"]
        for t in c.post(
            "/mcp",
            headers={"Authorization": f"Bearer {'dev-token-' + 'x' * 40}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ).json()["result"]["tools"]
    }
    assert len(names) == 12 and not any(
        n.split("_")[0] in {"submit", "place", "cancel", "record", "save", "update", "delete"}
        for n in names
    )
