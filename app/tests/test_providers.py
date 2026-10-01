import asyncio
from datetime import date

import httpx
import pytest

from app.config import Settings
from app.models.enums import EvidenceQuality
from app.services.providers.base import FileCache, HttpClient, ProviderError, RateLimiter
from app.services.providers.fred import FredProvider
from app.services.providers.google_news import classify_source, dedupe_stories, parse_feed
from app.services.providers.mock_transport import MockWorld
from app.services.providers.registry import build_providers
from app.services.providers.sec import SecProvider, parse_form4, valid_sec_user_agent

TODAY = date(2026, 10, 1)
UA = "Luke Test luke@example.com"


async def nosleep(_):
    return None


@pytest.fixture
def prov():
    s = Settings(sec_user_agent=UA, fred_api_key="k")
    return build_providers(s, MockWorld(TODAY).transport(), sleep=nosleep)


def test_sec_user_agent_validation():
    assert valid_sec_user_agent(UA)
    for bad in ["", "python-httpx", "nospace@example.com", "Name only"]:
        assert not valid_sec_user_agent(bad)
    with pytest.raises(Exception, match="SEC_USER_AGENT"):
        SecProvider("bad")


def test_registry_reports_unavailable_providers():
    p = build_providers(Settings(), MockWorld(TODAY).transport())
    assert p.sec is None and p.fred is None and "SEC_USER_AGENT" in p.unavailable["sec"]
    assert p.yahoo and p.news and p.wiki


async def test_sec_cik_filings_excerpt_form4(prov):
    m = await prov.sec.ticker_cik_map()
    assert m["KO"].cik == 21344
    f = (await prov.sec.recent_filings(21344, date(2026, 8, 1)))[0]
    assert f.form_type == "8-K" and f.items == ["1.01", "5.02"]
    assert f.url.endswith("/000002134426000001/d1.htm")
    assert (await prov.sec.filing_excerpt(f)).startswith("Item 1.01")
    x = (await prov.sec.recent_filings(34088, date(2026, 8, 1)))[0]
    s4 = await prov.sec.form4_summary(x)
    assert s4 and s4.transactions[0]["code"] == "S" and s4.net_value_usd < 0


def test_parse_form4_bad_xml():
    assert parse_form4("<not xml") is None


async def test_fred_whitelist_and_missing_series(prov):
    with pytest.raises(ValueError):
        await prov.fred.latest("GDP")
    obs, errors = await prov.fred.latest_all()
    assert set(obs) == {"DGS2", "DGS10", "T10Y2Y", "FEDFUNDS", "VIXCLS"} and not errors
    assert obs["DGS10"].value == 4.2  # skipped the '.' placeholder


async def test_fred_requires_key():
    with pytest.raises(ProviderError):
        FredProvider("")


async def test_yahoo_history_profile_and_error(prov):
    h = await prov.yahoo.daily_history("KO", "1mo")
    assert len(h.bars) == 22 and h.bars[-1].adj_close and h.last_price
    assert (await prov.yahoo.profile("KO")).market_cap == 2.5e11
    with pytest.raises(ProviderError):
        await prov.yahoo.daily_history("BADSYM")


async def test_wikipedia_and_missing(prov):
    w = await prov.wiki.describe("PepsiCo")
    assert w.industry == "Beverages, Snack food" and "}}" not in w.products[-1]
    assert await prov.wiki.describe("Missing Co") is None


async def test_news_dedupe_and_classification(prov):
    stories = await prov.news.search("Coca-Cola")
    assert len(stories) == 2  # syndicated duplicate collapsed, Reuters beats Yahoo
    by = {s.headline: s for s in stories}
    assert by["Coca-Cola names new finance chief"].evidence_quality == EvidenceQuality.SECONDARY
    assert by["Coca-Cola signs distribution deal"].evidence_quality == EvidenceQuality.PRIMARY
    assert all(s.link_confirmed for s in stories)


def test_classify_and_bad_feed():
    assert classify_source("www.randomblog.io") == EvidenceQuality.UNVERIFIED
    assert classify_source(None) == EvidenceQuality.UNVERIFIED
    assert parse_feed("<<<", "q") == []
    item = (
        "<rss><channel><item><title>X - Pub</title><link>https://n/1</link></item></channel></rss>"
    )
    (s,) = parse_feed(item, "q")
    assert s.published_at is None and not s.link_confirmed
    assert len(dedupe_stories([s, s])) == 1


async def test_retry_backoff_then_success_and_exhaustion():
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="ok")

    slept = []

    async def rec(d):
        slept.append(d)

    c = HttpClient(
        "t", user_agent="x", per_second=1000, transport=httpx.MockTransport(flaky), sleep=rec
    )
    assert await c.get_text("https://x.test/a") == "ok"
    backoffs = [d for d in slept if d > 0.1]  # limiter waits are tiny
    assert c.stats.retries == 2 and backoffs[1] > backoffs[0]  # exponential

    always = HttpClient("t", user_agent="x", per_second=1000, retries=1, sleep=rec,
                        transport=httpx.MockTransport(lambda r: httpx.Response(500)))  # fmt: skip
    with pytest.raises(ProviderError):
        await always.get_text("https://x.test/b")
    notfound = HttpClient("t", user_agent="x", per_second=1000, sleep=rec,
                          transport=httpx.MockTransport(lambda r: httpx.Response(404)))  # fmt: skip
    with pytest.raises(ProviderError) as e:
        await notfound.get_text("https://x.test/c")
    assert e.value.status == 404 and notfound.stats.http_requests == 1  # 4xx not retried


async def test_cache_hits_and_secret_not_in_key(tmp_path):
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(200, text="body")

    c = HttpClient(
        "t",
        user_agent="x",
        per_second=1000,
        cache=FileCache(tmp_path),
        transport=httpx.MockTransport(h),
    )
    await c.get_text("https://x.test/a", params={"api_key": "SECRET", "q": 1}, ttl=60)
    await c.get_text("https://x.test/a", params={"api_key": "OTHER", "q": 1}, ttl=60)
    assert n["c"] == 1 and c.stats.cache_hits == 1
    assert "SECRET" not in "".join(p.read_text() for p in tmp_path.iterdir())


async def test_rate_limiter_spacing_with_fake_clock():
    t = {"now": 0.0}
    waits = []

    async def sleep(d):
        waits.append(d)
        t["now"] += d

    rl = RateLimiter(2.0, sleep=sleep, clock=lambda: t["now"])
    for _ in range(3):
        await rl.wait()
    assert waits == pytest.approx([0.5, 0.5])


async def test_size_bounded_read():
    big = httpx.MockTransport(lambda r: httpx.Response(200, text="a" * 100_000))
    c = HttpClient("t", user_agent="x", per_second=1000, transport=big)
    assert len(await c.get_text("https://x.test/big", max_bytes=1000)) <= 1000


def test_asyncio_marker_available():
    assert asyncio.iscoroutinefunction(nosleep)
