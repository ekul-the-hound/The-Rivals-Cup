"""Shared dashboard plumbing. LOCAL-ONLY research UI: no auth, no network trading, no execution."""

import asyncio
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BANNER = (
    "Research only. Trades must be independently entered manually in Trader View. "
    "This system cannot place or manage trades."
)


def mode() -> str:
    return os.environ.get("DASHBOARD_MODE", "mock").lower()


@st.cache_resource(show_spinner="Loading data...")
def get_db():
    """mock (default): offline synthetic data in memory. supabase: your project via .env (local only)."""
    from scripts._runtime import build_context

    if mode() == "supabase":
        return build_context(mock=False).db

    from app.jobs import PROFILES, run_jobs

    async def go():
        ctx = build_context(mock=True)
        try:
            await run_jobs(ctx, PROFILES["sunday"])
        finally:
            await ctx.providers.aclose()
        return ctx.db

    return asyncio.run(go())


def page_header(title: str) -> None:
    """Every page calls this first so the required notice is always shown."""
    st.title(title)
    st.warning(BANNER)
    if mode() == "mock":
        st.caption(
            "MOCK MODE: synthetic offline data. Set DASHBOARD_MODE=supabase for your own data."
        )
