"""Compliance guards. Declarative list of what this codebase must never do."""

from app.models.enums import Direction

PROHIBITED_CAPABILITIES: tuple[str, ...] = (
    "order submission, creation, modification, cancellation, queueing, scheduling or closing",
    "paper broker or order simulator",
    "connection to Wall Street Rivals, Trader View, or any brokerage/execution system",
    "browser automation",
    "storage of WSR/Trader View credentials",
    "Telegram (or any messenger) to execute or manage a trade",
    "automated or scheduled trading",
    "exploiting bugs, stale/erroneous prices, scoring ambiguity, roster rules or data discrepancies",
    "claiming any signal has been entered as a trade",
)


class ResearchOnlyViolation(RuntimeError):
    pass


def assert_research_only(signal_sending_enabled: bool) -> None:
    """Raise if anything ever tries to run with signal sending on."""
    if signal_sending_enabled:
        raise ResearchOnlyViolation("signal sending is permanently disabled")


def is_recordable_side(side: Direction) -> bool:
    """Manual positions/trades must be LONG or SHORT; NO_TRADE is a research label only."""
    return side in (Direction.LONG, Direction.SHORT)
