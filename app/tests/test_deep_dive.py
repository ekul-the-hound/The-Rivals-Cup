"""Deep-dive data: FINRA short interest, SEC XBRL/RSS, earnings calendar, analyst ratings,
transcripts, options IV, research packets. All offline against synthetic mock transports."""

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_store
from app.api.main import app
from app.config import Settings
from app.db.mock_seed import build_mock_db
from app.jobs import PROFILES, run_jobs
from app.jobs.deep_dive import last_completed_quarters
from app.services.deep_dive.packet import analyst_score, build_packet
from app.services.providers.base import ProviderError
from app.services.providers.finra import (
    candidate_settlement_dates,
    parse_short_interest,
    parse_short_volume,
)
from app.services.providers.mock_transport import MockWorld
from app.services.providers.registry import build_providers
from app.services.providers.sec import parse_atom_feed, summarize_company_facts
from app.services.universe.repo import load_universe
from app.tests.test_jobs import NOW, TODAY, nosleep

KEYS = dict(sec_user_agent="Luke Test luke@example.com", fred_api_key="k", app_env="development")


def ctx_with(**kw):
    from app.services.ingestion.runner import JobContext

    s = Settings(**{**KEYS, **kw})
    p = build_providers(s, MockWorld(TODAY).transport(), sleep=nosleep)
    return JobContext(settings=s, db=build_mock_db(TODAY, NOW), providers=p, now=NOW)


@pytest.fixture(scope="module")
def full():
    ctx = ctx_with(
        finnhub_api_key="k",
        alpha_vantage_api_key="k",
        options_iv_enabled=True,
        deep_dive_tickers="MHLT,MIND,MBNK",
    )
    res = {}
    for prof in ("universe", "deepmarket", "deepdive"):
        for r in asyncio.run(run_jobs(ctx, PROFILES[prof])):
            res[r.job] = r
    return ctx, res


# ---------- parsing ----------
def test_short_interest_parse_is_header_driven_and_delimiter_agnostic():
    csv_text = 'symbolCode,currentShortPositionQuantity,previousShortPositionQuantity,daysToCoverQuantity,settlementDate\nBRK.B,"1,200",1000,2.5,2026-09-15\n'
    pipe = csv_text.replace(",", "|").replace('"1|200"', '"1,200"')
    for text in (csv_text, pipe):
        r = parse_short_interest(text, date(2026, 9, 15))[0]
        assert r.ticker == "BRK-B" and r.short_interest_shares == 1200 and r.days_to_cover == 2.5
    with pytest.raises(ProviderError):
        parse_short_interest("<html>blocked</html>\nfoo", date(2026, 9, 15))


def test_short_volume_parse_skips_trailer():
    text = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20260930|AAA|40|1|100|Q\n99 records"
    rows = parse_short_volume(text, date(2026, 9, 30))
    assert len(rows) == 1 and rows[0].ratio == 0.4 and rows[0].trade_date == date(2026, 9, 30)


def test_settlement_dates_are_weekdays_and_past():
    ds = candidate_settlement_dates(date(2026, 10, 2))
    assert ds == sorted(ds, reverse=True) and all(
        d.weekday() < 5 and d < date(2026, 10, 2) for d in ds
    )


def test_atom_feed_and_xbrl_summary():
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>8-K - ACME CORP (0000012345) (Filer)</title>'
        '<link href="https://x/y"/><updated>2026-10-01T10:00:00-04:00</updated>'
        "<id>urn:tag:sec.gov,2008:accession-number=0000012345-26-000007</id></entry></feed>"
    )
    e = parse_atom_feed(xml)[0]
    assert (
        e.accession_number == "0000012345-26-000007"
        and e.cik == "0000012345"
        and e.form_type == "8-K"
    )
    with pytest.raises(ProviderError):
        parse_atom_feed("<html>")
    s = summarize_company_facts(MockWorld(TODAY)._facts(9000001))
    assert (
        s["revenue"] == 1_000_000_000
        and s["revenue_growth_pct"] == 25.0
        and s["shares_outstanding"] == 50_000_000
    )
    assert s["metrics"]["revenue"]["tag"] == "Revenues"


