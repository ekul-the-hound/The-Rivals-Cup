"""Shared, cached mock-populated dataset (runs the Sunday refresh against the offline mock providers)."""

import asyncio
import copy

from app.db.memory import InMemoryDB
from app.jobs import PROFILES, run_jobs
from scripts._runtime import build_context

_BASE: dict | None = None


def base_data() -> dict:
    global _BASE
    if _BASE is None:

        async def go():
            ctx = build_context(mock=True)
            try:
                await run_jobs(ctx, PROFILES["sunday"])
            finally:
                await ctx.providers.aclose()
            return ctx.db.data

        _BASE = asyncio.run(go())
    return _BASE


def fresh_db() -> InMemoryDB:
    return InMemoryDB(copy.deepcopy(base_data()))
