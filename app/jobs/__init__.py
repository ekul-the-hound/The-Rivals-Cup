"""Data refresh jobs. Each job is a one-shot async function; scheduling is external (cadence.py)."""

from app.jobs.cadence import PROFILES
from app.jobs.corporate_catalysts import refresh_corporate_catalysts
from app.jobs.data_quality import refresh_data_quality
from app.jobs.macro import refresh_macro_context
from app.jobs.market_context import refresh_market_context
from app.jobs.news import refresh_news
from app.jobs.prices import refresh_daily_prices
from app.jobs.sec_filings import refresh_sec_filings
from app.jobs.universe import refresh_universe
from app.jobs.weekly_blackout import build_weekly_event_blackout_list
from app.services.ingestion.runner import JobContext, JobResult, run_job

JOBS = {
    "refresh_universe": ("sec_edgar+wikipedia+yahoo_finance", refresh_universe),
    "refresh_daily_prices": ("yahoo_finance", refresh_daily_prices),
    "refresh_market_context": ("derived", refresh_market_context),
    "refresh_macro_context": ("fred", refresh_macro_context),
    "refresh_sec_filings": ("sec_edgar", refresh_sec_filings),
    "refresh_corporate_catalysts": ("derived", refresh_corporate_catalysts),
    "refresh_news": ("google_news_rss", refresh_news),
    "refresh_data_quality": ("derived", refresh_data_quality),
    "build_weekly_event_blackout_list": ("derived", build_weekly_event_blackout_list),
}


async def run_jobs(ctx: JobContext, names: list[str]) -> list[JobResult]:
    unknown = [n for n in names if n not in JOBS]
    if unknown:
        raise ValueError(f"unknown jobs: {unknown}")
    return [await run_job(ctx, n, *JOBS[n]) for n in names]


__all__ = ["JOBS", "PROFILES", "run_jobs"]
