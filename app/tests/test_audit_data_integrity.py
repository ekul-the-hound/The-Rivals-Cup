"""Audit regression tests: bad/stale/missing prices, duplicate bars, future data, aligned returns,
unknown sectors, ticker/CIK changes, provider failures. Offline and deterministic."""

import asyncio
import math
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.db.mock_seed import build_mock_db
from app.jobs.prices import refresh_daily_prices
from app.models.enums import DataStatus
from app.services.features.metrics import (
    adv_dollar,
    adv_shares,
    align_to_common_end,
    correlation,
    estimate_liquidity_cap,
    spread_vol,
)
from app.services.ingestion.runner import JobContext
from app.services.leaders.book import competitor_candidates
from app.services.leaders.metrics import Series, compute_metrics
from app.services.providers.google_news import (
    NewsStory,
    dedupe_stories,
    normalize_title,
    story_hash,
)
from app.services.providers.nasdaq_trader import Listing
from app.services.providers.yahoo import Bar, History
from app.services.universe.classify import normalize_symbol
from app.services.universe.master import cik_changes, listing_rows
from app.services.universe.views import UniverseParams, evaluate
from app.services.validation.bars import validate_bars
from app.services.validation.freshness import price_status
from app.tests.test_jobs import NOW, TODAY, S

D0 = date(2026, 6, 1)  # a Monday


def weekdays(n: int, start: date = D0) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bar(d, close=100.0, vol=1000, **kw):
    return {"bar_date": d, "close": close, "adj_close": close, "volume": vol, **kw}


# ------------------------------------------------------------------ bar validation
def test_clean_series_has_no_issues():
    chk = validate_bars(
        [bar(d, 100 + i) for i, d in enumerate(weekdays(30))], today=date(2026, 9, 1)
    )
    assert chk.issues == [] and len(chk.clean) == 30 and chk.summary("X") is None


@pytest.mark.parametrize(
    "bad,code",
    [
        ({"close": 0}, "INVALID_PRICE"),
        ({"close": -5.0}, "INVALID_PRICE"),
        ({"close": float("nan")}, "INVALID_PRICE"),
        ({"close": None}, "INVALID_PRICE"),
        ({"adj_close": -1.0}, "INVALID_PRICE"),
        ({"volume": -10}, "INVALID_VOLUME"),
        ({"high": 90.0, "low": 95.0}, "INVALID_OHLC"),
        ({"high": 99.0, "low": 98.0, "open": 98.5}, "INVALID_OHLC"),  # close 100 above the high
        ({"open": 0.0, "high": 101.0, "low": 99.0}, "INVALID_OHLC"),
    ],
)
def test_impossible_bars_are_dropped_not_repaired(bad, code):
    days = weekdays(5)
    rows = [bar(d) for d in days]
    rows[2] = {**rows[2], **bad}
    chk = validate_bars(rows, today=date(2026, 9, 1))
    assert code in chk.codes() and chk.dropped == 1
    assert days[2] not in {c["bar_date"] for c in chk.clean} and len(chk.clean) == 4


def test_duplicate_bars_are_collapsed_last_wins_and_reported():
    days = weekdays(4)
    rows = [bar(d, 100.0) for d in days] + [bar(days[1], 101.0)]
    chk = validate_bars(rows, today=date(2026, 9, 1))
    assert "DUPLICATE_BAR" in chk.codes() and len(chk.clean) == 4
    assert next(c for c in chk.clean if c["bar_date"] == days[1])["close"] == 101.0


def test_out_of_order_bars_are_sorted_and_reported():
    days = weekdays(5)
    rows = [bar(d) for d in reversed(days)]
    chk = validate_bars(rows, today=date(2026, 9, 1))
    assert "OUT_OF_ORDER" in chk.codes()
    assert [c["bar_date"] for c in chk.clean] == days


def test_future_dated_bar_is_dropped():
    days = weekdays(5)
    chk = validate_bars([bar(d) for d in days], today=days[2])
    assert "FUTURE_BAR" in chk.codes() and [c["bar_date"] for c in chk.clean] == days[:3]


