"""Source freshness rules. No holiday calendar: a market holiday can cause a false STALE."""

from datetime import UTC, date, datetime, timedelta

from app.models.enums import DataStatus

MAX_AGE_DAYS = {
    "macro_daily": 5,  # DGS2, DGS10, T10Y2Y, VIXCLS (published with a lag)
    "macro_monthly": 45,  # FEDFUNDS
    "sec_run": 3,  # days since last successful SEC refresh
    "news": 7,
    "wiki_profile": 180,
    "blackout_verification": 7,  # manual verification should be within a week of the scoring week
    "liquidity": 5,
}


def expected_last_bar(today: date) -> date:
    """Most recent weekday strictly before `today` (daily bars land after the close)."""
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def price_status(latest_bar: date | None, today: date) -> DataStatus:
    if latest_bar is None:
        return DataStatus.MISSING
    return DataStatus.AVAILABLE if latest_bar >= expected_last_bar(today) else DataStatus.STALE


def age_status(ts: datetime | date | str | None, kind: str, now: datetime) -> DataStatus:
    if ts is None or ts == "":
        return DataStatus.MISSING
    if isinstance(ts, str):  # PostgREST returns ISO strings
        ts = (
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if "T" in ts or " " in ts
            else date.fromisoformat(ts)
        )
    if not isinstance(ts, datetime):
        ts = datetime(ts.year, ts.month, ts.day, tzinfo=UTC)
    elif ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (
        DataStatus.AVAILABLE if now - ts <= timedelta(days=MAX_AGE_DAYS[kind]) else DataStatus.STALE
    )
