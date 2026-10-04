"""Leaders & laggards candidate book (read-only research).

GET only. The book lists, per target sector, the strongest stock (long candidate) and the weakest of
its direct competitors (short candidate). It is a research ranking: it never places, queues or
records a trade, and it does not confirm Trader View availability.
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.api.deps import get_store
from app.config import Settings, get_settings
from app.config.clock import utcnow
from app.db.store import Store
from app.services.leaders.book import BookParams, build_book, render_markdown

router = APIRouter(prefix="/leaders-laggards", tags=["leaders & laggards (research only)"])


def _latest(store: Store) -> dict[str, Any] | None:
    rows = store.select("leader_laggard_books", order="scoring_week_start", desc=True, limit=1)
    return rows[0]["book"] if rows else None


def _book(store: Store, settings: Settings, live: bool) -> dict[str, Any]:
    if not live:
        b = _latest(store)
        if b:
            return b
    today: date = utcnow().date()
    b = build_book(store, today, BookParams.from_settings(settings, today))
    if not b["pairs"] and not live:
        raise HTTPException(
            404,
            "no book yet: run `python -m scripts.refresh_weekly_research --profile leaders`",
        )
    return b


@router.get("")
def leaders_laggards(
    live: bool = False,
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Latest stored book; `live=true` recomputes from stored price history (slower)."""
    return _book(store, settings, live)


@router.get("/markdown", response_class=PlainTextResponse)
def leaders_laggards_markdown(
    live: bool = False,
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> str:
    return render_markdown(_book(store, settings, live))
