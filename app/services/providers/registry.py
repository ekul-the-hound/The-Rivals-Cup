"""Builds the enabled providers from settings. Missing config => provider is None + reason."""

from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.services.providers.alpha_vantage import AlphaVantageProvider
from app.services.providers.base import ProviderUnavailable
from app.services.providers.finnhub import FinnhubProvider
from app.services.providers.finra import FinraProvider
from app.services.providers.fred import FredProvider
from app.services.providers.google_news import GoogleNewsProvider
from app.services.providers.nasdaq_trader import NasdaqTraderProvider
from app.services.providers.sec import SecProvider
from app.services.providers.wikipedia import WikipediaProvider
from app.services.providers.yahoo import YahooProvider


@dataclass
class Providers:
    sec: SecProvider | None = None
    fred: FredProvider | None = None
    yahoo: YahooProvider | None = None
    news: GoogleNewsProvider | None = None
    wiki: WikipediaProvider | None = None
    nasdaq: NasdaqTraderProvider | None = None
    finra: FinraProvider | None = None
    finnhub: FinnhubProvider | None = None
    alpha_vantage: AlphaVantageProvider | None = None
    unavailable: dict[str, str] = field(default_factory=dict)

    def require(self, name: str):
        p = getattr(self, name)
        if p is None:
            raise ProviderUnavailable(self.unavailable.get(name, f"{name} unavailable"))
        return p

    def all(self):
        return [
            p
            for p in (
                self.sec,
                self.fred,
                self.yahoo,
                self.news,
                self.wiki,
                self.nasdaq,
                self.finra,
                self.finnhub,
                self.alpha_vantage,
            )
            if p
        ]

    def stats(self) -> dict[str, int]:
        t = {"http_requests": 0, "cache_hits": 0, "retries": 0}
        for p in self.all():
            for k in t:
                t[k] += getattr(p.http.stats, k)
        return t

    async def aclose(self) -> None:
        for p in self.all():
            await p.aclose()


def build_providers(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None, sleep=None
) -> Providers:
    cache = None if transport else settings.provider_cache_dir
    kw = {
        "cache_dir": cache,
        "transport": transport,
        "timeout": settings.http_timeout_seconds,
        "sleep": sleep,
    }
    ua = settings.effective_web_user_agent
    out = Providers()
    for attr, factory in {
        "sec": lambda: SecProvider(settings.sec_user_agent, **kw),
        "fred": lambda: FredProvider(settings.fred_api_key.get_secret_value(), **kw),
        "yahoo": lambda: YahooProvider(ua, **kw),
        "news": lambda: GoogleNewsProvider(ua, **kw),
        "wiki": lambda: WikipediaProvider(ua, **kw),
        "nasdaq": lambda: NasdaqTraderProvider(ua, **kw),
        "finra": lambda: FinraProvider(ua, **kw),
        "finnhub": lambda: FinnhubProvider(settings.finnhub_api_key.get_secret_value(), **kw),
        "alpha_vantage": lambda: AlphaVantageProvider(
            settings.alpha_vantage_api_key.get_secret_value(), **kw
        ),
    }.items():
        try:
            setattr(out, attr, factory())
        except ProviderUnavailable as exc:
            out.unavailable[attr] = str(exc)
    return out
