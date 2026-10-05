"""Which reports count as "this week" and "next week" (Monday to Friday, display timezone)."""

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class WeekWindow:
    label: str  # "this" | "next"
    start: date  # Monday
    end: date  # Friday

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def week_windows(today: date) -> tuple[WeekWindow, WeekWindow]:
    """On Saturday or Sunday "this week" is the coming Monday-Friday, because that is the week you
    would be trading; Monday to Friday it is the current week."""
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(days=7)
    this = WeekWindow("this", monday, monday + timedelta(days=4))
    nxt = WeekWindow("next", monday + timedelta(days=7), monday + timedelta(days=11))
    return this, nxt


def window_for(d: date, today: date) -> str | None:
    this, nxt = week_windows(today)
    if this.contains(d):
        return "this"
    if nxt.contains(d):
        return "next"
    return None
