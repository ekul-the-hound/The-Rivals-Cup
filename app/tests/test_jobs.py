import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from app.config import Settings
from app.db.mock_seed import build_mock_db, sid
from app.jobs import JOBS, PROFILES, run_jobs
from app.jobs.macro import macro_regime
from app.models.enums import CatalystType
from app.services.ingestion.catalysts import detect_catalysts
from app.services.ingestion.runner import JobContext
from app.services.pairs.blackout import evaluate_leg, evaluate_pair, scoring_week
from app.services.pairs.candidates import monday_candidates
from app.services.pairs.inspect import inspect_pair, render_text
from app.services.providers.mock_transport import MockWorld
from app.services.providers.registry import build_providers
from app.services.validation.freshness import expected_last_bar, price_status

NOW = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)  # Thu 13:00 CT
TODAY = NOW.date()
S = Settings(sec_user_agent="Luke Test luke@example.com", fred_api_key="k", app_env="development")


async def nosleep(_):
    return None


def make_ctx(db=None, **kw):
    db = db or build_mock_db(TODAY, NOW)
    p = build_providers(S, MockWorld(TODAY).transport(), sleep=nosleep)
    return JobContext(settings=S, db=db, providers=p, now=NOW, **kw)


@pytest.fixture(scope="module")
def full():
    ctx = make_ctx()
    results = asyncio.run(run_jobs(ctx, PROFILES["sunday"]))
    return ctx, results


def test_all_jobs_registered_and_profiles_valid():
    assert set(JOBS) == {
        "refresh_universe", "refresh_daily_prices", "refresh_market_context", "refresh_macro_context",
        "refresh_sec_filings", "refresh_corporate_catalysts", "refresh_news", "refresh_data_quality",
        "build_weekly_event_blackout_list",
    }  # fmt: skip
    for names in PROFILES.values():
        assert set(names) <= set(JOBS)
    assert "refresh_news" in PROFILES["monday"] and "refresh_universe" not in PROFILES["monday"]


def test_pipeline_succeeds_and_logs(full):
    ctx, results = full
    assert [r.status for r in results] == ["SUCCEEDED"] * len(results)
    logs = ctx.db.select("provider_run_logs")
    assert {line["status"] for line in logs} == {"SUCCEEDED"} and len(logs) == len(results)
    assert any(line["http_requests"] for line in logs)


def test_idempotent_rerun(full):
    ctx, _ = full
    before = {t: len(rows) for t, rows in ctx.db.data.items() if t != "provider_run_logs"}
    asyncio.run(run_jobs(ctx, PROFILES["sunday"]))
    after = {t: len(rows) for t, rows in ctx.db.data.items() if t != "provider_run_logs"}
    assert before == after


def test_data_written(full):
    db = full[0].db
    assert db.count("market_bars") == 30 * 130
    ko = db.select("companies", eq={"security_id": sid("KO")})[0]
    assert ko["cik"] == "0000021344" and ko["wiki_industry"] and ko["market_cap_usd"]
    assert (
        db.select("liquidity_metrics", eq={"security_id": sid("KO")})[0]["est_max_position_usd"] > 0
    )
    macro = db.select("macro_context_snapshots")[0]
    assert macro["regime"] == "calm_vol+positive_curve" and macro["data_status"] == "AVAILABLE"
    cats = {c["catalyst_type"] for c in db.select("corporate_catalysts")}
    assert {
        "MATERIAL_AGREEMENT",
        "MANAGEMENT_CHANGE",
        "BUYBACK",
        "INSIDER",
        "REGULATORY",
        "M_AND_A",
    } <= cats
    assert not any(
        "results" in c["headline"].lower() for c in db.select("corporate_catalysts")
    )  # no 2.02


def test_blackout_exclusion_and_override(full):
    db = full[0].db
    start = scoring_week(TODAY)[0].isoformat()
    el = {e["pair_id"]: e for e in db.select("pair_weekly_eligibility", eq={"week_start": start})}
    pairs = {p["name"]: p["id"] for p in db.select("peer_pairs")}
    assert el[pairs["HD / LOW"]]["eligible"] is False
    assert el[pairs["AMD / INTC"]]["eligible"] is True and el[pairs["AMD / INTC"]]["override_id"]
    cand = monday_candidates(db, start)
    assert [x["pair"] for x in cand["excluded"]] == ["HD / LOW"]


def test_blackout_unit_rules():
    s, e = scoring_week(date(2026, 10, 3))  # Saturday -> next Monday
    assert s == date(2026, 10, 5) and e == date(2026, 10, 9)
    assert scoring_week(date(2026, 10, 1))[0] == date(2026, 9, 28)
    row = {"known_major_event_date": "2026-10-06", "manually_verified_at": NOW.isoformat()}
    leg = evaluate_leg("X", row, s, e, NOW)
    assert leg.blocked
    assert not evaluate_pair([leg], None).eligible
    assert not evaluate_pair([leg], {"reason": "  "}).eligible  # blank reason never overrides
    assert evaluate_pair([leg], {"reason": "demo event, not binary"}).eligible
    assert evaluate_leg("Y", None, s, e, NOW).warnings
    old = {"manually_verified_at": (NOW - timedelta(days=9)).isoformat()}
    assert any("older" in w for w in evaluate_leg("Z", old, s, e, NOW).warnings)


