"""Earnings module: calendar windows, collectors, probability model, Monte Carlo, scan, API, setup."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.earnings import calibration, model
from app.earnings.capitol import parse_capitol_date, parse_capitol_html, parse_size_mid
from app.earnings.extra import (
    _eps,
    already_reported,
    build_consensus,
    consensus_flag,
    filing_tilt,
    merge_headlines,
    short_interest_flag,
)
from app.earnings.mock import EarningsMockWorld
from app.earnings.models import (
    FilingEvent,
    Headline,
    HistoryStats,
    InsiderSummary,
    PoliticianTrade,
    ShortInterestInfo,
)
from app.earnings.montecarlo import seed_for, simulate
from app.earnings.packet import render_packet
from app.earnings.service import load_scan, render_table, run_scan, save_scan
from app.earnings.sources import (
    analyst_scores,
    history_stats,
    norm_name,
    normalize_politician_record,
    pair_surprises_with_filings,
    parse_amount_mid,
    parse_infotable,
    reaction_for,
    score_headline,
)
from app.earnings.weeks import week_windows, window_for
from app.services.providers.finnhub import Recommendation
from app.services.providers.registry import build_providers
from scripts import earnings_scan, setup_earnings_env, web_fetch


# ---------------------------------------------------------------- weeks
def test_weekday_this_week_is_current_monday_to_friday():
    this, nxt = week_windows(date(2026, 10, 7))  # Wednesday
    assert (this.start, this.end) == (date(2026, 10, 5), date(2026, 10, 9))
    assert (nxt.start, nxt.end) == (date(2026, 10, 12), date(2026, 10, 16))


def test_weekend_rolls_to_the_coming_week():
    for d in (date(2026, 10, 3), date(2026, 10, 4)):  # Saturday, Sunday
        this, nxt = week_windows(d)
        assert this.start == date(2026, 10, 5) and nxt.start == date(2026, 10, 12)


def test_window_for_labels_and_excludes_weekends_and_far_dates():
    today = date(2026, 10, 7)
    assert window_for(date(2026, 10, 9), today) == "this"
    assert window_for(date(2026, 10, 12), today) == "next"
    assert window_for(date(2026, 10, 10), today) is None  # Saturday
    assert window_for(date(2026, 10, 19), today) is None


# ---------------------------------------------------------------- collectors
def test_headline_lexicon():
    assert score_headline("Acme beats estimates and raises outlook") == 1
    assert score_headline("Acme shares slump after downgrade") == -1
    assert score_headline("Acme announces new office") == 0
    assert score_headline("Acme beats but warns on margins") == 0  # one each cancels


def _bars(n=60):
    d0 = date(2026, 1, 5)
    dates, closes = [], []
    d = d0
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
            closes.append(100.0 + len(dates))
        d += timedelta(days=1)
    return dates, closes


def test_reaction_timing_rules():
    dates, closes = _bars()
    e = dates[20]
    assert reaction_for(e, "bmo", dates, closes) == pytest.approx(closes[20] / closes[19] - 1)
    assert reaction_for(e, "amc", dates, closes) == pytest.approx(closes[21] / closes[20] - 1)
    assert reaction_for(e, "", dates, closes) == pytest.approx(closes[21] / closes[19] - 1)
    assert reaction_for(date(2026, 1, 3), "bmo", dates, closes) is None  # not a trading day
    assert reaction_for(dates[-1], "amc", dates, closes) is None  # reaction not in the data yet
    assert reaction_for(dates[0], "bmo", dates, closes) is None  # no prior close


def test_history_stats_beat_rate_and_move_asymmetry():
    dates, closes = _bars(200)
    events = [
        {
            "date": dates[150 - 20 * i],
            "hour": "amc",
            "eps_actual": 1.1 if i % 2 == 0 else 0.9,
            "eps_estimate": 1.0,
        }
        for i in range(6)
    ]
    h = history_stats(events, dates, closes)
    assert h.events_used == 6 and h.beat_rate == 0.5
    assert h.reactions == 6 and h.p_up_hist == 1.0  # synthetic series only rises
    assert 0.6 <= (h.up_mag or 1) <= 1.6


def test_analyst_scores_level_and_trend():
    recs = [
        Recommendation(
            ticker="X", period=date(2026, 9, 1), strong_buy=8, buy=10, hold=5, sell=0, strong_sell=0
        ),
        Recommendation(
            ticker="X", period=date(2026, 7, 1), strong_buy=2, buy=10, hold=9, sell=2, strong_sell=0
        ),
    ]
    level, trend, detail = analyst_scores(recs)
    assert level and level > 0.4 and trend and trend > 0 and "8 strong buy" in detail
    assert analyst_scores([])[0] is None
    thin = [Recommendation(ticker="X", period=date(2026, 9, 1), buy=1, hold=1)]
    assert analyst_scores(thin)[0] is None


def test_amounts_and_politician_records():
    assert parse_amount_mid("$1,001 - $15,000") == pytest.approx(8000.5)
    assert parse_amount_mid(5000) == 5000.0
    assert parse_amount_mid("n/a") is None
    house = {"ticker": "ko", "representative": "Rep A", "type": "purchase", "amount": "$15,001 - $50,000",
             "transaction_date": "2026-09-01", "disclosure_date": "09/10/2026"}  # fmt: skip
    t, trade = normalize_politician_record(house)
    assert t == "KO" and trade.side == "buy" and trade.chamber == "house"
    assert trade.disclosure_date == date(2026, 9, 10)
    sen = {"ticker": "BRK.B", "senator": "Sen B", "type": "sale_partial"}
    t, trade = normalize_politician_record(sen)
    assert t == "BRK-B" and trade.side == "sell" and trade.chamber == "senate"
    fh = {
        "symbol": "AAPL",
        "name": "Rep C",
        "transactionType": "Purchase",
        "amountFrom": 1001,
        "amountTo": 15000,
    }
    assert normalize_politician_record(fh)[1].amount_mid_usd == pytest.approx(8000.5)
    assert normalize_politician_record({"ticker": "--", "type": "purchase"}) is None
    assert normalize_politician_record({"ticker": "KO", "type": "exchange"}) is None


def test_13f_name_matching_and_infotable_parsing():
    assert norm_name("Apple Inc.") == norm_name("APPLE INC") == "APPLE"
    assert norm_name("Johnson & Johnson") == "JOHNSON AND JOHNSON"
    xml = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
      <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
      <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><shrsOrPrnAmt><sshPrnamt>50</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
      <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><shrsOrPrnAmt><sshPrnamt>999</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Put</putCall></infoTable>
      <infoTable><nameOfIssuer>SOME BOND CO</nameOfIssuer><shrsOrPrnAmt><sshPrnamt>7</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt></infoTable>
    </informationTable>"""
    assert parse_infotable(xml) == {"APPLE": 150.0}
    assert parse_infotable("<broken") == {}


