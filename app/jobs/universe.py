"""refresh_universe: SEC CIK mapping, Wikipedia static context, Yahoo profile (best effort)."""

from app.jobs._util import active_securities
from app.models.enums import DataStatus
from app.services.ingestion.runner import JobContext, JobResult
from app.services.providers.base import ProviderError
from app.services.validation.freshness import age_status


async def refresh_universe(ctx: JobContext) -> JobResult:
    sec = ctx.providers.require("sec")
    cik_map = await sec.ticker_cik_map()
    companies = {c["security_id"]: c for c in ctx.db.select("companies")}
    rows, warnings = [], []
    equities = [s for s in active_securities(ctx.db) if not s["is_etf"]]
    for s in equities:
        existing = companies.get(s["id"], {})
        row: dict = {"security_id": s["id"]}
        info = cik_map.get(s["ticker"])
        if info:
            row.update(
                cik=str(info.cik).zfill(10),
                sec_ticker_title=info.title,
                data_status=DataStatus.AVAILABLE,
            )
            if not existing.get("legal_name"):
                row["legal_name"] = info.title
        else:
            warnings.append(f"{s['ticker']}: not found in SEC ticker map")
        wiki = ctx.providers.wiki
        if (
            wiki
            and age_status(existing.get("wiki_fetched_at"), "wiki_profile", ctx.now)
            != DataStatus.AVAILABLE
        ):
            try:
                prof = await wiki.describe(existing.get("wiki_title") or s["name"] or s["ticker"])
            except ProviderError as exc:
                warnings.append(f"{s['ticker']}: wikipedia: {exc}")
                prof = None
            if prof:  # static context only, never a catalyst
                row.update(
                    wiki_summary=prof.summary, wiki_industry=prof.industry, wiki_products=prof.products,
                    wiki_competitors_hint=prof.competitors_hint, wiki_fetched_at=ctx.now,
                )  # fmt: skip
                if not existing.get("wiki_title"):
                    row["wiki_title"] = prof.title
        yahoo = ctx.providers.yahoo
        if yahoo:
            prof = await yahoo.profile(s["ticker"])  # returns None on any refusal
            if prof and prof.market_cap:
                row.update(
                    market_cap_usd=prof.market_cap, market_cap_source="yahoo_finance",
                    market_cap_as_of=ctx.today, yahoo_sector=prof.sector, yahoo_industry=prof.industry,
                )  # fmt: skip
        rows.append(row)
    written = ctx.db.upsert("companies", rows, "security_id")
    return JobResult(
        job="refresh_universe", rows_read=len(equities), rows_written=written, warnings=warnings
    )
