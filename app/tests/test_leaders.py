"""Leaders & laggards: metrics, ranking, competitor selection, screens, sizing, jobs, API, CLI.
All offline on synthetic data."""

import asyncio
import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_store
from app.api.main import app
from app.config import Settings
from app.db.memory import InMemoryDB
from app.jobs import PROFILES, run_jobs
from app.services.leaders.book import (
    BookParams,
    build_book,
    competitor_candidates,
    prepare,
    render_markdown,
)
from app.services.leaders.metrics import (
    Series,
    compute_metrics,
    estimate_next_ex_dividend,
    pair_correlation,
    series_to_row,
)
from app.services.leaders.ranking import (
    long_penalties,
    percentile_ranks,
    short_penalties,
    strength_scores,
)
from app.tests.test_deep_dive import ctx_with

D = date(2026, 10, 1)  # a Thursday: the scoring week is Mon 2026-09-28 to Fri 2026-10-02


def bars(n=252, end=D):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def make_series(ticker, drift, n=252, wiggle=0.004, vol=3_000_000, base=50.0, divs=(), splits=()):
    days = bars(n)
    px, close = base, []
    for i in range(n):
        px *= 1 + drift + wiggle * math.sin(i * 0.9 + len(ticker)) / 4
        close.append(px)
    return Series(ticker, days, close, list(close), [vol] * n, list(divs), list(splits))


def hist_row(s: Series):
    row = series_to_row(
        s.ticker, [(d, c, a, v) for d, c, a, v in zip(s.dates, s.close, s.adj, s.volume, strict=True)],
        s.dividends, s.splits,
    )  # fmt: skip
    return row


def master(t, sector="HEALTH_CARE", industry="Pharma", adv=50e6, eligible=True, **kw):
    return {
        "ticker": t, "company_name": f"{t} Inc", "sector": sector, "industry": industry,
        "exchange": "NYSE", "security_type": "COMMON_STOCK", "is_active": True,
        "average_dollar_volume_20d": adv, "last_price": 50.0, "earnings_date_if_known": None,
        "competition_tradable_status": "UNKNOWN", **kw,
    }  # fmt: skip


def view(t, eligible=True, reasons=()):
    return {"ticker": t, "in_pair_research_eligible": eligible, "in_all_target_sector_listings": True,
            "in_manual_review": False, "exclusion_reasons": list(reasons), "review_reasons": [], "flags": []}  # fmt: skip


def scenario(extra=None, peers=None):
    """HEALTH_CARE: LEAD strong; weak competitors C1..C4 (C1 weakest but crowded short); BLK strongest
    but in earnings blackout; spy/xlv benchmarks."""
    db = InMemoryDB()
    spec = {
        "LEAD": 0.0030, "BLK": 0.0045, "C1": -0.0030, "C2": -0.0018, "C3": -0.0005, "C4": 0.0004,
        "OTH": 0.0010,
    }  # fmt: skip
    ser = {t: make_series(t, d) for t, d in spec.items()}
    ser["SPY"] = make_series("SPY", 0.0003, base=400)
    ser["XLV"] = make_series("XLV", 0.0004, base=140)
    for t in spec:
        db.data.setdefault("security_master", []).append(
            master(t, industry="Pharma" if t != "OTH" else "Other Industry")
        )
        db.data.setdefault("security_master_view_state", []).append(
            view(t, eligible=t != "BLK", reasons=["EVENT_BLACKOUT"] if t == "BLK" else [])
        )
    for s in ser.values():
        db.data.setdefault("universe_price_history", []).append(hist_row(s))
    for p in peers or ["C1", "C2", "C3", "C4", "BLK"]:
        db.data.setdefault("competitor_map", []).append(
            {"ticker": "LEAD", "peer": p, "source": "FINNHUB_PEERS"}
        )
    if extra:
        extra(db)
    return db


PARAMS = BookParams(
    week_start=date(2026, 9, 28), week_end=date(2026, 10, 2), min_competitors=3, min_adv_usd=1e6
)


# ---------- metrics & ranking ----------
def test_percentile_ranks_ties_and_missing():
    r = percentile_ranks({"a": 1, "b": 2, "c": 2, "d": None})
    assert r["d"] is None and r["a"] < r["b"] == r["c"]
    assert percentile_ranks({"x": 5})["x"] == 50.0


def test_metrics_returns_beta_and_downside():
    s = make_series("AAA", 0.002)
    m = compute_metrics(s, s, s, D)
    assert m["ret_20d"] == pytest.approx((s.adj[-1] / s.adj[-21] - 1) * 100)
    assert m["beta_60d"] == pytest.approx(1.0) and m["corr_market_60d"] == pytest.approx(1.0)
    assert m["rel_60d_vs_sector"] == pytest.approx(0.0)
    assert m["n_bars"] == 252 and m["stale_days"] == 0
    assert pair_correlation(s, s) == pytest.approx(1.0)


