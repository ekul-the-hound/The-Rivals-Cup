"""Weekly event blackout (manual fields). A pair is excluded from default Monday candidates if
either leg has an earnings/known major event in the scoring week, unless a logged override exists.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.services.validation.freshness import MAX_AGE_DAYS


def scoring_week(today: date) -> tuple[date, date]:
    """Monday-entered strategy: Sat/Sun look ahead to next Monday; Mon-Fri use this week."""
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=4)


def _d(v: Any) -> date | None:
    return date.fromisoformat(str(v)[:10]) if v else None


@dataclass
class LegBlackout:
    ticker: str
    blocked: bool = False
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evaluate_leg(
    ticker: str, row: dict[str, Any] | None, start: date, end: date, now: datetime
) -> LegBlackout:
    leg = LegBlackout(ticker)
    if row is None:
        leg.warnings.append(f"{ticker}: no blackout row for this week (event dates not entered)")
        return leg
    for label, key in (
        ("earnings", "earnings_date_if_known"),
        ("major event", "known_major_event_date"),
    ):
        d = _d(row.get(key))
        if d and start <= d <= end:
            leg.blocked = True
            leg.reasons.append(f"{ticker}: {label} on {d.isoformat()} falls in scoring week")
    verified = row.get("manually_verified_at")
    if not verified:
        leg.warnings.append(f"{ticker}: blackout fields never manually verified")
    else:
        v = datetime.fromisoformat(str(verified).replace("Z", "+00:00"))
        v = v if v.tzinfo else v.replace(tzinfo=UTC)
        if now - v > timedelta(days=MAX_AGE_DAYS["blackout_verification"]) + timedelta(days=0):
            leg.warnings.append(f"{ticker}: blackout verification older than 7 days")
    if row.get("event_risk_notes"):
        leg.warnings.append(f"{ticker}: note: {row['event_risk_notes']}")
    return leg


@dataclass
class PairEligibility:
    eligible: bool
    blocked_reasons: list[str]
    warnings: list[str]
    override_reason: str | None = None


def evaluate_pair(legs: list[LegBlackout], override: dict[str, Any] | None) -> PairEligibility:
    reasons = [r for leg in legs for r in leg.reasons]
    warnings = [w for leg in legs for w in leg.warnings]
    blocked = any(leg.blocked for leg in legs)
    if blocked and override and str(override.get("reason", "")).strip():
        return PairEligibility(True, reasons, warnings, override["reason"])
    return PairEligibility(not blocked, reasons, warnings)