# ---------------------------------------------------------------- model
def _ev(**kw):
    return model.Evidence(**kw)


def test_no_evidence_returns_the_prior():
    sigs = model.build_signals(_ev())
    p, conf, lo = model.score(sigs, HistoryStats(), 0.52)
    assert p == pytest.approx(0.52, abs=1e-3) and conf == 0.0
    assert not any(s.available for s in sigs)


def test_typical_beat_rate_is_not_a_signal():
    """About 74% of reports beat, so a 75% beater should not look bullish."""
    h = HistoryStats(events_used=8, beat_rate=0.75, reactions=0)
    s = {x.name: x for x in model.build_signals(_ev(history=h))}["beat_history"]
    assert abs(s.contribution) < 0.05
    bad = HistoryStats(events_used=8, beat_rate=0.25)
    assert {x.name: x for x in model.build_signals(_ev(history=bad))}[
        "beat_history"
    ].contribution < -0.2


def test_more_bullish_evidence_raises_p_up_monotonically():
    base = _ev(history=HistoryStats(events_used=8, beat_rate=0.75, reactions=8, p_up_hist=0.5))
    p0 = model.score(model.build_signals(base), base.history, 0.52)[0]
    better = _ev(
        history=base.history,
        analyst_level=0.9,
        analyst_trend=0.2,
        headlines_scores=[1, 1, 1, 1, 1, 0],
    )
    p1 = model.score(model.build_signals(better), better.history, 0.52)[0]
    best = _ev(history=base.history, analyst_level=0.9, analyst_trend=0.2, headlines_scores=[1] * 6,
               insiders=InsiderSummary(filings_seen=3, buyers=3, open_market_buys=3, buy_value_usd=2e6))  # fmt: skip
    p2 = model.score(model.build_signals(best), best.history, 0.52)[0]
    assert p0 < p1 < p2 <= model.P_MAX


