"""Target-sector universe builder: listings, classification, views, overrides, export, API."""

import asyncio
import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_admin_db, get_store
from app.api.main import app
from app.config import Settings
from app.db.memory import InMemoryDB
from app.db.mock_seed import build_mock_db
from app.db.store import select_all
from app.jobs import PROFILES, run_jobs
from app.models.universe import DISCLAIMER, TARGET_SECTORS
from app.services.providers.nasdaq_trader import parse_listing_file
from app.services.universe.classify import classify_listing, normalize_symbol
from app.services.universe.export import EXPORT_COLUMNS, export_records, render_csv, render_json
from app.services.universe.master import UniverseWriteGuard, UniverseWriteViolation
from app.services.universe.overrides import InvalidChange, manual_override, manual_verify
from app.services.universe.repo import filter_rows, load_universe
from app.services.universe.sectors import CsvSectorMappingAdapter, normalize_sector
from app.services.universe.summary import summarize
from app.services.universe.views import UniverseParams, evaluate
from app.tests.test_jobs import NOW, TODAY, S, make_ctx

P = UniverseParams()


@pytest.fixture(scope="module")
def built():
    ctx = make_ctx()
    results = asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    return ctx, {r.job: r for r in results}, {r["ticker"]: r for r in load_universe(ctx.db)}


def row(**kw):
    base = dict(
        ticker="TST", security_type="COMMON_STOCK", is_common_stock=True, is_active=True, sector="INDUSTRIALS",
        sector_data_status="AVAILABLE", average_dollar_volume_20d=5e7, last_price=20.0,
        liquidity_data_as_of=TODAY, competition_tradable_status="UNKNOWN", cik="1",
    )  # fmt: skip
    return {**base, **kw}


# ---------- source parsing ----------
def test_nasdaq_trader_files_parse_and_dedupe(built):
    ctx, _, _ = built
    from app.services.providers.mock_transport import NASDAQ_LISTED_MOCK, OTHER_LISTED_MOCK

    nq = parse_listing_file(NASDAQ_LISTED_MOCK, "nasdaqtrader:nasdaqlisted")
    ot = parse_listing_file(OTHER_LISTED_MOCK, "nasdaqtrader:otherlisted")
    assert nq.created_at == datetime(2026, 10, 1, 21, 30, tzinfo=UTC)
    assert {r.exchange for r in ot.rows} == {"NYSE", "NYSE American", "NYSE Arca"}
    assert next(r for r in nq.rows if r.symbol == "MTST").test_issue
    assert next(r for r in ot.rows if r.symbol == "MXLF").etf
    listings, _ = asyncio.run(ctx.providers.require("nasdaq").all_listings())
    assert len(listings) == len(nq.rows) + len(ot.rows)


def test_bad_directory_file_is_rejected():
    from app.services.providers.base import ProviderError

    with pytest.raises(ProviderError):
        parse_listing_file("<html>blocked</html>", "x")


def test_universe_comes_from_source_not_a_hardcoded_list(built):
    _, res, rows = built
    assert res["refresh_us_listed_symbol_universe"].rows_read == 23
    assert "KO" not in rows and "MHLT" in rows  # the S&P-style mock names are not in this universe
    import pathlib

    for f in pathlib.Path("app/services/universe").glob("*.py"):
        assert '"MHLT"' not in f.read_text()


# ---------- classification & sector ----------
def test_normalize_sector_labels():
    assert normalize_sector("Healthcare") == "HEALTH_CARE" == normalize_sector("Health Care")
    assert normalize_sector("Financial Services") == "FINANCIALS" == normalize_sector("Financials")
    assert normalize_sector("Real Estate") == "REAL_ESTATE"
    assert normalize_sector("Utilities") == "UTILITIES"
    assert normalize_sector("Industrials") == "INDUSTRIALS"
    assert normalize_sector("Technology") == "OTHER"
    assert normalize_sector("Mock Sector") is None and normalize_sector("") is None