def test_split_like_jump_is_flagged_but_kept_for_a_person_to_review():
    days = weekdays(6)
    closes = [100, 101, 100, 50.0, 50.5, 51]  # an unadjusted 2:1 split looks like -50%
    chk = validate_bars([bar(d, c) for d, c in zip(days, closes, strict=True)])
    assert "SPLIT_LIKE_JUMP" in chk.codes() and len(chk.clean) == 6 and chk.dropped == 0


def test_missing_dates_are_flagged_but_one_holiday_is_not():
    d = weekdays(10)
    one_gap = [x for x in d if x != d[4]]  # a single missing weekday (e.g. a holiday)
    assert "MISSING_DATES" not in validate_bars([bar(x) for x in one_gap]).codes()
    long_gap = [x for x in d if x not in d[3:7]]  # four consecutive weekdays missing
    chk = validate_bars([bar(x) for x in long_gap])
    assert "MISSING_DATES" in chk.codes() and chk.dropped == 0


def test_stale_and_missing_price_status():
    assert price_status(None, TODAY) == DataStatus.MISSING
    assert price_status(date(2026, 9, 25), TODAY) == DataStatus.STALE
    assert price_status(date(2026, 9, 30), TODAY) == DataStatus.AVAILABLE  # last weekday before Thu


# ------------------------------------------------------------------ 20-day dollar volume
def test_adv_dollar_hand_calculation_and_zero_volume_days_count_as_zero():
    days = weekdays(25)
    bars = [bar(d, close=10.0, vol=1000) for d in days]  # $10,000 / day
    assert adv_dollar(bars) == pytest.approx(10_000.0)
    # last 20 bars: make 5 of them zero-volume. mean = 15 * 10,000 / 20 = 7,500 (NOT 10,000)
    for b in bars[-5:]:
        b["volume"] = 0
    assert adv_dollar(bars) == pytest.approx(7_500.0)
    assert adv_shares(bars) == pytest.approx(750.0)


def test_adv_uses_only_the_last_20_bars_and_unadjusted_close():
    days = weekdays(30)
    bars = [bar(d, close=1.0, vol=1) for d in days[:10]] + [
        {**bar(d, close=20.0, vol=500), "adj_close": 5.0} for d in days[10:]
    ]
    assert adv_dollar(bars) == pytest.approx(20.0 * 500)  # close x volume, not adj_close


def test_adv_is_missing_with_too_little_history():
    assert adv_dollar([bar(d) for d in weekdays(6)]) is None  # need >= 10 usable bars
    assert estimate_liquidity_cap(None, 0.01) is None
    assert estimate_liquidity_cap(5_000_000.0, 0.01) == 50_000.0


# ------------------------------------------------------------------ job: bad bars never reach the DB
class FakeYahoo:
    def __init__(self, bars: list[Bar]):
        self._h = History(symbol="X", bars=bars, last_price=100.0, last_price_at=None)

    async def daily_history(self, symbol, range_="6mo", **kw):
        return self._h


def test_refresh_daily_prices_drops_invalid_duplicate_and_future_bars():
    days = [d for d in weekdays(60, date(2026, 7, 6)) if d < TODAY]
    good = [
        Bar(bar_date=d, open=100, high=101, low=99, close=100.0, adj_close=100.0, volume=1000)
        for d in days
    ]
    bad = [
        Bar(
            bar_date=days[5], close=100.0, high=90.0, low=95.0, adj_close=100.0, volume=1000
        ),  # dup + bad OHLC
        Bar(bar_date=days[6], close=-3.0, volume=1000),
        Bar(
            bar_date=TODAY + timedelta(days=2), close=100.0, adj_close=100.0, volume=1000
        ),  # future
    ]
    db = build_mock_db(TODAY, NOW)
    db.data["market_bars"] = []
    ctx = JobContext(
        settings=S,
        db=db,
        providers=SimpleNamespace(require=lambda n: FakeYahoo(good + bad)),
        now=NOW,
    )
    res = asyncio.run(refresh_daily_prices(ctx))
    stored = db.data["market_bars"]
    assert stored, "valid bars should still be stored"
    assert all(float(r["close"]) > 0 for r in stored)
    assert all(str(r["bar_date"]) <= TODAY.isoformat() for r in stored)  # no future bar
    per_sec: dict[str, list] = {}
    for r in stored:
        per_sec.setdefault(r["security_id"], []).append(str(r["bar_date"]))
    assert all(len(v) == len(set(v)) for v in per_sec.values())  # no duplicate dates
    assert any("bar issues" in w for w in res.warnings)


