"""refresh_corporate_catalysts: rule-based detection over stored SEC filings."""

from datetime import timedelta

from app.services.ingestion.catalysts import detect_catalysts
from app.services.ingestion.runner import JobContext, JobResult


async def refresh_corporate_catalysts(ctx: JobContext, lookback_days: int = 45) -> JobResult:
    filings = ctx.db.select(
        "filing_documents", gte={"filed_at": ctx.today - timedelta(days=lookback_days)}
    )
    rows = []
    for f in filings:
        for h in detect_catalysts(f, f.get("excerpt")):
            rows.append(
                {
                    "security_id": f["security_id"], "catalyst_type": h.catalyst_type.value,
                    "headline": h.headline, "detail": h.detail, "event_date": str(f["filed_at"])[:10],
                    "source_url": f.get("url"), "filing_id": f.get("id"), "evidence_quality": "PRIMARY",
                    "data_status": "AVAILABLE", "dedupe_key": h.dedupe_key,
                }
            )  # fmt: skip
    return JobResult(
        job="refresh_corporate_catalysts", rows_read=len(filings),
        rows_written=ctx.db.upsert("corporate_catalysts", rows, "dedupe_key"),
    )  # fmt: skip