def test_probability_is_always_inside_the_guardrails():
    wild = _ev(history=HistoryStats(events_used=8, beat_rate=1.0, reactions=12, p_up_hist=1.0), analyst_level=1.0,
               analyst_trend=1.0, headlines_scores=[1] * 10, excess_20d=0.3,
               insiders=InsiderSummary(buyers=9, open_market_buys=9, buy_value_usd=1e8),
               politicians=[PoliticianTrade(politician=f"P{i}", side="buy") for i in range(9)],
               peer_moves=[0.2, 0.2])  # fmt: skip
    p, conf, _ = model.score(model.build_signals(wild), wild.history, 0.52)
    assert p == model.P_MAX and 0 < conf <= 1
    bad = _ev(history=HistoryStats(events_used=8, beat_rate=0.0, reactions=12, p_up_hist=0.0), analyst_level=-1,
              headlines_scores=[-1] * 10)  # fmt: skip
    assert model.score(model.build_signals(bad), bad.history, 0.52)[0] == model.P_MIN


def test_insider_sales_matter_less_than_buys():
    buy = {
        s.name: s
        for s in model.build_signals(_ev(insiders=InsiderSummary(buyers=2, buy_value_usd=1e6)))
    }
    sell = {
        s.name: s
        for s in model.build_signals(_ev(insiders=InsiderSummary(sellers=2, sell_value_usd=1e9)))
    }
    assert buy["insider"].contribution > abs(sell["insider"].contribution)
    assert sell["insider"].contribution >= -0.3 * 0.40 - 1e-9


def test_p_beat_bounds_and_expected_move_priority():
    assert 0.40 <= model.p_beat(model.build_signals(_ev())) <= 0.95
    h = HistoryStats(reactions=8, mean_abs_move=0.05)
    assert model.choose_expected_move(h, 0.07, 0.02) == (0.06, "options implied + history")
    assert model.choose_expected_move(h, None, 0.02)[1].startswith("history")
    assert model.choose_expected_move(HistoryStats(), None, 0.02)[1].startswith("volatility")
    assert model.choose_expected_move(HistoryStats(), None, None) == (
        0.06,
        "default 6% (no price data)",
    )
    assert model.choose_expected_move(HistoryStats(), None, 0.5)[0] == 0.20  # capped


# ---------------------------------------------------------------- Monte Carlo
def test_mc_is_deterministic_and_seed_specific():
    a = simulate(0.6, 0.05, 0.8, 5000, 123)
    b = simulate(0.6, 0.05, 0.8, 5000, 123)
    assert a == b
    assert simulate(0.6, 0.05, 0.8, 5000, 124) != a
    assert seed_for("KO", date(2026, 10, 13)) != seed_for("PEP", date(2026, 10, 13))


def test_mc_matches_inputs():
    r = simulate(0.65, 0.05, 1.0, 40_000, 7)
    assert r.p_up == pytest.approx(0.65, abs=0.02)
    assert r.mean > 0 and r.p05 < r.median < r.p95
    assert r.cvar5 <= r.p05  # the mean of the worst 5% is below the 5th percentile
    assert 0.9 * 40_000 <= sum(r.hist_counts) <= 40_000
    assert len(r.hist_edges) == len(r.hist_counts) + 1
    # the mean absolute move is close to the requested expected move (before the +-cap)
    big = simulate(0.5, 0.05, 1.0, 80_000, 1)
    assert big.expected_move == 0.05 and abs(big.mean) < 0.003


def test_mc_less_confidence_widens_direction_uncertainty_and_flips_sign():
    assert simulate(0.4, 0.05, 1.0, 20_000, 3).mean < 0 < simulate(0.6, 0.05, 1.0, 20_000, 3).mean
    asym = simulate(0.5, 0.05, 1.0, 40_000, 3, up_ratio=1.5, down_ratio=0.7)
    assert asym.mean > 0.005  # upside moves are bigger than downside moves


def test_mc_tails_are_capped():
    r = simulate(0.5, 0.5, 1.0, 20_000, 5)
    assert r.p05 >= -0.60 and r.p95 <= 1.0


# ---------------------------------------------------------------- full scan (offline synthetic)
def _world(today=None):
    today = today or datetime.now(UTC).date()
    return EarningsMockWorld(today), today


async def _scan(today=None, **kw):
    world, today = _world(today)
    settings = earnings_scan._mock_settings()
    transport = kw.pop("transport", world.transport())
    providers = build_providers(settings, transport, sleep=lambda _x: asyncio.sleep(0))
    try:
        return await run_scan(settings, providers, today, mock=True, transport=transport, **kw)
    finally:
        await providers.aclose()