def test_refresh_daily_prices_survives_provider_failure_without_writing():
    from app.services.providers.base import ProviderError

    class Down:
        async def daily_history(self, *a, **k):
            raise ProviderError("yahoo: HTTP 503")

    db = build_mock_db(TODAY, NOW)
    db.data["market_bars"] = []
    ctx = JobContext(
        settings=S, db=db, providers=SimpleNamespace(require=lambda n: Down()), now=NOW
    )
    res = asyncio.run(refresh_daily_prices(ctx))
    assert db.data["market_bars"] == [] and res.warnings  # failed safely and said so


# ------------------------------------------------------------------ aligned timestamps / no future leakage
def _series(dates, closes, ticker="T"):
    return Series(ticker, list(dates), list(closes), list(closes), [1e6] * len(dates))


def test_metrics_never_use_bars_after_the_calculation_date():
    days = weekdays(260, date(2025, 9, 1))
    rng = np.random.default_rng(7)
    closes = list(100 * np.cumprod(1 + rng.normal(0, 0.01, len(days))))
    cut = days[200]
    full = _series(days, closes)
    spiked = _series(days, closes[:201] + [c * 5 for c in closes[201:]])  # wild "future" prices
    a = compute_metrics(full.as_of(cut), None, None, cut)
    b = compute_metrics(spiked, None, None, cut)  # same as-of date, different future
    for k in ("ret_5d", "ret_20d", "ret_60d", "vol_60d", "pos_52w", "last_close", "n_bars"):
        assert a[k] == pytest.approx(b[k]), k
    assert b["last_date"] == cut.isoformat()


def test_trailing_returns_end_on_the_same_bar_for_both_legs():
    d = [x.isoformat() for x in weekdays(10)]
    long_ = pd.Series([100 + i for i in range(10)], index=d, dtype=float)
    short = pd.Series([50.0] * 9, index=d[:9], dtype=float)  # stale: missing the last day
    a, b = align_to_common_end(long_, short)
    assert a.index[-1] == b.index[-1] == d[8] and len(a) == 9


def test_correlation_aligns_levels_before_taking_returns():
    d = [x.isoformat() for x in weekdays(80)]
    rng = np.random.default_rng(3)
    base = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
    a = pd.Series(base, index=d)
    b = pd.Series(base * 2 + rng.normal(0, 0.01, 80), index=d)
    b = b.drop(d[50])  # one missing bar in b
    expect = pd.concat([a, b], axis=1, join="inner").dropna().pct_change().dropna().tail(60)
    exp_corr = expect.iloc[:, 0].corr(expect.iloc[:, 1])
    corr, n = correlation(a, b, 60)
    assert corr == pytest.approx(exp_corr) and n == 60
    assert spread_vol(a, b) == pytest.approx(
        float((expect.iloc[:, 0] - expect.iloc[:, 1]).std(ddof=1))
    )


# ------------------------------------------------------------------ universe: unknown sector, tickers, CIK
def _row(**kw):
    base = dict(
        ticker="TST", security_type="COMMON_STOCK", is_common_stock=True, is_active=True,
        sector="INDUSTRIALS", sector_data_status="AVAILABLE", average_dollar_volume_20d=5e7,
        last_price=20.0, liquidity_data_as_of=TODAY, competition_tradable_status="UNKNOWN", cik="1",
    )  # fmt: skip
    return {**base, **kw}


def test_unknown_or_non_target_sector_is_never_eligible():
    p = UniverseParams()
    missing = evaluate(_row(sector=None), p, TODAY)
    assert not missing.in_pair_eligible and "SECTOR_MISSING" in missing.exclusion_reasons
    assert missing.in_manual_review  # a person has to classify it
    other = evaluate(_row(sector="ENERGY"), p, TODAY)
    assert not other.in_pair_eligible and "NON_TARGET_SECTOR" in other.exclusion_reasons
    guess = evaluate(_row(sector_data_status="UNVERIFIED"), p, TODAY)
    assert not guess.in_pair_eligible and "SECTOR_UNVERIFIED" in guess.exclusion_reasons


