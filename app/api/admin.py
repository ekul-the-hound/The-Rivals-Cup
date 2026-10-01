"""Admin-only manual refresh of research DATA. Never touches trades, orders or any broker."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.deps import get_admin_db
from app.config import Settings, get_settings
from app.config.clock import utcnow
from app.db.writer import DB
from app.jobs import JOBS, PROFILES, run_jobs
from app.services.ingestion.runner import JobContext, ingestion_allowed
from app.services.providers.registry import build_providers

router = APIRouter(prefix="/admin", tags=["admin"])


async def execute_refresh(db: DB, settings: Settings, names: list[str]) -> None:
    providers = build_providers(settings)
    try:
        await run_jobs(
            JobContext(settings=settings, db=db, providers=providers, now=utcnow()), names
        )
    finally:
        await providers.aclose()


def _accept(names: list[str], bg: BackgroundTasks, db: DB, settings: Settings) -> dict:
    ok, why = ingestion_allowed(db)
    if not ok:
        raise HTTPException(409, f"ingestion not allowed: {why}")
    bg.add_task(execute_refresh, db, settings, names)
    return {
        "accepted": names,
        "note": "runs in background; see provider_run_logs / /research/status",
    }


@router.post("/refresh/{job_name}", status_code=202)
def refresh_job(
    job_name: str,
    bg: BackgroundTasks,
    db: DB = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if job_name not in JOBS:
        raise HTTPException(404, f"unknown job; choose from {sorted(JOBS)}")
    return _accept([job_name], bg, db, settings)


@router.post("/refresh-profile/{profile}", status_code=202)
def refresh_profile(
    profile: str,
    bg: BackgroundTasks,
    db: DB = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if profile not in PROFILES:
        raise HTTPException(404, f"unknown profile; choose from {sorted(PROFILES)}")
    return _accept(PROFILES[profile], bg, db, settings)