async def test_mock_scan_end_to_end():
    scan = await _scan()
    this, nxt = week_windows(scan.today)
    assert scan.analysed == len(scan.reports) > 10
    assert scan.calendar_counts["this"] + scan.calendar_counts["next"] >= scan.analysed - 2
    for r in scan.reports:
        assert this.contains(r.report_date) or nxt.contains(r.report_date)
        assert r.week == window_for(r.report_date, scan.today)
        assert model.P_MIN <= r.p_up <= model.P_MAX
        assert r.p_up + r.p_down == pytest.approx(1.0, abs=1e-3)
        assert 0 <= r.confidence <= 1 and r.mc and r.mc.n_paths == 10_000
        assert r.mc.p_up == pytest.approx(r.p_up, abs=0.06)
        assert r.suggestions is not None
        # a long candidate must clear every gate
        if r.long_candidate:
            assert (
                r.p_up >= 0.58
                and r.mc.mean > 0
                and r.confidence >= 0.45
                and (r.adv_usd or 0) >= 5e6
            )
        assert not (r.long_candidate and r.short_candidate)
    assert any(r.long_candidate for r in scan.reports)
    ranks = [r.rank_score for r in scan.reports]
    assert ranks == sorted(ranks, reverse=True)
    names = {s.name for s in scan.sources}
    assert {
        "finnhub",
        "sec_edgar",
        "yahoo",
        "google_news",
        "politicians",
        "13f",
        "options",
    } <= names
    assert all(s.failed == 0 for s in scan.sources if s.name != "alpha_vantage"), scan.sources
    assert scan.global_suggestions and "NOT been backtested" in scan.disclaimer


async def test_mock_scan_uses_every_signal_family():
    scan = await _scan()
    seen = {s.name for r in scan.reports for s in r.signals if s.available}
    assert {"beat_history", "reaction_history", "analyst_level", "analyst_trend", "news", "insider",
            "politicians", "institutions", "momentum", "peers", "options_skew", "filings"} <= seen  # fmt: skip
    ko = next(r for r in scan.reports if r.ticker == "KO")
    assert ko.insiders.open_market_buys >= 2 and ko.politicians and ko.institutions and ko.headlines


async def test_scan_is_reproducible():
    a, b = await _scan(), await _scan()
    strip = lambda s: [  # noqa: E731
        (r.ticker, r.p_up, r.confidence, r.mc.mean, r.mc.p05, r.long_candidate) for r in s.reports
    ]
    assert strip(a) == strip(b)


async def test_ticker_filter_week_filter_and_skips():
    scan = await _scan(tickers=["ko", "pep", "NOPE"])
    assert {r.ticker for r in scan.reports} == {"KO", "PEP"}
    assert any("NOPE" in s for s in scan.skipped)
    body = render_table(scan, "this").split("\n")[3:]
    shown = {line.split()[0] for line in body if line.strip() and line.split()[0] in {"KO", "PEP"}}
    assert shown == {r.ticker for r in scan.reports if r.week == "this"}
    assert "Research only" in render_table(scan)


async def test_max_tickers_cap_is_reported():
    scan = await _scan(max_tickers=5)
    assert scan.analysed == 5 and any("skipped" in s for s in scan.skipped)


async def test_without_any_keys_the_scan_degrades_instead_of_crashing():
    from app.config import Settings

    settings = Settings(
        sec_user_agent="", finnhub_api_key="", alpha_vantage_api_key="", provider_cache_dir=""
    )
    world, today = _world()
    providers = build_providers(settings, world.transport(), sleep=lambda _x: asyncio.sleep(0))
    try:
        scan = await run_scan(settings, providers, today)
    finally:
        await providers.aclose()
    assert scan.reports == [] and any("FINNHUB_API_KEY" in w for w in scan.warnings)
    assert any(s.name == "finnhub" and not s.configured for s in scan.sources)


async def test_one_failing_source_does_not_sink_the_scan():
    world, _ = _world()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "news.google.com" or req.url.path.endswith("/company-news"):
            return httpx.Response(500, text="boom")
        return world.handle(req)

    scan = await _scan(transport=httpx.MockTransport(handler))
    assert scan.reports
    news = next(s for s in scan.sources if s.name == "google_news")
    assert news.failed > 0 and news.ok == 0 and news.note
    assert all(not any(x.name == "news" and x.available for x in r.signals) for r in scan.reports)
    assert any("google_news" in w for w in scan.warnings)


