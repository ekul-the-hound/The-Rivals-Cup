"""Build / merge `security_master` rows. Writes only to universe tables (see UniverseWriteGuard).

Upserts are grouped by identical column sets so a partial update can never reset a column that
was not part of it (PostgREST fills missing keys with column defaults on bulk upserts).
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.db.writer import DB
from app.services.providers.nasdaq_trader import Listing
from app.services.universe.classify import classify_listing, normalize_symbol

UNIVERSE_TABLES = {"security_master", "security_master_view_state", "security_master_audit"}


class UniverseWriteViolation(RuntimeError):
    pass


class UniverseWriteGuard:
    """Read-through DB wrapper that only allows upserts to the three universe tables (and, for
    security_master, only to an optional column allow-list). Not a trade/order/execution path."""

    def __init__(self, db: DB, master_columns: set[str] | None = None) -> None:
        self._db = db
        self._cols = master_columns

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
        if table not in UNIVERSE_TABLES:
            raise UniverseWriteViolation(
                f"write to '{table}' is not permitted from the universe builder"
            )
        if table == "security_master" and self._cols is not None:
            for r in rows:
                extra = set(r) - self._cols - {"ticker"}
                if extra:
                    raise UniverseWriteViolation(f"columns not allowed here: {sorted(extra)}")
        return self._db.upsert(table, rows, on_conflict, ignore_duplicates)


def upsert_uniform(
    db: DB, table: str, rows: list[dict[str, Any]], on_conflict: str, chunk: int = 500
) -> int:
    """Upsert rows grouped by their exact key set (never mixes partial and full rows)."""
    groups: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[frozenset(r)].append(r)
    written = 0
    for batch in groups.values():
        for i in range(0, len(batch), chunk):
            written += db.upsert(table, batch[i : i + chunk], on_conflict)
    return written


NEW_ROW_DEFAULTS: dict[str, Any] = {
    "sector": None, "sector_data_status": "MISSING", "competition_tradable_status": "UNKNOWN",
    "manually_overridden": False, "is_otc": False, "security_type_source": "DERIVED",
}  # fmt: skip


def listing_rows(
    listings: list[Listing],
    existing: dict[str, dict[str, Any]],
    cik_map: dict[str, Any],
    asof: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """-> (rows to upsert, number of listings that were new). Manual choices are never overwritten."""
    out: list[dict[str, Any]] = []
    new = 0
    for li in listings:
        tk = normalize_symbol(li.symbol)
        cls = classify_listing(li.symbol, li.name, li.etf, li.test_issue)
        info = cik_map.get(tk)
        row: dict[str, Any] = {
            "ticker": tk,
            "company_name": li.name,
            "exchange": li.exchange,
            "listing_source": li.listing_source,
            "listing_verified_at": asof,
            "is_active": True,
            "is_test_issue": cls.is_test_issue,
            "listing_financial_status": li.financial_status,
            "cik": str(info.cik).zfill(10) if info else None,
            "sec_company_name": info.title if info else None,
        }
        prev = existing.get(tk)
        if prev is None:
            new += 1
            row.update(NEW_ROW_DEFAULTS)
        manual_type = bool(prev and prev.get("security_type_source") == "MANUAL")
        if not manual_type:
            row.update(
                security_type=cls.security_type.value,
                is_common_stock=cls.is_common_stock,
                is_adr=cls.is_adr,
                is_etf=cls.is_etf,
                is_leveraged_product=cls.is_leveraged_product,
                is_preferred=cls.is_preferred,
                is_warrant=cls.is_warrant,
                is_right=cls.is_right,
                is_unit=cls.is_unit,
            )
        row["is_spac"] = cls.is_spac
        row["is_reit"] = bool((prev or {}).get("is_reit")) or cls.is_reit_by_name
        out.append(row)
    return out, new


def inactive_rows(
    existing: dict[str, dict[str, Any]], current: set[str], asof: datetime
) -> list[dict[str, Any]]:
    """Active rows that came from a Nasdaq Trader directory but are no longer listed."""
    return [
        {"ticker": tk, "is_active": False, "listing_verified_at": asof}
        for tk, r in existing.items()
        if r.get("is_active", True)
        and str(r.get("listing_source", "")).startswith("nasdaqtrader")
        and tk not in current
    ]
