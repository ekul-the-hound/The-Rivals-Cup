"""Google News RSS: discovery only. No page fetching; dedupes syndicated stories."""

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.models.enums import EvidenceQuality
from app.services.providers.base import FileCache, HttpClient

RSS = "https://news.google.com/rss/search"
PRIMARY_DOMAINS = {
    "sec.gov",
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
    "accesswire.com",
}
SECONDARY_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "apnews.com", "marketwatch.com",
    "barrons.com", "nytimes.com", "forbes.com", "fortune.com", "investors.com", "axios.com",
}  # fmt: skip


class NewsStory(BaseModel):
    headline: str
    publisher_name: str | None
    publisher_domain: str | None
    url: str | None
    published_at: datetime | None
    story_hash: str
    evidence_quality: EvidenceQuality
    link_confirmed: bool
    query: str


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def classify_source(domain: str | None) -> EvidenceQuality:
    if not domain:
        return EvidenceQuality.UNVERIFIED
    root = ".".join(domain.split(".")[-2:])
    if root in PRIMARY_DOMAINS:
        return EvidenceQuality.PRIMARY  # company-issued releases / filings
    if root in SECONDARY_DOMAINS:
        return EvidenceQuality.SECONDARY
    return EvidenceQuality.UNVERIFIED


def normalize_title(title: str, publisher: str | None = None) -> str:
    t = title
    if publisher and t.endswith(f" - {publisher}"):
        t = t[: -len(publisher) - 3]
    else:
        t = re.sub(r"\s+-\s+[^-]{2,40}$", "", t)
    return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


def story_hash(norm_title: str) -> str:
    return hashlib.sha1(norm_title[:90].encode()).hexdigest()


_RANK = {EvidenceQuality.PRIMARY: 0, EvidenceQuality.SECONDARY: 1, EvidenceQuality.UNVERIFIED: 2}


def dedupe_stories(stories: list[NewsStory]) -> list[NewsStory]:
    """Keep one story per syndicated headline: best source class, then earliest timestamp."""
    best: dict[str, NewsStory] = {}
    far = datetime.max.replace(tzinfo=UTC)
    for s in stories:
        cur = best.get(s.story_hash)
        if cur is None or (_RANK[s.evidence_quality], s.published_at or far) < (
            _RANK[cur.evidence_quality],
            cur.published_at or far,
        ):
            best[s.story_hash] = s
    return list(best.values())


def parse_feed(xml_text: str, query: str) -> list[NewsStory]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[NewsStory] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        src = item.find("source")
        pub_name = (src.text or "").strip() if src is not None else None
        domain = _domain(src.get("url")) if src is not None else None
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            published = (
                published.astimezone(UTC) if published.tzinfo else published.replace(tzinfo=UTC)
            )
        except (TypeError, ValueError):
            published = None
        link = (item.findtext("link") or "").strip() or None
        norm = normalize_title(title, pub_name)
        out.append(
            NewsStory(
                headline=re.sub(r"\s+-\s+" + re.escape(pub_name) + r"$", "", title)
                if pub_name
                else title,
                publisher_name=pub_name,
                publisher_domain=domain,
                url=link,
                published_at=published,
                story_hash=story_hash(norm),
                evidence_quality=classify_source(domain),
                link_confirmed=bool(link and domain and published),
                query=query,
            )
        )
    return out


class GoogleNewsProvider:
    name = "google_news_rss"

    def __init__(
        self,
        user_agent: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        sleep=None,
    ) -> None:
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name,
            user_agent=user_agent,
            per_second=0.5,
            timeout=timeout,
            cache=FileCache(cache_dir),
            transport=transport,
            **kw,
        )

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self.http.aclose()

    async def search(self, query: str, days: int = 7) -> list[NewsStory]:
        xml_text = await self.http.get_text(
            RSS,
            params={"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
            ttl=3600,
        )
        return dedupe_stories(parse_feed(xml_text, query))