@pytest.mark.parametrize(
    ("name", "etf", "expected"),
    [
        ("Acme Corp. - Common Stock", False, "COMMON_STOCK"),
        ("Acme Index ETF", True, "ETF"),
        ("Acme Preferred Stock Series A", False, "PREFERRED"),
        ("Acme Corp. - Warrants", False, "WARRANT"),
        ("Acme Corp. - Rights", False, "RIGHT"),
        ("Acme Acquisition Corp. - Units", False, "UNIT"),
        ("Acme plc American Depositary Shares", False, "ADR"),
        ("Acme Income Fund", False, "FUND"),
        ("Acme Holdings", False, "UNKNOWN"),
    ],
)
def test_security_type_classification(name, etf, expected):
    assert classify_listing("ACME", name, etf).security_type == expected


def test_never_guesses_sector_from_name(built):
    _, _, rows = built
    assert rows["MUNK"]["sector"] is None and rows["MUNK"]["sector_data_status"] == "MISSING"
    assert rows["MUNK"]["in_manual_review"] and "SECTOR_MISSING" in rows["MUNK"]["review_reasons"]
    assert not rows["MUNK"]["in_all_target_sector_listings"]


def test_symbol_normalisation():
    assert normalize_symbol("BRK.B") == "BRK-B"


# ---------- exclusions ----------
@pytest.mark.parametrize(
    ("tk", "reason"),
    [("MHET", "ETF"), ("MLEV", "LEVERAGED_PRODUCT"), ("MBNK$A", "PREFERRED"), ("MWRT", "WARRANT"),
     ("MRGT", "RIGHT"), ("MUNT", "UNIT"), ("MTST", "TEST_ISSUE"), ("MFND", "FUND"),
     ("MTEC", "NON_TARGET_SECTOR"), ("MNEW", "SECURITY_TYPE_UNCERTAIN")],
)  # fmt: skip
def test_excluded_from_every_view(built, tk, reason):
    _, _, rows = built
    r = rows[tk]
    assert reason in r["exclusion_reasons"]
    assert not r["in_all_target_sector_listings"] and not r["in_pair_research_eligible"]


def test_target_sector_counts_and_all_five_sectors(built):
    ctx, res, rows = built
    s = summarize(list(rows.values()))
    assert set(s["by_sector"]) == set(TARGET_SECTORS)
    assert s["by_sector"] == {
        "HEALTH_CARE": 4,
        "INDUSTRIALS": 2,
        "FINANCIALS": 3,
        "UTILITIES": 1,
        "REAL_ESTATE": 1,
    }
    assert s["pair_research_eligible"] == 8 and s["manual_review"] == 4
    assert "all_target_sector_listings=11" in res["build_target_sector_universe_views"].message


def test_adr_and_reit_are_included_but_flagged(built):
    _, _, rows = built
    assert rows["MADR"]["in_all_target_sector_listings"] and "ADR" in rows["MADR"]["flags"]
    assert "ADR_VERIFY_TRADABILITY" in rows["MADR"]["review_reasons"]
    assert (
        rows["MREI"]["is_reit"]
        and "REIT" in rows["MREI"]["flags"]
        and rows["MREI"]["sector"] == "REAL_ESTATE"
    )


def test_every_row_flags_tradability_unknown(built):
    _, _, rows = built
    assert all("TRADABILITY_UNKNOWN" in r["flags"] for r in rows.values())


def test_liquidity_stale_price_and_missing_data_rules():
    assert "ILLIQUID" in evaluate(row(average_dollar_volume_20d=1e5), P, TODAY).exclusion_reasons
    assert "BELOW_MIN_PRICE" in evaluate(row(last_price=0.5), P, TODAY).exclusion_reasons
    stale = row(liquidity_data_as_of=TODAY - timedelta(days=10))
    assert "MARKET_DATA_STALE" in evaluate(stale, P, TODAY).exclusion_reasons
    assert "MARKET_DATA_MISSING" in evaluate(row(last_price=None), P, TODAY).exclusion_reasons
    ok = evaluate(row(), P, TODAY)
    assert ok.in_all_target and ok.in_pair_eligible


def test_event_blackout_week_excludes_from_pair_view_only():
    ev = evaluate(row(earnings_date_if_known=TODAY), P, TODAY)
    assert ev.in_all_target and not ev.in_pair_eligible and "EVENT_BLACKOUT" in ev.exclusion_reasons