def test_data_quality_autoresolves(full):
    ctx, _ = full
    db = ctx.db
    db.upsert(
        "data_quality_issues",
        [
            {
                "dedupe_key": "x",
                "issue_type": "OLD",
                "severity": "INFO",
                "source": "auto",
                "resolved_at": None,
            }
        ],
        "dedupe_key",
    )
    asyncio.run(run_jobs(ctx, ["refresh_data_quality"]))
    x = db.select("data_quality_issues", eq={"dedupe_key": "x"})[0]
    assert x["resolved_at"] is not None


def test_gate_skips_when_paused():
    db = build_mock_db(TODAY, NOW)
    db.data["system_control_state"][0]["mode"] = "PAUSED"
    (r,) = asyncio.run(run_jobs(make_ctx(db), ["refresh_daily_prices"]))
    assert r.status == "SKIPPED" and "PAUSED" in r.message
    assert db.count("market_bars") == 0
    assert db.select("provider_run_logs")[0]["status"] == "SKIPPED"
    db.data["system_control_state"][0].update(
        mode="RESEARCH_ONLY", provider_ingestion_enabled=False
    )
    (r,) = asyncio.run(run_jobs(make_ctx(db), ["refresh_daily_prices"]))
    assert r.status == "SKIPPED"


def test_missing_provider_key_skips_job():
    ctx = make_ctx()
    ctx.providers.fred = None
    ctx.providers.unavailable["fred"] = "FRED_API_KEY not set"
    (r,) = asyncio.run(run_jobs(ctx, ["refresh_macro_context"]))
    assert r.status == "SKIPPED" and "FRED_API_KEY" in r.message


def test_partial_status_on_symbol_failure():
    db = build_mock_db(TODAY, NOW)
    db.upsert(
        "securities",
        [{"id": "bad-1", "ticker": "BADSYM", "is_etf": False, "is_active": True, "name": "Bad"}],
        "ticker",
    )
    (r,) = asyncio.run(run_jobs(make_ctx(db), ["refresh_daily_prices"]))
    assert r.status == "PARTIAL" and any("BADSYM" in w for w in r.warnings)


def test_catalyst_rules():
    f = {"accession_number": "a-1", "form_type": "8-K", "items": ["2.02", "5.02"]}
    assert [h.catalyst_type for h in detect_catalysts(f, "Item 2.02 results")] == [
        CatalystType.MANAGEMENT_CHANGE
    ]
    small = {
        "accession_number": "a-2",
        "form_type": "4",
        "form4_summary": {"transactions": [{"code": "P", "value_usd": 1000}]},
    }
    assert detect_catalysts(small) == []
    assert detect_catalysts({"accession_number": "a-3", "form_type": "10-K", "items": []}) == []


def test_freshness_and_regime():
    assert expected_last_bar(date(2026, 10, 5)) == date(2026, 10, 2)  # Monday -> Friday
    assert price_status(date(2026, 10, 2), date(2026, 10, 5)).value == "AVAILABLE"
    assert price_status(date(2026, 10, 1), date(2026, 10, 5)).value == "STALE"
    assert price_status(None, date(2026, 10, 5)).value == "MISSING"
    assert (
        macro_regime(30, -0.2) == "high_vol+inverted_curve"
        and macro_regime(None, None) == "unknown"
    )


def test_inspect_pair(full):
    ctx, _ = full
    r = inspect_pair(ctx.db, S, "HD", "LOW", NOW, size_usd=10_000_000_000)
    assert (
        r.blackout["status"] == "BLACKOUT"
        and r.blackout["eligible_for_default_monday_list"] is False
    )
    assert any("EXCEEDS" in w for w in r.liquidity_warnings)
    assert r.correlation_60d is not None and set(r.returns["HD"]) == {1, 5, 20, 60}
    text = render_text(r)
    for section in [
        "Relationship",
        "Returns",
        "Relative strength",
        "Correlation",
        "liquidity",
        "blackout",
        "catalysts",
        "Macro",
        "Missing / stale",
        "WSR liquidity-cap",
    ]:
        assert section.lower() in text.lower()
    ok = inspect_pair(ctx.db, S, "AMD", "INTC", NOW)
    assert ok.blackout["status"] == "OVERRIDDEN" and ok.blackout["override_reason"]


def test_inspect_flags_missing_data():
    db = build_mock_db(TODAY, NOW)  # nothing refreshed
    r = inspect_pair(db, S, "KO", "PEP", NOW)
    assert any("prices MISSING" in x for x in r.missing_or_stale) and r.correlation_60d is None
