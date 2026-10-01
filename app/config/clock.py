"""UTC internally; America/Chicago for display only."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_display(dt: datetime, tz: str = "America/Chicago") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S %Z")