def test_unverified_sector_excluded_unless_explicitly_allowed():
    r = row(sector_data_status="UNVERIFIED")
    assert "SECTOR_UNVERIFIED" in evaluate(r, P, TODAY).exclusion_reasons
    assert evaluate(r, UniverseParams(allow_unverified_sector=True), TODAY).in_all_target


def test_inactive_and_delisted_excluded():
    assert "INACTIVE_OR_DELISTED" in evaluate(row(is_active=False), P, TODAY).exclusion_reasons


# ---------- refresh behaviour ----------
def test_delisted_symbol_is_deactivated_and_reappearing_symbol_reactivated():
    db = build_mock_db(TODAY, NOW)
    db.upsert(
        "security_master",
        [{"ticker": "GONE", "company_name": "Gone Inc.", "listing_source": "nasdaqtrader:nasdaqlisted",
          "security_type": "COMMON_STOCK", "is_common_stock": True, "is_active": True, "sector": "INDUSTRIALS",
          "sector_data_status": "AVAILABLE"}],
        "ticker",
    )  # fmt: skip
    ctx = make_ctx(db)
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    rows = {r["ticker"]: r for r in load_universe(ctx.db)}
    assert rows["GONE"]["is_active"] is False
    assert "INACTIVE_OR_DELISTED" in rows["GONE"]["exclusion_reasons"]


def test_shrunken_directory_does_not_mass_deactivate():
    db = build_mock_db(TODAY, NOW)
    big = [
        {"ticker": f"T{i}", "listing_source": "nasdaqtrader:nasdaqlisted", "security_type": "COMMON_STOCK",
         "is_common_stock": True, "is_active": True} for i in range(100)
    ]  # fmt: skip
    db.upsert("security_master", big, "ticker")
    ctx = make_ctx(db)
    res = asyncio.run(run_jobs(ctx, ["refresh_us_listed_symbol_universe"]))[0]
    assert res.status == "PARTIAL" and any("delistings not applied" in w for w in res.warnings)
    assert all(
        r["is_active"] for r in select_all(ctx.db, "security_master") if r["ticker"].startswith("T")
    )


def test_refresh_never_overwrites_manual_choices():
    ctx = make_ctx()
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    manual_override(ctx.db, "MTEC", {"sector": "Industrials"}, "checked company filings", NOW)
    manual_override(
        ctx.db, "MNEW", {"security_type": "COMMON_STOCK"}, "confirmed on exchange site", NOW
    )
    ctx.now = NOW + timedelta(days=60)  # force reclassification
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    rows = {r["ticker"]: r for r in load_universe(ctx.db)}
    assert rows["MTEC"]["sector"] == "INDUSTRIALS" and rows["MTEC"]["sector_source"] == "MANUAL"
    assert (
        rows["MNEW"]["security_type"] == "COMMON_STOCK"
        and rows["MNEW"]["security_type_source"] == "MANUAL"
    )
    assert "MANUALLY_OVERRIDDEN" in rows["MTEC"]["flags"]


def test_batches_resume_across_runs():
    ctx = make_ctx()
    ctx.settings = Settings(**{**S.model_dump(), "universe_classify_batch": 3})
    asyncio.run(run_jobs(ctx, ["refresh_us_listed_symbol_universe"]))
    first = asyncio.run(run_jobs(ctx, ["classify_target_sectors"]))[0]
    second = asyncio.run(run_jobs(ctx, ["classify_target_sectors"]))[0]
    checked = [r for r in select_all(ctx.db, "security_master") if r.get("profile_checked_at")]
    assert first.rows_read == 3 and second.rows_read == 3 and len(checked) == 6


def test_optional_csv_adapter_marks_unverified(tmp_path):
    f = tmp_path / "map.csv"
    f.write_text("ticker,sector,industry\nMUNK,Utilities,Water\n")
    a = CsvSectorMappingAdapter(f)
    assert a.lookup("MUNK").source == "SECTOR_MAPPING_CSV"
    ctx = make_ctx()
    ctx.settings = Settings(
        **{**S.model_dump(), "sector_mapping_adapter_enabled": True, "sector_mapping_csv": str(f)}
    )
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    r = {x["ticker"]: x for x in load_universe(ctx.db)}["MUNK"]
    assert r["sector"] == "UTILITIES" and r["sector_data_status"] == "UNVERIFIED"
    assert not r["in_all_target_sector_listings"]  # unverified stays out unless explicitly allowed


