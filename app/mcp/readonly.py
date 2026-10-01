"""Read-only data access for MCP tools.

`ReadOnlyStore` exposes ONLY `select` and `count`. It deliberately does not forward arbitrary
attributes, so there is no path to upsert/insert/update/delete or to the raw client.
`AsOfStore` additionally clamps time-series tables to an `as_of` instant.
"""

from datetime import date, datetime
from typing import Any

from app.db.store import Store

# table -> (column, kind). kind "date" compares against as_of.date(); "ts" against as_of.
AS_OF_COLUMNS: dict[str, tuple[str, str]] = {
    "market_bars": ("bar_date", "date"),
    "news_items": ("published_at", "ts"),
    "filing_documents": ("filed_at", "ts"),
    "corporate_catalysts": ("event_date", "date"),
    "macro_context_snapshots": ("as_of_date", "date"),
    "market_context_snapshots": ("snapshot_date", "date"),
    "liquidity_metrics": ("as_of_date", "date"),
    "score_snapshots": ("snapshot_at", "ts"),
}


class ReadOnlyStore:
    __slots__ = ("_store",)

    def __init__(self, store: Store) -> None:
        object.__setattr__(self, "_store", store)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ReadOnlyStore is immutable")

    def select(self, table: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self._store.select(table, **kwargs)

    def count(self, table: str) -> int:
        return self._store.count(table)


class AsOfStore(ReadOnlyStore):
    __slots__ = ("_as_of",)

    def __init__(self, store: Store, as_of: datetime) -> None:
        super().__init__(store)
        object.__setattr__(self, "_as_of", as_of)

    def select(self, table: str, **kwargs: Any) -> list[dict[str, Any]]:
        spec = AS_OF_COLUMNS.get(table)
        if spec:
            col, kind = spec
            bound: date | datetime = self._as_of.date() if kind == "date" else self._as_of
            lte = dict(kwargs.get("lte") or {})
            if col in lte:
                lte[col] = min(lte[col], bound) if type(lte[col]) is type(bound) else bound
            else:
                lte[col] = bound
            kwargs["lte"] = lte
        return super().select(table, **kwargs)