async def test_politician_feed_failure_is_reported_not_raised():
    world, _ = _world()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "mock-politicians.example":
            return httpx.Response(404, text="gone")
        return world.handle(req)

    scan = await _scan(transport=httpx.MockTransport(handler))
    assert scan.reports and all(
        not any(x.name == "politicians" and x.available for x in r.signals) for r in scan.reports
    )
    assert next(s for s in scan.sources if s.name == "politicians").failed >= 1


def test_snapshot_roundtrip(tmp_path):
    scan = asyncio.run(_scan(tickers=["KO"]))
    path = save_scan(scan, tmp_path / "x" / "latest.json")
    again = load_scan(path)
    assert again and again.reports[0].ticker == "KO"
    assert again.reports[0].mc.hist_counts == scan.reports[0].mc.hist_counts
    assert load_scan(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert load_scan(tmp_path / "bad.json") is None


# ---------------------------------------------------------------- calibration
def test_calibration_logging_and_scoring(tmp_path):
    scan = asyncio.run(_scan(tickers=["KO", "PEP"]))
    log = tmp_path / "p.jsonl"
    assert calibration.log_predictions(scan, log) == 2
    rows = calibration.read_log(log)
    assert len(rows) == 2 and all(r["mock"] for r in rows)
    assert calibration.latest_per_report(rows) == []  # mock predictions are never scored
    real = [
        {
            "logged_at": "2026-09-01T00:00:00",
            "report_date": "2026-09-10",
            "ticker": "A",
            "p_up": 0.6,
            "mock": False,
        },
        {
            "logged_at": "2026-09-05T00:00:00",
            "report_date": "2026-09-10",
            "ticker": "A",
            "p_up": 0.7,
            "mock": False,
        },
        {
            "logged_at": "2026-09-11T00:00:00",
            "report_date": "2026-09-10",
            "ticker": "B",
            "p_up": 0.7,
            "mock": False,
        },
    ]
    kept = calibration.latest_per_report(real)
    assert [r["p_up"] for r in kept] == [0.7] and kept[0][
        "ticker"
    ] == "A"  # newest, made before the report


def test_evaluate_brier_and_bins():
    good = [{"p_up": 0.7, "outcome_up": 1}] * 7 + [{"p_up": 0.7, "outcome_up": 0}] * 3
    r = calibration.evaluate(good)
    assert r["n"] == 10 and r["hit_rate"] == 0.7
    assert r["brier"] == pytest.approx(0.21, abs=1e-3)
    assert r["bins"][0]["actual_up"] == 0.7 and "noise" in r["caution"]
    assert calibration.evaluate([]) == {"n": 0}


# ---------------------------------------------------------------- API
def test_api_serves_saved_scan(tmp_path, monkeypatch):
    scan = asyncio.run(_scan())
    monkeypatch.setattr("app.api.earnings.load_scan", lambda: scan)
    c = TestClient(app)
    body = c.get("/earnings/week").json()
    assert len(body["reports"]) == scan.analysed
    nxt = c.get("/earnings/week", params={"week": "next"}).json()["reports"]
    assert nxt and all(r["week"] == "next" for r in nxt)
    longs = c.get("/earnings/week", params={"long_only": True}).json()["reports"]
    assert longs and all(r["long_candidate"] for r in longs)
    assert c.get("/earnings/ko").json()["ticker"] == "KO"
    assert c.get("/earnings/ZZZZ").status_code == 404
    assert c.get("/earnings/week", params={"week": "bogus"}).status_code == 422


def test_api_404_when_no_scan_saved(monkeypatch):
    monkeypatch.setattr("app.api.earnings.load_scan", lambda: None)
    r = TestClient(app).get("/earnings/week")
    assert r.status_code == 404 and "earnings_scan" in r.json()["detail"]


def test_earnings_routes_are_read_only():
    paths = {p: v for p, v in app.openapi()["paths"].items() if p.startswith("/earnings")}
    assert paths and all(set(v) == {"get"} for v in paths.values())


# ---------------------------------------------------------------- CLI
def test_cli_mock_prints_table_and_writes_no_files_with_no_save(capsys, tmp_path):
    out = tmp_path / "week.json"
    assert (
        earnings_scan.main(["--mock", "--no-save", "--format", "json", "--output", str(out)]) == 0
    )
    data = json.loads(out.read_text())
    assert data["mock"] is True and data["reports"]
    assert earnings_scan.main(["--mock", "--no-save", "--week", "next", "--tickers", "KO"]) == 0
    printed = capsys.readouterr().out
    assert "KO" in printed and "Research only" in printed


# ---------------------------------------------------------------- setup script
def test_apply_env_preserves_and_never_overwrites_by_default():
    old = "A=1\nFINNHUB_API_KEY=\nSEC_USER_AGENT=Old Name old@example.com\n# note\n"
    new, changed = setup_earnings_env.apply_env(
        old,
        {
            "FINNHUB_API_KEY": "k123",
            "SEC_USER_AGENT": "New N new@example.com",
            "OPTIONS_IV_ENABLED": "true",
        },
    )
    assert "A=1" in new and "# note" in new
    assert "FINNHUB_API_KEY=k123" in new and "SEC_USER_AGENT=Old Name old@example.com" in new
    assert "OPTIONS_IV_ENABLED=true" in new and new.count(setup_earnings_env.HEADER) == 1
    assert set(changed) == {"FINNHUB_API_KEY", "OPTIONS_IV_ENABLED"}
    new2, changed2 = setup_earnings_env.apply_env(
        new, {"SEC_USER_AGENT": "New N new@example.com"}, overwrite=True
    )
    assert "SEC_USER_AGENT=New N new@example.com" in new2 and changed2 == ["SEC_USER_AGENT"]
    assert (
        setup_earnings_env.apply_env(new2, {"FINNHUB_API_KEY": ""})[1] == []
    )  # blank input is skipped


def test_setup_cli_writes_non_secret_values_and_hides_keys(tmp_path, capsys, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("KEEP=me\n")
    monkeypatch.setattr("getpass.getpass", lambda _p: "secret-key-xyz")
    rc = setup_earnings_env.main(
        ["--name", "Luke Brunson", "--email", "luke@example.com", "--env", str(env)]
    )
    shown = capsys.readouterr().out
    assert rc == 0 and "secret-key-xyz" not in shown
    text = env.read_text()
    assert "KEEP=me" in text and "SEC_USER_AGENT=Luke Brunson luke@example.com" in text
    assert "FINNHUB_API_KEY=secret-key-xyz" in text
    assert (
        setup_earnings_env.main(
            ["--name", "L", "--email", "nope", "--env", str(env), "--no-prompt"]
        )
        == 2
    )
    from app.services.providers.sec import valid_sec_user_agent

    assert valid_sec_user_agent("Luke Brunson luke@example.com")


# ---------------------------------------------------------------- safety
def test_earnings_package_contains_no_execution_language():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "earnings"
    bad = re.compile(
        r"\b(place|submit|cancel|modify|close|queue)_(order|trade|position)s?\b|broker|selenium|playwright",
        re.I,
    )
    for f in root.glob("*.py"):
        assert not bad.search(f.read_text()), f.name


def test_np_seed_independence_sanity():
    assert np.random.default_rng(1).random() != np.random.default_rng(2).random()


# ---- extra sources, research packet and web fetch bridge ----
def test_eps_parsing_and_consensus_disagreement():
    assert _eps("$1.25") == 1.25 and _eps("($0.40)") == -0.40 and _eps("N/A") is None
    c = build_consensus(1.00, {"eps": 1.25, "n": 7})
    assert c and c.disagreement_pct == pytest.approx(20.0) and consensus_flag(c)
    assert consensus_flag(build_consensus(1.0, {"eps": 1.02})) is None
    assert build_consensus(None, None) is None


def test_filing_tilt_only_warns_and_is_capped():
    assert filing_tilt([]) == 0.0
    bad = [FilingEvent(form="8-K", filed=date(2026, 9, 1), items=["4.02", "1.03"], note="")]
    assert filing_tilt(bad) == -1.0
    good = [FilingEvent(form="8-K", filed=date(2026, 9, 1), items=["8.01"], note="")]
    assert filing_tilt(good) <= 0.0


def test_short_interest_flag_threshold():
    assert short_interest_flag(
        ShortInterestInfo(settlement_date=date(2026, 9, 15), days_to_cover=8.0)
    )
    assert (
        short_interest_flag(ShortInterestInfo(settlement_date=date(2026, 9, 15), days_to_cover=2.0))
        is None
    )
    assert short_interest_flag(None) is None


def test_merge_headlines_dedupes_and_handles_mixed_timezones():
    a = Headline(title="Acme beats!", published_at=datetime(2026, 10, 1, 12, tzinfo=UTC))
    b = Headline(title="acme beats", published_at=datetime(2026, 10, 2, 12))
    c = Headline(title="Other news", published_at=datetime(2026, 10, 3, 12))
    out = merge_headlines([a], [b, c])
    assert [h.title for h in out] == ["Other news", "Acme beats!"]


async def test_scan_fills_new_fields_and_packet_renders():
    scan = await _scan()
    r = next(x for x in scan.reports if x.ticker == "KO")
    assert r.short_interest and r.consensus and r.filings
    md = render_packet(scan)
    assert "KO" in md and "Monte Carlo" in md and "NOT been backtested" in md
    assert {"finra", "nasdaq"} <= {s.name for s in scan.sources}


def test_web_fetch_saves_pages_and_respects_robots(tmp_path):
    page = "<html><body><p>" + "Earnings preview. " * 40 + "</p></body></html>"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        if req.url.path == "/thin":
            return httpx.Response(200, text="<html><body>hi</body></html>")
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    urls = ["https://x.example/ok", "https://x.example/private/a", "https://x.example/thin"]
    res = web_fetch.fetch_all(
        urls, tmp_path, "test-agent", delay=0, transport=httpx.MockTransport(handler)
    )
    assert res[0]["file"] and "Earnings preview" in (tmp_path / res[0]["file"]).read_text()
    assert "robots" in res[1]["note"] and res[1]["file"] is None
    assert "JavaScript" in res[2]["note"]
    assert (tmp_path / "index.json").exists()


def test_history_fallback_pairs_quarters_with_8k_dates():
    rows = [
        {"period": "2026-06-30", "actual": 1.1, "estimate": 1.0},
        {"period": "2026-03-31", "actual": 0.9, "estimate": 1.0},
        {"period": "2025-12-31", "actual": None, "estimate": 1.0},
    ]
    filings = [date(2026, 7, 22), date(2026, 4, 21), date(2026, 1, 20)]
    ev = pair_surprises_with_filings(rows, filings)
    assert [e["date"] for e in ev] == [date(2026, 7, 22), date(2026, 4, 21)]
    assert ev[0]["eps_actual"] == 1.1 and ev[1]["eps_estimate"] == 1.0
    assert pair_surprises_with_filings(rows, []) == []


def test_capitol_parsers():
    assert parse_capitol_date("21 Sept2026") == date(2026, 9, 21)
    assert parse_capitol_date("2 Oct 2026") == date(2026, 10, 2)
    assert parse_capitol_date("garbage") is None
    assert parse_size_mid("1K\u201315K") == 8000
    assert parse_size_mid("250K\u2013500K") == 375000
    assert parse_size_mid("25M\u201350M") == 37_500_000
    assert parse_size_mid("N/A") is None


async def test_scan_reads_capitol_trades_when_no_feed_url():
    world, today = _world()
    settings = earnings_scan._mock_settings().model_copy(update={"politician_trades_url": ""})
    transport = world.transport()
    providers = build_providers(settings, transport, sleep=lambda _x: asyncio.sleep(0))
    try:
        scan = await run_scan(settings, providers, today, mock=True, transport=transport)
    finally:
        await providers.aclose()
    ko = next(r for r in scan.reports if r.ticker == "KO")
    assert {t.side for t in ko.politicians} == {"buy", "sell"}
    assert any(t.chamber in ("house", "senate") and t.amount_mid_usd for t in ko.politicians)
    pol = next(s for s in scan.sources if s.name == "politicians")
    assert pol.configured and pol.failed == 0, pol


def test_capitol_html_skips_rows_without_ticker_or_side():
    html = (
        "<table><tbody><tr><td>x</td></tr></tbody></table>"
        "<tr><td><h2><a>A B</a></h2></td><td>no ticker</td><td/><td/><td/><td/><td>buy</td><td/><td/></tr>"
    )
    assert parse_capitol_html(html) == []


def _real_row(name, chamber, tk, pub, traded, owner, side, size):
    """Same cell structure as a live capitoltrades.com row (captured Oct 2026), classes trimmed."""
    tick = f'<span class="q-field issuer-ticker">{tk}</span>'
    date_td = lambda d, y: (  # noqa: E731
        f'<td><div><div class="text-size-3">{d}</div><div class="text-size-2">{y}</div></div></td>'
    )
    return (
        f'<tr class="border-b"><td><div class="q-cell cell--politician"><div>'
        f'<h2 class="politician-name"><a href="/politicians/X">{name}</a></h2>'
        f'<div><span class="q-field party party--republican">Republican</span>'
        f'<span class="q-field chamber chamber--{chamber}">{chamber.title()}</span></div></div></div></td>'
        f'<td><div class="q-cell cell--traded-issuer"><h3 class="q-fieldset issuer-name"><a>X Inc</a></h3>'
        f"{tick}</div></td>{date_td(*pub)}{date_td(*traded)}"
        f'<td><div class="q-cell cell--reporting-gap"><div class="q-label">days</div><div class="q-value">'
        f'<span>9</span></div></div></td><td><span class="q-field owner-with-icon"><div class="svg-image">'
        f'</div><span class="q-label">{owner}</span></span></td>'
        f'<td><div><span class="q-field tx-type tx-type--{side}">{side}</span></div></td>'
        f'<td><span class="q-field trade-size"><div><div><span class="range-icon"></span></div>'
        f'<span class="mt-1">{size}</span></div></span></td><td><span>$410.24</span></td>'
        f'<td><button><a href="/trades/1"><span class="sr-only">Goto trade detail page.</span></a></button></td></tr>'
    )


def test_capitol_parser_on_live_shaped_rows():
    html = "<table><tbody>" + "".join(
        [
            _real_row("David Taylor", "house", "AMGN:US", ("2 Oct", "2026"), ("21 Sept", "2026"),
                      "Undisclosed", "buy", "1K–15K"),
            _real_row("Sheldon Whitehouse", "senate", "V:US", ("1 Oct", "2026"), ("3 Sept", "2026"),
                      "Self", "sell", "15K–50K"),
            _real_row("Don Beyer", "house", "N/A", ("2 Oct", "2026"), ("22 Sept", "2026"),
                      "Joint", "buy", "15K–50K"),
        ]
    ) + "</tbody></table>"  # fmt: skip
    rows = parse_capitol_html(html)
    assert [r["ticker"] for r in rows] == ["AMGN", "V"]  # the N/A (muni bond) row is dropped
    a, v = rows
    assert (a["politician"], a["chamber"], a["side"], a["amount_mid"]) == (
        "David Taylor",
        "house",
        "buy",
        8000,
    )
    assert a["published"] == date(2026, 10, 2) and a["traded"] == date(2026, 9, 21)
    assert (v["chamber"], v["side"], v["amount_mid"]) == ("senate", "sell", 32500)


async def test_options_snapshot_gets_crumb_and_straddle_move():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.host == "fc.yahoo.com":
            return httpx.Response(404, headers={"set-cookie": "A3=x; Path=/"})
        if req.url.path.endswith("/getcrumb"):
            return httpx.Response(200, text="abc123crumb")
        exp1, exp2 = 1_790_000_000, 1_790_600_000  # first one is BEFORE the report date
        want = int(req.url.params.get("date", exp1))
        quote = {"regularMarketPrice": 100.0}
        chain = {
            "expirationDate": want,
            "calls": [
                {"strike": 100, "bid": 3.0, "ask": 3.4, "impliedVolatility": 0.5, "volume": 10}
            ],
            "puts": [
                {"strike": 100, "bid": 2.8, "ask": 3.2, "impliedVolatility": 0.5, "volume": 20}
            ],
        }
        body = {"optionChain": {"result": [{"quote": quote, "expirationDates": [exp1, exp2],
                                            "options": [chain]}]}}  # fmt: skip
        return httpx.Response(200, json=body)

    settings = earnings_scan._mock_settings()
    providers = build_providers(
        settings, httpx.MockTransport(handler), sleep=lambda _x: asyncio.sleep(0)
    )
    try:
        after = datetime.fromtimestamp(1_790_100_000, UTC).date()
        snap = await providers.yahoo.options_snapshot("ZZZ", after)
    finally:
        await providers.aclose()
    assert snap and snap["straddle_move"] == pytest.approx(0.85 * 6.2 / 100, abs=1e-4)
    assert any("crumb=abc123crumb" in u for u in calls)
    assert any("date=1790600000" in u for u in calls)  # moved to the post-report expiry


def test_already_reported_detection():
    e = FilingEvent(form="8-K", filed=date(2026, 10, 1), items=["2.02", "9.01"], note="")
    assert already_reported([e], date(2026, 10, 5), date(2026, 10, 4)) == e
    assert already_reported([e], date(2026, 10, 1), date(2026, 10, 4)) is None  # reports today
    old = FilingEvent(form="8-K", filed=date(2026, 7, 1), items=["2.02"], note="")
    assert already_reported([old], date(2026, 10, 5), date(2026, 10, 4)) is None
    other = FilingEvent(form="8-K", filed=date(2026, 10, 1), items=["5.02"], note="")
    assert already_reported([other], date(2026, 10, 5), date(2026, 10, 4)) is None