def test_adapter_disabled_by_default():
    assert S.sector_mapping_adapter_enabled is False


# ---------- manual verification & overrides ----------
def test_manual_verify_and_reject_are_audited_and_change_pair_view():
    ctx = make_ctx()
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    out = manual_verify(ctx.db, "MHLT", "MANUALLY_VERIFIED", "visible in Trader View", NOW)
    assert "does not contact" in out["note"]
    with pytest.raises(InvalidChange):
        manual_verify(ctx.db, "MIND", "MANUALLY_REJECTED", None, NOW)
    manual_verify(ctx.db, "MIND", "MANUALLY_REJECTED", "not offered in the competition", NOW)
    rows = {r["ticker"]: r for r in load_universe(ctx.db)}
    assert (
        "MANUALLY_VERIFIED" in rows["MHLT"]["flags"] and rows["MHLT"]["in_pair_research_eligible"]
    )
    assert (
        not rows["MIND"]["in_pair_research_eligible"]
        and "MANUALLY_REJECTED" in rows["MIND"]["exclusion_reasons"]
    )
    audit = ctx.db.data["security_master_audit"]
    assert [a["action"] for a in audit] == ["MANUAL_VERIFY", "MANUAL_VERIFY"]


def test_override_requires_reason_and_known_fields():
    ctx = make_ctx()
    asyncio.run(run_jobs(ctx, PROFILES["universe"]))
    with pytest.raises(InvalidChange):
        manual_override(ctx.db, "MTEC", {"sector": "Utilities"}, "", NOW)
    with pytest.raises(InvalidChange):
        manual_override(ctx.db, "MTEC", {"last_price": 5}, "because I said so", NOW)
    with pytest.raises(InvalidChange):
        manual_override(ctx.db, "MTEC", {"sector": "Astrology"}, "valid reason here", NOW)
    out = manual_override(ctx.db, "MTEC", {"sector": "Utilities"}, "company is a utility", NOW)
    assert out["views"]["in_all_target_sector_listings"]
    entry = ctx.db.data["security_master_audit"][-1]
    assert (
        entry["action"] == "MANUAL_OVERRIDE"
        and entry["field_changes"]["sector"]["new"] == "UTILITIES"
    )


def test_write_guard_blocks_other_tables():
    g = UniverseWriteGuard(InMemoryDB())
    with pytest.raises(UniverseWriteViolation):
        g.upsert("manual_trades", [{"id": "x"}], "id")
    g.upsert("security_master", [{"ticker": "OK"}], "ticker")


# ---------- export ----------
def test_export_has_disclaimer_flags_provenance_and_timestamp(built):
    _, _, rows = built
    now = datetime(2026, 10, 1, tzinfo=UTC)
    picked = filter_rows(list(rows.values()), "all")
    recs = export_records(picked, now)
    assert len(recs) == 11
    text = render_csv(recs)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0]) == EXPORT_COLUMNS
    assert all(
        p["disclaimer"] == DISCLAIMER and p["generated_at"] == now.isoformat() for p in parsed
    )
    assert all(
        p["listing_source"].startswith("nasdaqtrader:") and p["sector_source"] for p in parsed
    )
    assert any("ADR" in p["flags"] for p in parsed)
    body = json.loads(render_json(recs, now))
    assert body["disclaimer"] == DISCLAIMER and body["count"] == 11
    assert DISCLAIMER == (
        "This is a research universe. It does not confirm that WSR/Trader View permits trading "
        "every listed symbol. Verify availability manually before any trade."
    )


def test_export_script_writes_file_and_creates_directories(tmp_path):
    from scripts.export_target_sector_universe import main

    out = tmp_path / "nested" / "dir" / "u.csv"
    assert main(["--mock", "--format", "csv", "--output", str(out)]) == 0
    assert out.read_text().startswith("ticker,company_name")


# ---------- paging beyond PostgREST's 1000-row cap ----------
def test_select_all_pages_past_1000_rows():
    db = InMemoryDB({"security_master": [{"ticker": f"T{i:05d}"} for i in range(2500)]})
    assert len(select_all(db, "security_master")) == 2500


