"""Refresh cadence as DATA. Nothing here runs by itself; see docs/data_providers.md for how to
trigger `python -m scripts.refresh_weekly_research --profile X` (Task Scheduler / cron).
No intraday scans. Times are America/Chicago.
"""

PROFILES: dict[str, list[str]] = {
    # weekdays ~15:45 CT, after the cash close
    "daily": [
        "refresh_daily_prices", "refresh_market_context", "refresh_macro_context",
        "refresh_sec_filings", "refresh_corporate_catalysts", "refresh_news", "refresh_data_quality",
    ],
    # Sunday ~18:00 CT: full refresh + build the blackout list for the coming Monday
    "sunday": [
        "refresh_universe", "refresh_daily_prices", "refresh_market_context", "refresh_macro_context",
        "refresh_sec_filings", "refresh_corporate_catalysts", "refresh_news",
        "build_weekly_event_blackout_list", "refresh_data_quality",
    ],
    # Monday ~07:30 CT pre-market / manual: freshen price+macro+news, recompute blackout
    "monday": [
        "refresh_daily_prices", "refresh_market_context", "refresh_macro_context",
        "refresh_news", "build_weekly_event_blackout_list", "refresh_data_quality",
    ],
}  # fmt: skip
# Target-sector universe (docs/universe_builder.md). Run repeatedly until classification and market
# data cover the whole list: each run handles one rate-limited batch and resumes where it stopped.
PROFILES["universe"] = [
    "refresh_us_listed_symbol_universe",
    "classify_target_sectors",
    "refresh_universe_market_data",
    "build_target_sector_universe_views",
]
PROFILES["full"] = PROFILES["sunday"]

SCHEDULE_HINT = {"daily": "Mon-Fri 15:45 CT", "sunday": "Sun 18:00 CT", "monday": "Mon 07:30 CT"}
