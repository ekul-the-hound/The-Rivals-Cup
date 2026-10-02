"""Upsert-only write path for ingestion jobs (service role). No delete, no raw update.

The read-only `Store` protocol (app/db/store.py) is what the API uses. Ingestion needs `DB`,
which adds `upsert` only.
"""

import math
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from app.db.store import Store, SupabaseStore, with_retry


class DB(Store, Protocol):
    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: str,
        ignore_duplicates: bool = False,
    ) -> int: ...


def jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    return value


class SupabaseDB(SupabaseStore):
    """SupabaseStore + idempotent upsert. Build with the SERVICE-ROLE client, server-side only."""

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: str,
        ignore_duplicates: bool = False,
    ) -> int:
        if not rows:
            return 0
        payload = [jsonable(r) for r in rows]
        for i in range(0, len(payload), 500):
            with_retry(  # upserts are idempotent, so retrying a dropped connection is safe
                self._c.table(table)
                .upsert(
                    payload[i : i + 500],
                    on_conflict=on_conflict,
                    ignore_duplicates=ignore_duplicates,
                    default_to_null=False,  # don't null columns omitted from the payload
                )
                .execute
            )
        return len(payload)
