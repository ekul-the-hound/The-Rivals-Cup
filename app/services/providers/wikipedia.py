"""Wikipedia MediaWiki API: STATIC company context only. Never a market catalyst."""

import re

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient

API = "https://en.wikipedia.org/w/api.php"


class WikiProfile(BaseModel):
    title: str
    summary: str
    industry: str | None = None
    products: list[str] = []
    competitors_hint: str | None = None


def _clean(markup: str) -> str:
    s = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", markup, flags=re.S)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"<[^>]+>", "\n", s)
    return s.strip()


def _infobox_field(wikitext: str, name: str) -> list[str]:
    m = re.search(
        rf"^\s*\|\s*{name}\s*=\s*(.+?)(?=^\s*\|\s*\w+\s*=|\Z)", wikitext, re.S | re.M | re.I
    )
    if not m:
        return []
    raw = re.sub(r"\}\}\s*$", "", m.group(1).strip())
    return [p.strip(" *\t") for p in re.split(r"\n|,", _clean(raw)) if p.strip(" *\t")]


def competitors_hint(extract: str) -> str | None:
    hits = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", extract)
        if re.search(r"\b(compet\w+|rival\w*)\b", s, re.I)
    ]
    return " ".join(hits)[:500] or None  # a hint for manual peer mapping, not a fact table


class WikipediaProvider:
    name = "wikipedia"

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
            per_second=2.0,
            timeout=timeout,
            cache=FileCache(cache_dir),
            transport=transport,
            **kw,
        )

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self.http.aclose()

    async def describe(self, title: str) -> WikiProfile | None:
        data = await self.http.get_json(
            API,
            params={
                "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
                "prop": "extracts|revisions", "exintro": 1, "explaintext": 1,
                "rvprop": "content", "rvslots": "main", "rvsection": 0, "titles": title,
            },
            ttl=7 * 86400,
        )  # fmt: skip
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        page = pages[0]
        extract = page.get("extract") or ""
        wikitext = ((page.get("revisions") or [{}])[0].get("slots", {}).get("main", {})).get(
            "content", ""
        )
        industry = _infobox_field(wikitext, "industry")
        return WikiProfile(
            title=page.get("title", title),
            summary=extract[:1500],
            industry=", ".join(industry) or None,
            products=_infobox_field(wikitext, "products")[:10],
            competitors_hint=competitors_hint(extract),
        )
