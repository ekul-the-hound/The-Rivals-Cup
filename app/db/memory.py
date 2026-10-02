"""In-memory DB implementing Store + upsert. Used by tests and the --mock CLI mode."""

import uuid
from typing import Any

from app.db.writer import jsonable

_NO_ID_TABLES = {"system_control_state"}


class InMemoryDB:
    def __init__(self, data: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.data: dict[str, list[dict[str, Any]]] = {
            t: [jsonable(dict(r)) for r in rows] for t, rows in (data or {}).items()
        }

    # --- Store ---
    def select(
        self,
        table,
        *,
        columns="*",
        eq=None,
        is_null=None,
        order=None,
        desc=False,
        limit=None,
        gte=None,
        lte=None,
        in_=None,
        offset=None,
    ):
        rows = [dict(r) for r in self.data.get(table, [])]
        for k, v in (eq or {}).items():
            rows = [r for r in rows if r.get(k) == jsonable(v)]
        for k in is_null or []:
            rows = [r for r in rows if r.get(k) is None]
        for k, v in (gte or {}).items():
            rows = [r for r in rows if r.get(k) is not None and str(r[k]) >= str(jsonable(v))]
        for k, v in (lte or {}).items():
            rows = [r for r in rows if r.get(k) is not None and str(r[k]) <= str(jsonable(v))]
        for k, vals in (in_ or {}).items():
            allowed = {str(jsonable(x)) for x in vals}
            rows = [r for r in rows if str(r.get(k)) in allowed]
        if order:
            rows.sort(key=lambda r: (r.get(order) is None, r.get(order)), reverse=desc)
        if offset:
            rows = rows[offset:]
        return rows[:limit] if limit else rows

    def count(self, table):
        return len(self.data.get(table, []))

    # --- upsert ---
    def upsert(self, table, rows, on_conflict, ignore_duplicates=False):
        cols = [c.strip() for c in on_conflict.split(",")]
        existing = self.data.setdefault(table, [])
        for raw in rows:
            row = jsonable(dict(raw))
            match = next(
                (e for e in existing if all(e.get(c) == row.get(c) for c in cols)),
                None,
            )
            if match is None:
                if table not in _NO_ID_TABLES:
                    row.setdefault("id", str(uuid.uuid4()))
                existing.append(row)
            elif not ignore_duplicates:
                match.update(row)
        return len(rows)
