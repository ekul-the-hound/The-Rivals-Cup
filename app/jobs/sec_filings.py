"""refresh_sec_filings: 8-K / 10-Q / 10-K / Form 4 metadata, bounded 8-K excerpts, Form 4 parse."""

from datetime import timedelta

from app.services.ingestion.runner import JobContext, JobResult
from app.services.providers.base import ProviderError

MAX_EXCERPTS_PER_COMPANY = 10


async def refresh_sec_filings(ctx: JobContext) -> JobResult:
    sec = ctx.providers.require("sec")
    rows_in = rows_out = 0
    warnings: list[str] = []
    secs = {s["id"]: s for s in ctx.db.select("securities")}
    for c in ctx.db.select("companies"):
        if not c.get("cik") or c["security_id"] not in secs:
            continue
        tk = secs[c["security_id"]]["ticker"]
        have = ctx.db.select(
            "filing_documents",
            columns="accession_number,excerpt,form4_summary",
            eq={"security_id": c["security_id"]},
        )
        known = {h["accession_number"]: h for h in have}
        since = ctx.today - timedelta(days=14 if have else 90)
        try:
            filings = await sec.recent_filings(int(c["cik"]), since)
        except ProviderError as exc:
            warnings.append(f"{tk}: {exc}")
            continue
        rows_in += len(filings)
        rows, excerpts = [], 0
        for f in filings:
            row = {
                "security_id": c["security_id"], "form_type": f.form_type, "accession_number": f.accession_number,
                "filed_at": f.filed_at, "period_of_report": f.period_of_report, "url": f.url,
                "title": f.description, "primary_document": f.primary_document, "items": f.items,
                "evidence_quality": "PRIMARY", "data_status": "AVAILABLE", "retrieved_at": ctx.now,
            }  # fmt: skip
            prior = known.get(f.accession_number, {})
            try:
                if (
                    f.form_type == "8-K"
                    and not prior.get("excerpt")
                    and excerpts < MAX_EXCERPTS_PER_COMPANY
                ):
                    row["excerpt"] = await sec.filing_excerpt(f)
                    excerpts += 1
                elif f.form_type == "4" and not prior.get("form4_summary"):
                    s4 = await sec.form4_summary(f)
                    row["form4_summary"] = s4.model_dump() if s4 else None
            except ProviderError as exc:
                warnings.append(f"{tk} {f.accession_number}: {exc}")
            rows.append(row)
        rows_out += ctx.db.upsert("filing_documents", rows, "accession_number")
    return JobResult(
        job="refresh_sec_filings", rows_read=rows_in, rows_written=rows_out, warnings=warnings
    )
