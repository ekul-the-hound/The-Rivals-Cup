"""Research-review state ONLY. Nothing here can create, change or imply a trade.

`ResearchWriteGuard` wraps a DB and permits upserts to a tiny allow-list of research tables.
Even on those it rejects anything resembling trade state (e.g. pair status ACTIVE_MANUALLY).
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.db.store import Store
from app.db.writer import DB
from app.models.enums import PairStatus

# table -> allowed upsert columns (None = any column; the table is research output only)
ALLOWED_WRITES: dict[str, set[str] | None] = {
    "pair_rankings": None,
    "weekly_portfolios": None,
    "pair_manual_decisions": {"pair_id", "week_start", "decision", "reason", "decided_at"},
    "peer_pairs": {"id", "status"},  # review status only
}
REVIEW_STATUSES = {PairStatus.CANDIDATE.value, PairStatus.APPROVED_FOR_REVIEW.value}
TRADE_STATUSES = {PairStatus.ACTIVE_MANUALLY.value, PairStatus.CLOSED_MANUALLY.value}


class ResearchWriteViolation(RuntimeError):
    pass


class PairNotFound(ValueError):
    pass


class ReviewBlocked(ValueError):
    pass


class ResearchWriteGuard:
    """Read-through; upsert only to the allow-list. Not a trade/order/execution path."""

    def __init__(self, db: DB) -> None:
        self._db = db

    def select(self, *a: Any, **k: Any):
        return self._db.select(*a, **k)

    def count(self, table: str) -> int:
        return self._db.count(table)

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: str,
        ignore_duplicates: bool = False,
    ) -> int:
        if table not in ALLOWED_WRITES:
            raise ResearchWriteViolation(f"write to '{table}' is not permitted from pair research")
        cols = ALLOWED_WRITES[table]
        for r in rows:
            if cols is not None and not set(r) <= cols:
                raise ResearchWriteViolation(
                    f"columns {sorted(set(r) - cols)} not permitted on '{table}'"
                )
            if table == "peer_pairs" and str(r.get("status")) not in REVIEW_STATUSES:
                raise ResearchWriteViolation(
                    f"pair status '{r.get('status')}' is not a research-review status"
                )
            if (
                table == "weekly_portfolios"
                and r.get("status", "RESEARCH_DRAFT") != "RESEARCH_DRAFT"
            ):
                raise ResearchWriteViolation("weekly portfolios can only be RESEARCH_DRAFT")
        return self._db.upsert(table, rows, on_conflict, ignore_duplicates)


class ReviewResult(BaseModel):
    pair_id: str
    week_start: date
    decision: Literal["INCLUDE", "EXCLUDE"]
    pair_status: str
    reason: str | None = None
    is_trade: Literal[False] = False
    note: str = (
        "Research-review state only. This is NOT a trade: nothing was entered, ordered or recorded "
        "as a position. Place any trade yourself in Trader View and record it manually afterward."
    )


def _get_pair(store: Store, pair_id: str) -> dict[str, Any]:
    rows = store.select("peer_pairs", eq={"id": pair_id}, limit=1)
    if not rows:
        raise PairNotFound(pair_id)
    return rows[0]


def approve_for_review(
    db: DB, pair_id: str, week_start: date, now: datetime, eligible: bool, why_ineligible: str = ""
) -> ReviewResult:
    """Pin a pair into this week's review list. Does NOT bypass hard eligibility."""
    g = ResearchWriteGuard(db)
    pair = _get_pair(g, pair_id)
    if pair["status"] in TRADE_STATUSES:
        raise ReviewBlocked(f"pair is {pair['status']}; review approval does not apply")
    if not eligible:
        raise ReviewBlocked(
            f"pair fails hard eligibility, so it cannot be approved for review: {why_ineligible}"
        )
    g.upsert(
        "pair_manual_decisions",
        [{"pair_id": pair_id, "week_start": week_start, "decision": "INCLUDE", "reason": None, "decided_at": now}],
        "pair_id,week_start",
    )  # fmt: skip
    g.upsert("peer_pairs", [{"id": pair_id, "status": PairStatus.APPROVED_FOR_REVIEW.value}], "id")
    return ReviewResult(
        pair_id=pair_id, week_start=week_start, decision="INCLUDE",
        pair_status=PairStatus.APPROVED_FOR_REVIEW.value,
    )  # fmt: skip


def exclude(db: DB, pair_id: str, week_start: date, now: datetime, reason: str) -> ReviewResult:
    g = ResearchWriteGuard(db)
    pair = _get_pair(g, pair_id)
    if len((reason or "").strip()) < 5:
        raise ReviewBlocked("an exclusion reason of at least 5 characters is required")
    g.upsert(
        "pair_manual_decisions",
        [{"pair_id": pair_id, "week_start": week_start, "decision": "EXCLUDE", "reason": reason.strip(), "decided_at": now}],
        "pair_id,week_start",
    )  # fmt: skip
    return ReviewResult(
        pair_id=pair_id, week_start=week_start, decision="EXCLUDE", pair_status=pair["status"],
        reason=reason.strip(),
    )  # fmt: skip