# ---------- API ----------
@pytest.fixture
def api(built):
    ctx, _, _ = built
    db = InMemoryDB({k: list(v) for k, v in ctx.db.data.items()})
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides[get_admin_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.clear()


def test_api_list_filter_summary_review_export(api):
    c, _ = api
    r = c.get("/universe/target-sectors", params={"sector": "UTILITIES"}).json()
    assert r["total"] == 1 and r["records"][0]["ticker"] == "MUTL" and r["disclaimer"] == DISCLAIMER
    assert c.get("/universe/target-sectors", params={"sector": "TECH"}).status_code == 422
    assert c.get("/universe/target-sectors", params={"view": "pair_research"}).json()["total"] == 8
    assert c.get("/universe/target-sectors", params={"adr": True}).json()["total"] == 1
    s = c.get("/universe/target-sectors/summary").json()
    assert s["by_sector"]["HEALTH_CARE"] == 4
    assert c.get("/universe/manual-review").json()["total"] == 4
    csv_resp = c.get("/universe/target-sectors/export", params={"format": "csv"})
    assert csv_resp.headers["content-type"].startswith("text/csv") and DISCLAIMER in csv_resp.text
    assert c.get("/universe/target-sectors/export", params={"format": "json"}).json()["count"] == 11


def test_api_manual_verify_and_override(api):
    c, _ = api
    ok = c.post(
        "/universe/MHLT/manual-verify", json={"status": "MANUALLY_VERIFIED", "note": "seen"}
    )
    assert ok.status_code == 200 and ok.json()["competition_tradable_status"] == "MANUALLY_VERIFIED"
    assert (
        c.post("/universe/NOPE/manual-verify", json={"status": "MANUALLY_VERIFIED"}).status_code
        == 404
    )
    assert (
        c.post("/universe/MIND/manual-verify", json={"status": "MANUALLY_REJECTED"}).status_code
        == 400
    )
    assert (
        c.post(
            "/universe/MTEC/manual-override", json={"reason": "ab", "sector": "Utilities"}
        ).status_code
        == 422
    )
    done = c.post(
        "/universe/MTEC/manual-override", json={"reason": "is a utility", "sector": "Utilities"}
    )
    assert done.status_code == 200 and "sector" in done.json()["changed"]
    assert (
        c.post(
            "/universe/MTEC/manual-override", json={"reason": "is a utility", "sector": "Bogus"}
        ).status_code
        == 400
    )


def test_api_requires_auth_and_has_no_order_routes():
    c = TestClient(app)
    assert c.get("/universe/target-sectors").status_code == 401
    paths = [p for p in app.openapi()["paths"] if p.startswith("/universe")]
    assert len(paths) == 7
    assert not any(w in p for p in paths for w in ("order", "trade", "execute", "broker"))


# ---------- SEC SIC classification ----------
@pytest.mark.parametrize(
    ("sic", "sector"),
    [(2834, "HEALTH_CARE"), (8062, "HEALTH_CARE"), (3841, "HEALTH_CARE"), (3560, "INDUSTRIALS"),
     (4213, "INDUSTRIALS"), (6022, "FINANCIALS"), (6211, "FINANCIALS"), (4911, "UTILITIES"),
     (4941, "UTILITIES"), (6798, "REAL_ESTATE"), (6512, "REAL_ESTATE"), (7372, "OTHER"),
     (1311, "OTHER"), (4700, "OTHER"), (4724, "OTHER"), (4731, "INDUSTRIALS"), (3674, "OTHER"), (None, None), ("", None)],
)  # fmt: skip
def test_sic_mapping(sic, sector):
    from app.services.universe.sic import sector_from_sic

    assert sector_from_sic(sic) == sector


def test_sec_sic_is_primary_source_and_flagged(built):
    _, _, rows = built
    assert rows["MHLT"]["sector_source"] == "SEC_SIC" and rows["MHLT"]["sector"] == "HEALTH_CARE"
    assert "SECTOR_FROM_SIC" in rows["MHLT"]["flags"] and rows["MREI"]["is_reit"]
    assert rows["MFIN"]["sector_source"] == "YAHOO_FINANCE"  # no SEC record in the mock
