"""Job runner: control-state gate, provider run logs, status roll-up."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.config import Settings
from app.config.clock import utcnow
from app.db.writer import DB
from app.services.providers.base import ProviderError, ProviderUnavailable
from app.services.providers.registry import Providers


@dataclass
class JobContext:
    settings: Settings
    db: DB
    providers: Providers
    now: datetime
    week_start: date | None = None

    @property
    def local_now(self) -> datetime:
        return self.now.astimezone(ZoneInfo(self.settings.display_timezone))

    @property
    def today(self) -> date:
        return self.local_now.date()


class JobResult(BaseModel):
    job: str
    status: str = "SUCCEEDED"
    rows_read: int = 0
    rows_written: int = 0
    message: str | None = None
    warnings: list[str] = []


def ingestion_allowed(db: DB) -> tuple[bool, str]:
    rows = db.select("system_control_state", limit=1)
    if not rows:
        return False, "system_control_state missing (treated as PAUSED)"
    r = rows[0]
    if r.get("mode") != "RESEARCH_ONLY":
        return False, f"system mode is {r.get('mode')}; set RESEARCH_ONLY to ingest"
    if not r.get("provider_ingestion_enabled"):
        return False, "provider_ingestion_enabled is false"
    return True, ""


JobFn = Callable[[JobContext], Awaitable[JobResult]]


async def run_job(ctx: JobContext, name: str, provider_label: str, fn: JobFn) -> JobResult:
    run_id = str(uuid.uuid4())
    base = {"id": run_id, "provider": provider_label, "job_name": name, "started_at": utcnow()}
    allowed, why = ingestion_allowed(ctx.db)
    if not allowed:
        ctx.db.upsert(
            "provider_run_logs",
            [{**base, "status": "SKIPPED", "finished_at": utcnow(), "error_message": why}],
            "id",
        )
        return JobResult(job=name, status="SKIPPED", message=why)
    ctx.db.upsert("provider_run_logs", [{**base, "status": "RUNNING"}], "id")
    before = ctx.providers.stats()
    try:
        res = await fn(ctx)
        if res.rows_written == 0 and res.warnings:
            res.status = "FAILED"
        elif res.warnings:
            res.status = "PARTIAL"
    except ProviderUnavailable as exc:
        res = JobResult(job=name, status="SKIPPED", message=str(exc))
    except (ProviderError, Exception) as exc:  # noqa: BLE001 - logged, never raised to the scheduler
        res = JobResult(job=name, status="FAILED", message=f"{type(exc).__name__}: {exc}"[:500])
    after = ctx.providers.stats()
    ctx.db.upsert(
        "provider_run_logs",
        [
            {
                **base,
                "status": res.status,
                "finished_at": utcnow(),
                "rows_read": res.rows_read,
                "rows_written": res.rows_written,
                "error_message": res.message,
                "http_requests": after["http_requests"] - before["http_requests"],
                "cache_hits": after["cache_hits"] - before["cache_hits"],
                "retries": after["retries"] - before["retries"],
                "metadata": {"warnings": res.warnings[:50]},
            }
        ],
        "id",
    )
    return res