def test_ex_dividend_estimate_projects_cadence():
    d = [(date(2026, 1, 15), 0.5), (date(2026, 4, 16), 0.5), (date(2026, 7, 16), 0.5)]
    assert estimate_next_ex_dividend(d) == date(2026, 10, 15)
    assert estimate_next_ex_dividend(d[:1]) is None


def test_strength_orders_by_momentum_and_penalties_apply():
    ms = {
        t: compute_metrics(make_series(t, dr), None, None, D)
        for t, dr in (("UP", 0.003), ("MID", 0.0005), ("DOWN", -0.002))
    }
    st = strength_scores(ms)
    assert st["UP"]["strength"] > st["MID"]["strength"] > st["DOWN"]["strength"]
    pen, _, flags = short_penalties({"vol_60d": 120, "ret_5d": 20}, {"days_to_cover": 9}, (D, D))
    assert pen >= 15 + 10 + 10 and {"SQUEEZE_RISK", "EXTREME_VOLATILITY", "RECENT_BOUNCE"} <= set(
        flags
    )
    assert long_penalties({"ret_20d": 60, "vol_60d": 90})[0] == 18


def test_ex_div_in_week_penalizes_short():
    m = {"next_ex_dividend_est": "2026-09-30", "div_yield_pct": 4.0}
    pen, text, flags = short_penalties(m, {}, (date(2026, 9, 28), date(2026, 10, 2)))
    assert "EX_DIV_LIKELY_IN_WEEK" in flags and pen == pytest.approx(9.0)


# ---------- book ----------
def test_book_longs_strongest_and_shorts_weakest_competitor():
    book = build_book(scenario(), D, PARAMS)
    pair = next(p for p in book["pairs"] if p["sector"] == "HEALTH_CARE")
    assert pair["long"]["ticker"] == "LEAD"  # BLK is stronger but blocked by the earnings blackout
    assert pair["short"]["ticker"] == "C1"
    assert pair["competitor_source"] == "FINNHUB_PEERS"
    assert pair["blocked_by_event_blackout"][0]["ticker"] == "BLK"
    assert {s["sector"] for s in book["sectors_without_pair"]} == {
        "INDUSTRIALS", "FINANCIALS", "UTILITIES", "REAL_ESTATE",
    }  # fmt: skip
    assert len(book["pairs"]) == 1
    txt = render_markdown(book)
    assert "LONG candidate LEAD" in txt and "SHORT candidate C1" in txt


def test_crowded_short_is_penalized_below_next_weakest():
    def crowd(db):
        db.data["short_interest"] = [
            {
                "ticker": "C1",
                "settlement_date": "2026-09-15",
                "days_to_cover": 11.0,
                "change_percent": 5,
            },
        ]

    pair = build_book(scenario(crowd), D, PARAMS)["pairs"][0]
    assert pair["short"]["ticker"] == "C2"
    c1 = next(a for a in pair["alternates"]["shorts"] if a["ticker"] == "C1")
    assert c1["score"] < pair["short"]["score"]


def test_fallback_to_sec_industry_size_match_when_no_peer_map():
    book = build_book(scenario(peers=["ZZZ"]), D, PARAMS)
    pair = book["pairs"][0]
    assert pair["competitor_source"] == "SEC_INDUSTRY_SIZE_MATCH"
    assert pair["short"]["ticker"] == "C1"
    assert any("COMPETITOR_SOURCE" in w for w in pair["warnings"])


def test_other_sector_peers_are_not_used():
    db = scenario()
    db.data["security_master"].append(master("FOREIGN", sector="FINANCIALS"))
    db.data["security_master_view_state"].append(view("FOREIGN"))
    db.data["universe_price_history"].append(hist_row(make_series("FOREIGN", -0.01)))
    db.data["competitor_map"].append(
        {"ticker": "LEAD", "peer": "FOREIGN", "source": "FINNHUB_PEERS"}
    )
    pair = build_book(db, D, PARAMS)["pairs"][0]
    assert pair["short"]["ticker"] != "FOREIGN"
    assert any("outside HEALTH_CARE" in w for w in pair["warnings"])


def test_screens_drop_illiquid_penny_stale_and_split_names():
    def mutate(db):
        for t, kw in {"C1": {"adv": 1_000}, "C2": {}}.items():
            row = next(r for r in db.data["security_master"] if r["ticker"] == t)
            row["average_dollar_volume_20d"] = kw.get("adv", row["average_dollar_volume_20d"])
        c3 = next(h for h in db.data["universe_price_history"] if h["ticker"] == "C3")
        c3["splits"] = [[(D - timedelta(days=3)).isoformat(), "2:1"]]

    prep = prepare(scenario(mutate), D, PARAMS)
    assert "C1" not in prep.metrics and "C3" not in prep.metrics
    assert prep.screened_out["BELOW_MIN_ADV"] >= 1 and prep.screened_out["RECENT_SPLIT"] == 1
    names = [
        c for c, _ in competitor_candidates(prep, "LEAD", {"LEAD": ["C1", "C2", "C3", "C4"]})[0]
    ]
    assert [c for c in names if c in prep.metrics and c not in prep.blocked] == ["C2", "C4"]