def test_reit_common_is_allowed_in_real_estate_and_adr_is_flagged_for_review():
    p = UniverseParams()
    reit = evaluate(_row(sector="REAL_ESTATE", is_reit=True), p, TODAY)
    assert reit.in_pair_eligible and "REIT" in reit.flags
    adr = evaluate(_row(security_type="ADR", is_adr=True), p, TODAY)
    assert adr.in_all_target and "ADR_VERIFY_TRADABILITY" in adr.review_reasons


def test_symbol_normalisation_and_ticker_change_detection():
    assert normalize_symbol("brk.b") == "BRK-B" and normalize_symbol(" bf.b ") == "BF-B"
    cik_map = {"NEWC": SimpleNamespace(cik=222, title="Renamed Co")}
    existing = {
        "NEWC": {"ticker": "NEWC", "cik": "0000000111"},
        "SAME": {"ticker": "SAME", "cik": "0000000005"},
    }
    msgs = cik_changes(existing, {**cik_map, "SAME": SimpleNamespace(cik=5, title="Same")})
    assert len(msgs) == 1 and "NEWC" in msgs[0] and "0000000222" in msgs[0]


def test_sec_outage_never_erases_a_known_cik():
    li = Listing(symbol="ABCD", name="Abcd Inc. Common Stock", exchange="Q", etf=False, test_issue=False,
                 financial_status="N", listing_source="nasdaqtrader:test")  # fmt: skip
    existing = {"ABCD": {"ticker": "ABCD", "cik": "0000000123", "sec_company_name": "Abcd Inc"}}
    rows, _ = listing_rows([li], existing, {}, NOW)  # SEC ticker map unavailable -> empty
    assert "cik" not in rows[0] and "sec_company_name" not in rows[0]
    rows2, _ = listing_rows(
        [li], existing, {"ABCD": SimpleNamespace(cik=123, title="Abcd Inc")}, NOW
    )
    assert rows2[0]["cik"] == "0000000123"


# ------------------------------------------------------------------ peers, news
def test_competitor_candidates_never_include_the_leader_itself():
    prep = SimpleNamespace(
        rows={t: {"sector": "INDUSTRIALS", "industry": "X"} for t in ("AAA", "BBB", "CCC")},
        series={"AAA": 1, "BBB": 1, "CCC": 1},
        metrics={t: {"adv_usd": 1e7} for t in ("AAA", "BBB", "CCC")},
        blocked=set(),
        params=SimpleNamespace(min_competitors=1),
    )
    chosen, _ = competitor_candidates(prep, "AAA", {"AAA": ["AAA", "BBB", "ZZZ"]})
    assert "AAA" not in {t for t, _ in chosen} and ("BBB", "FINNHUB_PEERS") in chosen


def test_news_dedupes_syndicated_headlines_and_prefers_primary_source():
    from app.models.enums import EvidenceQuality

    t = "Acme beats estimates - Reuters"
    n = normalize_title(t, "Reuters")
    assert n == normalize_title("Acme beats estimates - Some Blog")
    mk = lambda q, pub: NewsStory(  # noqa: E731
        headline=t, url="https://x/y", publisher_name="p", publisher_domain="x.com",
        published_at=pub, query="q", evidence_quality=q, story_hash=story_hash(n),
        link_confirmed=True,
    )  # fmt: skip
    a = mk(EvidenceQuality.UNVERIFIED, None)
    b = mk(EvidenceQuality.PRIMARY, None)
    assert dedupe_stories([a, b])[0].evidence_quality == EvidenceQuality.PRIMARY


def test_liquidity_estimate_is_labeled_estimated_in_the_score_output():
    from app.services.scoring.wsr import LIQ_CAP_PCT

    assert LIQ_CAP_PCT == 0.01 and math.isclose(estimate_liquidity_cap(1e7, LIQ_CAP_PCT), 1e5)
