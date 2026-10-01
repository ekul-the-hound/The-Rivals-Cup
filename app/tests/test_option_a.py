"""Option A: PeerPairEngine, weekly portfolio selection, review state, and safety guards."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_admin_db, get_store
from app.api.main import app
from app.config.clock import utcnow
from app.db.memory import InMemoryDB
from app.services.pairs.engine import PeerPairEngine
from app.services.pairs.factors import FactorProfile, factor_overlap_penalty, pair_overlap
from app.services.pairs.loader import load_week_inputs
from app.services.pairs.params import WEIGHTS, EngineParams
from app.services.pairs.portfolio import build_weekly_portfolio
from app.services.pairs.review import ResearchWriteGuard, ResearchWriteViolation
from app.tests.mock_world import fresh_db


@pytest.fixture
def db() -> InMemoryDB:
    return fresh_db()


def _evals(db, p: EngineParams | None = None):
    inputs, _, _ = load_week_inputs(db, utcnow())
    return {e.name: e for e in PeerPairEngine(p or EngineParams()).evaluate_all(inputs)}


def _sid(db, ticker):
    return next(s["id"] for s in db.data["securities"] if s["ticker"] == ticker)


def _codes(e):
    return {r.code for r in e.ineligible_reasons}


# ------------------------------------------------------------------ the 8 named tests
def test_valid_pair_passes_with_full_packet(db):
    e = _evals(db)["KO / PEP"]
    assert e.eligible and not e.ineligible_reasons
    assert 60 <= e.score <= 100 and sum(WEIGHTS.values()) == 100
    pk = e.packet
    assert pk.long_ticker and pk.short_ticker and pk.long_ticker != pk.short_ticker
    assert pk.top_reasons and pk.counter_thesis and pk.manual_checklist
    assert pk.event_blackout["status"] == "CLEAR"
    assert {z.side for z in pk.entry_zones} == {"LONG", "SHORT"}
    assert "NOT an executable instruction" in pk.entry_zones[0].basis
    assert (
        "Not a stop order" in pk.invalidation_concept and "Not a limit order" in pk.target_concept
    )
    assert pk.review_cadence and "RESEARCH CANDIDATE ONLY" in pk.status_note
    for k in (
        "returns",
        "spread_long_minus_short",
        "correlation_60d",
        "beta_vs_spy_60d",
        "liquidity",
    ):
        assert k in pk.metrics
    assert set(pk.metrics["returns"][pk.long_ticker]) == {1, 5, 20, 60}


def test_event_blackout_pair_excluded_and_override_logged(db):
    ev = _evals(db)
    assert not ev["HD / LOW"].eligible and "EVENT_BLACKOUT" in _codes(ev["HD / LOW"])
    assert ev["HD / LOW"].packet.event_blackout["status"] == "BLACKOUT"
    # AMD/INTC has a major event but a logged override in the mock data
    assert ev["AMD / INTC"].eligible and "BLACKOUT_OVERRIDDEN" in ev["AMD / INTC"].packet.risk_flags
    db.data["pair_blackout_overrides"].clear()
    assert "EVENT_BLACKOUT" in _codes(_evals(db)["AMD / INTC"])


def test_low_liquidity_pair_excluded(db):
    sid = _sid(db, "PFE")
    for b in db.data["market_bars"]:
        if b["security_id"] == sid:
            b["volume"] = 100
    e = _evals(db)["MRK / PFE"]
    assert not e.eligible and "INSUFFICIENT_LIQUIDITY" in _codes(e)


def test_poor_peer_mapping_excluded(db):
    pair = next(p for p in db.data["peer_pairs"] if p["name"] == "AMD / INTC")
    pair["relationship_quality"] = 2
    assert "RELATIONSHIP_WEAK" in _codes(_evals(db)["AMD / INTC"])
    pair["relationship_quality"] = 5
    pair["relationship_explanation"] = "  "
    assert "RELATIONSHIP_NOT_MAPPED" in _codes(_evals(db)["AMD / INTC"])
    # one leg missing from the mapping
    pair["relationship_explanation"] = "ok"
    db.data["peer_pair_members"] = [
        m
        for m in db.data["peer_pair_members"]
        if not (m["pair_id"] == pair["id"] and m["role"] == "B")
    ]
    assert "RELATIONSHIP_NOT_MAPPED" in _codes(_evals(db)["AMD / INTC"])


def test_factor_overlap_penalty():
    a = FactorProfile("Energy", "cyclical_commodity", "oil_price")
    same = FactorProfile("Energy", "cyclical_commodity", "oil_price")
    other = FactorProfile("Health Care", "defensive", "healthcare_defensive")
    p = EngineParams()
    assert pair_overlap(a, other)[0] == 0
    assert pair_overlap(a, same)[0] == 38  # driver 20 + cluster 12 + sector 6
    pen, why = factor_overlap_penalty(a, [same, same, same], p)
    assert pen == p.overlap_cap and why  # capped
    assert factor_overlap_penalty(a, [other], p)[0] == 0
    # materials vs energy share a cluster even in different sectors (energy/materials clustering)
    mat = FactorProfile("Materials", "cyclical_commodity", "commodities_cycle")
    assert pair_overlap(a, mat)[0] == 12


def test_portfolio_overlap_and_cluster_limits_applied(db):
    pf = build_weekly_portfolio(db, utcnow())
    drivers = [s.factor["driver"] for s in pf.selected]
    assert len(drivers) == len(set(drivers))  # one pair per macro driver
    clusters = [s.factor["cluster"] for s in pf.selected]
    assert max(clusters.count(c) for c in set(clusters)) <= EngineParams().max_per_cluster
    names = {a.name: a for a in pf.alternates}
    assert any("overlap" in " ".join(a.why_not_selected) for a in names.values())


def test_portfolio_selects_fewer_than_six_if_quality_is_low(db):
    pf = build_weekly_portfolio(db, utcnow())
    assert 1 <= len(pf.selected) < 6  # 8 sample pairs, but not all qualify
    strict = build_weekly_portfolio(db, utcnow(), EngineParams(min_score=75, min_adjusted_score=70))
    assert len(strict.selected) < len(pf.selected)
    none = build_weekly_portfolio(db, utcnow(), EngineParams(min_score=99, min_adjusted_score=99))
    assert none.selected == [] and any("NO PAIRS QUALIFIED" in w for w in none.warnings)
    assert none.summary["short_of_target"] is True
    short = [w for w in strict.warnings if "met the quality bar" in w]
    assert len(strict.selected) >= EngineParams().target_min_pairs or short


def test_max_pairs_hard_capped_at_six(db):
    with pytest.raises(ValueError):
        EngineParams(max_pairs=7)
    pf = build_weekly_portfolio(
        db,
        utcnow(),
        EngineParams(
            max_pairs=6, min_score=0, min_adjusted_score=0, max_per_cluster=6, max_per_driver=6
        ),
    )
    assert len(pf.selected) <= 6


EXEC_WORDS = ("place_order", "submit_order", "create_order", "send_order", "cancel_order", "broker", "execute_trade", "selenium", "playwright", "telegram", "paper_trade")  # fmt: skip


def test_pair_selection_has_no_execution_code():
    root = Path(__file__).resolve().parents[1]
    files = list((root / "services" / "pairs").glob("*.py")) + [
        root / "api" / "pairs.py",
        root / "schemas" / "pairs.py",
    ]
    for f in files:
        text = f.read_text().lower()
        for w in EXEC_WORDS:
            assert w not in text, f"{w} found in {f.name}"
    from scripts.check_no_execution import scan

    assert scan() == []


def test_manual_review_state_is_not_a_trade(db):
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        c = TestClient(app)
        pid = next(p["id"] for p in db.data["peer_pairs"] if p["name"] == "KO / PEP")
        r = c.post(f"/pairs/{pid}/manual-approve-for-review", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["decision"] == "INCLUDE" and body["is_trade"] is False
        assert body["pair_status"] == "APPROVED_FOR_REVIEW"
        pair = next(p for p in db.data["peer_pairs"] if p["id"] == pid)
        assert pair["status"] == "APPROVED_FOR_REVIEW" and pair["status"] != "ACTIVE_MANUALLY"
        for t in ("manual_trades", "manual_positions", "manual_trade_events"):
            assert db.data.get(t, []) == []  # nothing recorded as traded
        pf = c.post("/pairs/build-weekly-portfolio", json={}).json()
        assert pf["status"] == "RESEARCH_DRAFT"
        assert all(s["is_trade"] is False for s in pf["selected"])
        assert db.data.get("manual_trades", []) == []
        assert (
            next(p for p in db.data["peer_pairs"] if p["id"] == pid)["status"]
            == "APPROVED_FOR_REVIEW"
        )
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------ guard
def test_write_guard_blocks_trade_tables_and_states(db):
    g = ResearchWriteGuard(db)
    for t in (
        "manual_trades",
        "manual_positions",
        "manual_trade_events",
        "orders",
        "system_control_state",
    ):
        with pytest.raises(ResearchWriteViolation):
            g.upsert(t, [{"x": 1}], "id")
    with pytest.raises(ResearchWriteViolation):
        g.upsert("peer_pairs", [{"id": "x", "status": "ACTIVE_MANUALLY"}], "id")
    with pytest.raises(ResearchWriteViolation):
        g.upsert("peer_pairs", [{"id": "x", "status": "CANDIDATE", "name": "renamed"}], "id")
    with pytest.raises(ResearchWriteViolation):
        g.upsert("weekly_portfolios", [{"run_id": "r", "status": "ACTIVE"}], "run_id")
    assert g.upsert("pair_manual_decisions", [{"pair_id": "p", "week_start": "2026-09-28", "decision": "EXCLUDE", "reason": "just because"}], "pair_id,week_start") == 1  # fmt: skip


# ------------------------------------------------------------------ more hard checks
def test_other_hard_eligibility_checks(db):
    def sec(t):
        return next(s for s in db.data["securities"] if s["ticker"] == t)

    sec("KO")["exchange"] = "OTC"
    assert "NOT_US_LISTED_EQUITY" in _codes(_evals(db)["KO / PEP"])
    sec("KO")["exchange"] = "NYSE"
    sec("V")["unresolved_corporate_action"] = True
    assert "CORPORATE_ACTION" in _codes(_evals(db)["V / MA"])
    sec("V")["unresolved_corporate_action"] = False
    sid = _sid(db, "JPM")
    db.data["market_bars"] = [b for b in db.data["market_bars"] if b["security_id"] != sid][:]
    ev = _evals(db)["JPM / BAC"]
    assert not ev.eligible and "INSUFFICIENT_HISTORY" in _codes(ev)
    # stale critical prices
    sid = _sid(db, "XOM")
    for b in db.data["market_bars"]:
        if b["security_id"] == sid:
            b["bar_date"] = (
                date.fromisoformat(str(b["bar_date"])[:10]) - timedelta(days=60)
            ).isoformat()
    assert "STALE_PRICES" in _codes(_evals(db)["XOM / CVX"])


def test_low_quality_catalyst_cannot_be_core_evidence(db):
    pair = next(p for p in db.data["peer_pairs"] if p["name"] == "KO / PEP")
    pair["thesis_basis"] = "CATALYST"
    for c in db.data.get("corporate_catalysts", []):
        c["evidence_quality"] = "UNVERIFIED"
    for n in db.data.get("news_items", []):
        n["evidence_quality"] = "UNVERIFIED"
    assert "NO_QUALITY_CATALYST_EVIDENCE" in _codes(_evals(db)["KO / PEP"])


def test_leveraged_etf_leg_rejected(db):
    ko = next(s for s in db.data["securities"] if s["ticker"] == "KO")
    ko.update(is_etf=True, security_type="ETF", leverage_factor=3)
    assert {"LEVERAGED_ETF", "NOT_US_LISTED_EQUITY"} <= _codes(_evals(db)["KO / PEP"])


def test_manual_exclude_removes_pair_and_include_cannot_bypass_hard_rules(db):
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        c = TestClient(app)
        ids = {p["name"]: p["id"] for p in db.data["peer_pairs"]}
        assert (
            c.post(f"/pairs/{ids['KO / PEP']}/manual-exclude", json={"reason": "x"}).status_code
            == 422
        )
        r = c.post(
            f"/pairs/{ids['KO / PEP']}/manual-exclude", json={"reason": "prefer to skip staples"}
        )
        assert r.status_code == 200 and r.json()["is_trade"] is False
        pf = c.post("/pairs/build-weekly-portfolio", json={}).json()
        assert "KO / PEP" not in [s["name"] for s in pf["selected"]]
        assert "MANUALLY_EXCLUDED" in str(pf["excluded"])
        # HD/LOW is blacked out: approval for review must be refused (409)
        assert (
            c.post(f"/pairs/{ids['HD / LOW']}/manual-approve-for-review", json={}).status_code
            == 409
        )
        assert c.post("/pairs/nope/manual-approve-for-review", json={}).status_code == 404
        assert not any(
            d["pair_id"] == ids["HD / LOW"] for d in db.data.get("pair_manual_decisions", [])
        )
    finally:
        app.dependency_overrides.clear()


def test_pinned_pair_is_selected_first(db):
    pid = next(p["id"] for p in db.data["peer_pairs"] if p["name"] == "AMD / INTC")
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        c = TestClient(app)
        assert c.post(f"/pairs/{pid}/manual-approve-for-review", json={}).status_code == 200
        pf = build_weekly_portfolio(db, utcnow())
        s = next(x for x in pf.selected if x.name == "AMD / INTC")
        assert s.pinned_by_owner
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------ API
def test_pairs_api_endpoints(db):
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        c = TestClient(app)
        cand = c.get("/pairs/candidates").json()
        assert {x["name"] for x in cand["eligible"]} >= {"KO / PEP"}
        assert "HD / LOW" in {x["name"] for x in cand["ineligible"]}
        pid = next(p["id"] for p in db.data["peer_pairs"] if p["name"] == "KO / PEP")
        pk = c.get(f"/pairs/{pid}/packet").json()
        assert pk["eligible"] and pk["packet"]["pair_quality_score"] > 0
        assert c.get("/pairs/not-a-pair/packet").status_code == 404
        assert c.get("/pairs/weekly-portfolio").status_code == 404
        assert c.post("/pairs/build-weekly-portfolio", json={"max_pairs": 7}).status_code == 422
        built = c.post("/pairs/build-weekly-portfolio", json={"max_pairs": 4}).json()
        assert len(built["selected"]) <= 4
        got = c.get("/pairs/weekly-portfolio").json()
        assert got["run_id"] == built["run_id"] and got["status"] == "RESEARCH_DRAFT"
        assert db.data["pair_rankings"] and db.data["weekly_portfolios"]
        # PAUSED blocks building
        db.data["system_control_state"][0]["mode"] = "PAUSED"
        assert c.post("/pairs/build-weekly-portfolio", json={}).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_pairs_post_routes_require_auth():
    c = TestClient(app)
    assert c.post("/pairs/build-weekly-portfolio").status_code == 401
    assert c.post("/pairs/abc/manual-exclude", json={"reason": "because"}).status_code == 401