def test_sizing_is_dollar_neutral_capped_and_within_gross_limit():
    book = build_book(scenario(), D, PARAMS)
    z = book["pairs"][0]["sizing_estimate"]
    assert z["leg_usd_dollar_neutral"] <= 0.15 * PARAMS.portfolio_usd
    bk = book["book"]
    assert bk["long_usd"] == bk["short_usd"] and bk["net_pct_of_portfolio"] == 0
    assert bk["gross_pct_of_portfolio"] <= 200
    thin = scenario(lambda db: db.data["security_master"][0].update(average_dollar_volume_20d=6e6))
    book = build_book(thin, D, PARAMS)
    assert book["pairs"][0]["sizing_estimate"]["liquidity_cap_binding"]


def test_book_has_no_execution_language_and_stays_research_only():
    txt = render_markdown(build_book(scenario(), D, PARAMS)).lower()
    assert "not a recommendation and not a trade instruction" in txt
    assert "trader view" in txt


# ---------- jobs, API, CLI ----------
@pytest.fixture(scope="module")
def pipeline():
    ctx = ctx_with(finnhub_api_key="k", alpha_vantage_api_key="k",
                   leaders_min_competitors=1, leaders_min_adv_usd=1.0, leaders_min_price=1.0)  # fmt: skip
    res = {}
    for prof in ("universe", "deepmarket", "leaders"):
        for r in asyncio.run(run_jobs(ctx, PROFILES[prof])):
            res[r.job] = r
    return ctx, res


def test_leaders_profile_runs_and_stores_a_research_only_book(pipeline):
    ctx, res = pipeline
    assert res["refresh_universe_price_history"].status == "SUCCEEDED"
    assert res["refresh_competitor_map"].status in ("SUCCEEDED", "PARTIAL")
    assert res["build_leader_laggard_book"].status in ("SUCCEEDED", "PARTIAL")
    hist = ctx.db.select("universe_price_history")
    assert {"SPY", "XLV", "XLRE"} <= {h["ticker"] for h in hist}
    assert all(h["n_bars"] >= 200 for h in hist)
    row = ctx.db.select("leader_laggard_books")[0]
    assert row["is_research_only"] is True and row["book"]["pairs"]
    assert any(h["dividends"] for h in hist)  # dividends ride along on the same Yahoo request


def test_history_job_is_resumable_and_skips_fresh_tickers(pipeline):
    ctx, _ = pipeline
    again = asyncio.run(run_jobs(ctx, ["refresh_universe_price_history"]))[0]
    assert again.rows_written == 0 and "0 histories downloaded" in again.message


def test_deep_dive_shortlist_includes_book_finalists(pipeline):
    ctx, _ = pipeline
    from app.jobs.deep_dive import finalist_tickers

    picks = {p["long"]["ticker"] for p in ctx.db.select("leader_laggard_books")[0]["book"]["pairs"]}
    assert picks <= set(finalist_tickers(ctx.db))


def test_api_returns_stored_book(pipeline):
    ctx, _ = pipeline
    app.dependency_overrides[get_store] = lambda: ctx.db
    try:
        c = TestClient(app)
        r = c.get("/leaders-laggards")
        assert r.status_code == 200 and r.json()["pairs"]
        md = c.get("/leaders-laggards/markdown")
        assert md.status_code == 200 and "LONG candidate" in md.text
    finally:
        app.dependency_overrides.clear()


def test_cli_mock_prints_the_book(capsys):
    from scripts.leaders_laggards import main

    assert main(["--mock"]) == 0
    out = capsys.readouterr().out
    assert "LONG candidate" in out and "SHORT candidate" in out


def test_settings_defaults_are_sane():
    s = Settings(sec_user_agent="A b a@b.com", fred_api_key="k", app_env="development")
    assert s.leaders_gross_target_pct <= 180 and s.leaders_max_leg_pct <= 25


def test_spread_stats_detects_a_steady_winning_pair():
    from datetime import date, timedelta

    from app.services.leaders.metrics import Series, spread_stats

    n = 200
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    up = [100 * (1.004**i) for i in range(n)]
    down = [100 * (0.998**i) for i in range(n)]
    a = Series("UP", days, up, up, [1e6] * n)
    b = Series("DN", days, down, down, [1e6] * n)
    st = spread_stats(a, b)
    assert st and st["wk_hit_rate"] == 1.0 and st["wk_score"] > 0 and st["max_dd_120d"] == 0
    flipped = spread_stats(b, a)
    assert flipped and flipped["wk_hit_rate"] == 0.0 and flipped["wk_score"] < 0
    assert spread_stats(a, Series("X", days[:30], up[:30], up[:30], [1.0] * 30)) is None
