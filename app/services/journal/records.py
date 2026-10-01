"""Record trades the owner has ALREADY entered manually in Trader View.

Nothing here places, queues, simulates or imports a trade. Every call requires an explicit
`confirmed_entered_manually=True`. Recording never changes a research pair's status/approval, and
approving a pair in research never creates a record here.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.writer import DB
from app.services.journal.guard import JournalWriteGuard
from app.services.pairs.blackout import scoring_week

NOTICE = (
    "Journal record of a trade you entered manually in Trader View. "
    "This system did not place it and cannot place or manage trades."
)


class JournalError(ValueError):
    pass


class LegEntry(BaseModel):
    ticker: str
    side: str = Field(pattern="^(LONG|SHORT)$")
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    entered_at: datetime
    notes: str | None = None
    stop_concept: str | None = None
    target_concept: str | None = None


def _require_confirmation(confirmed: bool) -> None:
    if confirmed is not True:
        raise JournalError(
            "Refusing to record: set confirmed_entered_manually=True only after you have "
            "independently entered this trade in Trader View."
        )


def _security_id(db: JournalWriteGuard, ticker: str) -> str:
    rows = db.select("securities", eq={"ticker": ticker.upper()})
    if not rows:
        raise JournalError(f"unknown ticker {ticker!r}; add it to securities first")
    return rows[0]["id"]


def ensure_portfolio(
    db: DB, name: str = "WSR Manual", starting_cash_usd: float = 1_000_000.0
) -> str:
    g = JournalWriteGuard(db)
    rows = g.select("manual_portfolios", eq={"name": name})
    if rows:
        return rows[0]["id"]
    pid = str(uuid.uuid4())
    g.upsert(
        "manual_portfolios",
        [{"id": pid, "name": name, "starting_cash_usd": starting_cash_usd, "is_active": True}],
        "id",
    )
    return pid


def _leg_rows(
    g: JournalWriteGuard,
    portfolio_id: str,
    leg: LegEntry,
    pair_record_id: str | None,
    peer_pair_id: str | None,
) -> str:
    sid = _security_id(g, leg.ticker)
    pos_id = str(uuid.uuid4())
    wk, _ = scoring_week(leg.entered_at.date())
    g.upsert(
        "manual_positions",
        [
            {
                "id": pos_id,
                "portfolio_id": portfolio_id,
                "pair_id": peer_pair_id,
                "manual_pair_record_id": pair_record_id,
                "security_id": sid,
                "side": leg.side,
                "quantity": leg.quantity,
                "avg_entry_price": leg.entry_price,
                "opened_at": leg.entered_at,
                "is_open": True,
                "scoring_week_start": wk,
                "stop_concept": leg.stop_concept,
                "target_concept": leg.target_concept,
                "notes": leg.notes,
            }
        ],
        "id",
    )
    g.upsert(
        "manual_trades",
        [
            {
                "id": str(uuid.uuid4()),
                "portfolio_id": portfolio_id,
                "position_id": pos_id,
                "pair_id": peer_pair_id,
                "security_id": sid,
                "side": leg.side,
                "action": "OPEN",
                "quantity": leg.quantity,
                "price": leg.entry_price,
                "traded_at": leg.entered_at,
                "notes": leg.notes,
            }
        ],
        "id",
    )
    return pos_id


def record_manual_entry(
    db: DB,
    *,
    portfolio_id: str,
    leg: LegEntry,
    confirmed_entered_manually: bool,
    pair_record_id: str | None = None,
) -> dict[str, Any]:
    """Journal ONE leg you already entered in Trader View."""
    _require_confirmation(confirmed_entered_manually)
    g = JournalWriteGuard(db)
    pos_id = _leg_rows(g, portfolio_id, leg, pair_record_id, None)
    return {"position_id": pos_id, "recorded": True, "is_order": False, "notice": NOTICE}


def record_manual_pair(
    db: DB,
    *,
    portfolio_id: str,
    name: str,
    long_leg: LegEntry,
    short_leg: LegEntry,
    confirmed_entered_manually: bool,
    peer_pair_id: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Journal a pair you already entered as two separate Trader View trades, linked together."""
    _require_confirmation(confirmed_entered_manually)
    if long_leg.side != "LONG" or short_leg.side != "SHORT":
        raise JournalError("long_leg must be LONG and short_leg must be SHORT")
    g = JournalWriteGuard(db)
    rec_id = str(uuid.uuid4())
    opened = min(long_leg.entered_at, short_leg.entered_at)
    wk, _ = scoring_week(opened.date())
    g.upsert(
        "manual_pair_records",
        [
            {
                "id": rec_id,
                "portfolio_id": portfolio_id,
                "peer_pair_id": peer_pair_id,
                "name": name,
                "scoring_week_start": wk,
                "notes": notes,
                "status": "OPEN",
                "opened_at": opened,
            }
        ],
        "id",
    )
    ids = [_leg_rows(g, portfolio_id, leg, rec_id, peer_pair_id) for leg in (long_leg, short_leg)]
    return {
        "pair_record_id": rec_id,
        "position_ids": ids,
        "recorded": True,
        "is_order": False,
        "notice": NOTICE,
    }


def record_manual_exit(
    db: DB,
    *,
    position_id: str,
    exit_price: float,
    exited_at: datetime,
    reason: str,
    confirmed_entered_manually: bool,
) -> dict[str, Any]:
    """Journal an exit you already made manually in Trader View."""
    _require_confirmation(confirmed_entered_manually)
    if exit_price <= 0:
        raise JournalError("exit_price must be > 0")
    g = JournalWriteGuard(db)
    rows = g.select("manual_positions", eq={"id": position_id})
    if not rows:
        raise JournalError("unknown position")
    pos = rows[0]
    if not pos.get("is_open", True):
        raise JournalError("position is already recorded as exited")
    g.upsert(
        "manual_positions",
        [
            {
                **pos,
                "is_open": False,
                "closed_at": exited_at,
                "exit_price": exit_price,
                "exit_reason": reason,
            }
        ],
        "id",
    )
    g.upsert(
        "manual_trades",
        [
            {
                "id": str(uuid.uuid4()),
                "portfolio_id": pos["portfolio_id"],
                "position_id": position_id,
                "pair_id": pos.get("pair_id"),
                "security_id": pos["security_id"],
                "side": pos["side"],
                "action": "CLOSE",
                "quantity": pos["quantity"],
                "price": exit_price,
                "traded_at": exited_at,
                "notes": reason,
            }
        ],
        "id",
    )
    rec = pos.get("manual_pair_record_id")
    if rec:
        legs = g.select("manual_positions", eq={"manual_pair_record_id": rec})
        if all(not leg["is_open"] for leg in legs):
            prow = g.select("manual_pair_records", eq={"id": rec})[0]
            g.upsert(
                "manual_pair_records",
                [{**prow, "status": "CLOSED", "closed_at": exited_at}],
                "id",
            )
    return {"position_id": position_id, "recorded": True, "is_order": False, "notice": NOTICE}
