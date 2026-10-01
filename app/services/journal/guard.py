"""Write allow-list for the manual journal. Records only; never orders, never research state."""

from typing import Any

from app.db.writer import DB

ALLOWED_TABLES = {
    "manual_portfolios",
    "manual_pair_records",
    "manual_positions",
    "manual_trades",
    "dividend_events",
}


class JournalWriteViolation(RuntimeError):
    pass


class JournalWriteGuard:
    """Read-through; upsert only to journal tables, always stamped source=MANUAL."""

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
        if table not in ALLOWED_TABLES:
            raise JournalWriteViolation(f"journal may not write to '{table}'")
        stamped = []
        for r in rows:
            r = dict(r)
            if table in {"manual_positions", "manual_trades", "manual_pair_records"}:
                r["source"] = "MANUAL"
            if table == "manual_positions":
                r["data_status"] = "MANUAL"
            if "pair_id" in r and table == "manual_trades" and r["pair_id"] is not None:
                pass  # link only; never alters peer_pairs
            stamped.append(r)
        return self._db.upsert(table, stamped, on_conflict, ignore_duplicates)
