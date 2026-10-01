"""refresh_news: Google News RSS discovery by ticker/company, peer group and sector."""

from app.jobs._util import short_name
from app.services.ingestion.runner import JobContext, JobResult
from app.services.providers.base import ProviderError


async def refresh_news(ctx: JobContext) -> JobResult:
    news = ctx.providers.require("news")
    secs = {s["id"]: s for s in ctx.db.select("securities", eq={"is_active": True})}
    comps = {c["security_id"]: c for c in ctx.db.select("companies")}
    members: dict[str, list[str]] = {}
    for m in ctx.db.select("peer_pair_members"):
        members.setdefault(m["pair_id"], []).append(m["security_id"])
    pairs = [p for p in ctx.db.select("peer_pairs") if p["status"] != "REJECTED"]
    used = {sid for p in pairs for sid in members.get(p["id"], [])}

    def name(sid: str) -> str:
        c = comps.get(sid, {})
        return short_name(c.get("wiki_title") or secs[sid]["name"])

    queries: list[tuple[str, str | None]] = []  # security-specific first so they win de-dupe
    queries += [(f'"{name(sid)}" {secs[sid]["ticker"]} stock', sid) for sid in used if sid in secs]
    queries += [
        (f'"{name(m[0])}" "{name(m[1])}"', None)
        for p in pairs
        if len(m := members.get(p["id"], [])) == 2
    ]
    queries += [(f"{sec} sector stocks", None) for sec in sorted({secs[s]["sector"] for s in used if secs[s].get("sector")})]  # fmt: skip

    best: dict[str, dict] = {}
    warnings: list[str] = []
    for q, sid in queries:
        try:
            stories = await news.search(q)
        except ProviderError as exc:
            warnings.append(f"{q}: {exc}")
            continue
        for st in stories:
            best.setdefault(
                st.story_hash,
                {
                    "content_hash": st.story_hash, "security_id": sid, "headline": st.headline,
                    "source_name": st.publisher_name, "publisher_name": st.publisher_name,
                    "publisher_domain": st.publisher_domain, "url": st.url, "published_at": st.published_at,
                    "evidence_quality": st.evidence_quality.value, "link_confirmed": st.link_confirmed,
                    "data_status": "AVAILABLE" if st.link_confirmed else "UNVERIFIED",
                    "matched_query": q, "retrieved_at": ctx.now,
                },
            )  # fmt: skip
    written = ctx.db.upsert(
        "news_items", list(best.values()), "content_hash", ignore_duplicates=True
    )
    return JobResult(
        job="refresh_news", rows_read=len(queries), rows_written=written, warnings=warnings
    )