def test_analyst_score_and_quarters():
    assert analyst_score({"strong_buy": 2}) == 2.0 and analyst_score({"sell": 1, "buy": 1}) == 0.0
    assert analyst_score({}) is None
    assert last_completed_quarters(date(2026, 1, 10)) == ["2025Q4", "2025Q3"]
    assert last_completed_quarters(date(2026, 10, 2)) == ["2026Q3", "2026Q2"]


def test_api_keys_never_enter_cache_keys():
    p = build_providers(
        Settings(**KEYS, finnhub_api_key="SECRETVALUE"), MockWorld(TODAY).transport(), sleep=nosleep
    )
    assert "SECRETVALUE" not in p.finnhub.http._key("u", {"symbol": "X", "token": "SECRETVALUE"})


# ---------- jobs ----------
def test_market_wide_jobs_keep_only_target_sector_names(full):
    ctx, res = full
    assert all(
        r.status in ("SUCCEEDED", "PARTIAL")
        for k, r in res.items()
        if k.startswith(("refresh_short", "refresh_sec", "refresh_earnings_cal"))
    )
    tickers = {r["ticker"] for r in ctx.db.data["short_interest"]}
    assert tickers and tickers <= {r["ticker"] for r in ctx.db.data["security_master"]}
    assert {r["ticker"] for r in ctx.db.data["sec_filing_feed"]} == {
        "MHLT"
    }  # UNKNOWN CO has no match
    assert "NOTLISTED" not in {r["ticker"] for r in ctx.db.data["earnings_calendar"]}


def test_earnings_calendar_feeds_blackout_date_into_security_master(full):
    ctx, _ = full
    rows = {r["ticker"]: r for r in load_universe(ctx.db)}
    assert str(rows["MHLT"]["earnings_date_if_known"]) == "2026-10-02"
    assert (
        "EVENT_BLACKOUT" in rows["MHLT"]["exclusion_reasons"]
        and not rows["MHLT"]["in_pair_research_eligible"]
    )


def test_manual_earnings_date_is_not_overwritten():
    from app.services.universe.overrides import manual_override

    ctx = ctx_with(finnhub_api_key="k", alpha_vantage_api_key="k")
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    manual_override(
        ctx.db, "MHLT", {"earnings_date_if_known": "2026-10-20"}, "company confirmed date", NOW
    )
    asyncio.run(run_jobs(ctx, ["refresh_earnings_calendar"]))
    m = {r["ticker"]: r for r in ctx.db.data["security_master"]}["MHLT"]
    assert str(m["earnings_date_if_known"]) == "2026-10-20"


def test_per_company_jobs_store_data(full):
    ctx, res = full
    assert {r["ticker"] for r in ctx.db.data["analyst_recommendations"]} == {"MHLT", "MIND", "MBNK"}
    assert {r["ticker"] for r in ctx.db.data["company_fundamentals"]} == {"MHLT", "MIND", "MBNK"}
    assert (
        len(ctx.db.data["earnings_transcripts"]) == 3
        and len(ctx.db.data["options_iv_snapshots"]) == 3
    )
    iv = ctx.db.data["options_iv_snapshots"][0]
    assert iv["atm_implied_vol"] == pytest.approx(0.36)


def test_jobs_skip_without_keys_shortlist_or_flag():
    ctx = ctx_with()  # no keys, no shortlist
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    out = {
        r.job: r
        for r in asyncio.run(run_jobs(ctx, PROFILES["deepdive"] + ["refresh_earnings_calendar"]))
    }
    assert all(r.status == "SKIPPED" for r in out.values())
    ctx2 = ctx_with(finnhub_api_key="k", alpha_vantage_api_key="k", deep_dive_tickers="MHLT")
    asyncio.run(run_jobs(ctx2, PROFILES["universe"]))
    r = asyncio.run(run_jobs(ctx2, ["refresh_options_iv"]))[0]
    assert r.status == "SKIPPED" and "OPTIONS_IV_ENABLED" in r.message


def test_transcript_job_respects_daily_budget():
    ctx = ctx_with(
        alpha_vantage_api_key="k", deep_dive_tickers="MHLT,MIND,MBNK", alpha_vantage_daily_budget=2
    )
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    r = asyncio.run(run_jobs(ctx, ["refresh_earnings_transcripts"]))[0]
    assert len(ctx.db.data["earnings_transcripts"]) == 2 and any("budget" in w for w in r.warnings)
    r2 = asyncio.run(run_jobs(ctx, ["refresh_earnings_transcripts"]))[0]  # next day resumes
    assert len(ctx.db.data["earnings_transcripts"]) == 3 and r2.rows_written == 1


