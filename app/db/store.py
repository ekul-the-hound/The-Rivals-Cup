"""Thin READ-ONLY data-access layer.

The `Store` protocol exposes only select/count. There is deliberately no insert/update/delete
here; admin/manual-entry write endpoints will live in a separate, explicit module later.
"""

import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

PAGE_SIZE = 1000  # PostgREST's default row cap


def select_all(
    store: "Store", table: str, order: str = "ticker", **filters: Any
) -> list[dict[str, Any]]:
    """Read every row (paging past the 1000-row cap). `order` must be a unique, stable column."""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.select(table, order=order, limit=PAGE_SIZE, offset=offset, **filters)
        out.extend(page)
        if len(page) < PAGE_SIZE:
            return out
        offset += PAGE_SIZE


def with_retry(call: Callable[[], Any], attempts: int = 3, delay: float = 0.5) -> Any:
    """Retry a read once or twice on a dropped connection (e.g. an idle HTTP/2 link the server closed).

    Only transport errors are retried; HTTP/API errors are raised immediately.
    """
    for i in range(attempts):
        try:
            return call()
        except (httpx.TransportError, httpx.TimeoutException):
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


class Store(Protocol):
    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        eq: dict[str, Any] | None = None,
        is_null: list[str] | None = None,
        order: str | None = None,
        desc: bool = False,
        limit: int | None = None,
        gte: dict[str, Any] | None = None,
        lte: dict[str, Any] | None = None,
        in_: dict[str, list[Any]] | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def count(self, table: str) -> int: ...


class SupabaseStore:
    """Store backed by a supabase-py client (user-JWT client => RLS applies)."""

    def __init__(self, client: Any) -> None:
        self._c = client

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        eq: dict[str, Any] | None = None,
        is_null: list[str] | None = None,
        order: str | None = None,
        desc: bool = False,
        limit: int | None = None,
        gte: dict[str, Any] | None = None,
        lte: dict[str, Any] | None = None,
        in_: dict[str, list[Any]] | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        q = self._c.table(table).select(columns)
        for k, v in (gte or {}).items():
            q = q.gte(k, v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in (lte or {}).items():
            q = q.lte(k, v.isoformat() if hasattr(v, "isoformat") else v)
        for k, vals in (in_ or {}).items():
            q = q.in_(k, [str(x) for x in vals])
        for k, v in (eq or {}).items():
            q = q.eq(k, v)
        for k in is_null or []:
            q = q.is_(k, "null")
        if order:
            q = q.order(order, desc=desc)
        if offset is not None:
            q = q.range(offset, offset + (limit or PAGE_SIZE) - 1)
        elif limit:
            q = q.limit(limit)
        # PostgREST caps responses (1000 rows by default); callers needing more must pass limit.
        return list(with_retry(q.execute).data or [])

    def count(self, table: str) -> int:
        res = with_retry(self._c.table(table).select("id", count="exact").limit(1).execute)
        return int(res.count or 0)


def create_supabase_client(url: str, key: str, user_jwt: str | None = None) -> Any:
    from supabase import create_client  # lazy: keeps tests/import light

    client = create_client(url, key)
    if user_jwt:
        client.postgrest.auth(user_jwt)
    return client