def test_options_refusal_is_tolerated():
    ctx = ctx_with(options_iv_enabled=True, deep_dive_tickers="BADX")
    r = asyncio.run(run_jobs(ctx, ["refresh_options_iv"]))[0]
    assert r.status == "FAILED" and not ctx.db.data.get("options_iv_snapshots")


# ---------- packet / API ----------
def test_research_packet_and_gaps(full):
    ctx, _ = full
    p = build_packet(ctx.db, "MHLT", now=NOW)
    assert (
        p["short_interest"]["latest"]
        and p["analyst"]["score_latest"]
        and p["fundamentals"]["revenue"]
    )
    assert p["data_gaps"] == [] and p["disclaimer"].startswith("This is a research universe")
    thin = build_packet(ctx.db, "MUTL")
    assert any("analyst" in g for g in thin["data_gaps"]) and thin["fundamentals"] is None
    with pytest.raises(KeyError):
        build_packet(ctx.db, "ZZZZ")


def test_packet_endpoint(full):
    ctx, _ = full
    app.dependency_overrides[get_store] = lambda: ctx.db
    try:
        c = TestClient(app)
        assert c.get("/universe/MHLT/research-packet").json()["ticker"] == "MHLT"
        assert c.get("/universe/ZZZZ/research-packet").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_duplicate_symbol_lines_are_summed_not_rejected(monkeypatch):
    from app.services.providers.finra import ShortVolumeRow

    ctx = ctx_with()
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))

    async def dup(_today):
        d = date(2026, 9, 30)
        return [ShortVolumeRow(ticker="MHLT", trade_date=d, short_volume=40, short_exempt_volume=1, total_volume=100),
                ShortVolumeRow(ticker="MHLT", trade_date=d, short_volume=10, short_exempt_volume=0, total_volume=100)]  # fmt: skip

    monkeypatch.setattr(ctx.providers.finra, "latest_short_volume", dup)
    r = asyncio.run(run_jobs(ctx, ["refresh_short_sale_volume"]))[0]
    row = ctx.db.data["short_sale_volume_daily"][0]
    assert (
        r.status == "SUCCEEDED" and row["short_volume"] == 50 and row["short_volume_ratio"] == 0.25
    )


def test_upsert_uniform_dedupes_on_conflict_keys():
    from app.db.memory import InMemoryDB
    from app.services.universe.master import upsert_uniform

    db = InMemoryDB()
    n = upsert_uniform(db, "t", [{"k": "a", "v": 1}, {"k": "a", "v": 2}, {"k": "b", "v": 3}], "k")
    assert n == 2 and {r["k"]: r["v"] for r in db.data["t"]} == {"a": 2, "b": 3}


def test_summarize_prefers_freshest_xbrl_tag():
    from app.services.providers.sec import summarize_company_facts

    def ent(end, val, filed):
        return {"end": end, "val": val, "form": "10-K", "fy": int(end[:4]), "fp": "FY",
                "start": f"{int(end[:4])}-01-01", "filed": filed, "accn": "a"}  # fmt: skip

    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [ent("2019-12-31", 1, "2020-02-01"), ent("2020-12-31", 2, "2021-02-01")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            ent("2024-12-31", 8, "2025-02-01"), ent("2025-12-31", 10, "2026-02-01")]}},
    }}}  # fmt: skip
    out = summarize_company_facts(facts)
    assert out["revenue"] == 10 and out["period_end"] == "2025-12-31"
    assert out["metrics"]["revenue"]["tag"].startswith("RevenueFromContract")


def test_peer_candidates_rank_by_size_similarity():
    from app.services.deep_dive.packet import peer_candidates

    def r(t, adv):
        return {"ticker": t, "sector": "S", "industry": "I", "in_pair_research_eligible": True,
                "average_dollar_volume_20d": adv}  # fmt: skip

    rows = [r("ME", 1e9), r("BIG", 2e9), r("TINY", 1e5), r("NEAR", 8e8)]
    assert [p["ticker"] for p in peer_candidates(rows, rows[0])] == ["NEAR", "BIG", "TINY"]
