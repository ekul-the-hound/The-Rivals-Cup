"""Target-sector research universe endpoints.

GETs are read-only (user JWT, RLS). The two POSTs change research metadata only
(MANUALLY_VERIFIED / MANUALLY_REJECTED notes, sector / industry / security-type corrections) and are
audited. Nothing here contacts WSR, Trader View, a browser, a broker or any execution system.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_admin_db, get_store
from app.config import Settings, get_settings
from app.config.clock import utcnow
from app.db.store import Store
from app.db.writer import DB
from app.models.universe import DISCLAIMER, TARGET_SECTORS
from app.services.deep_dive.packet import build_packet
from app.services.universe.export import export_records, render_csv, render_json
from app.services.universe.overrides import (
    InvalidChange,
    UnknownTicker,
    manual_override,
    manual_verify,
)
from app.services.universe.repo import filter_rows, load_universe
from app.services.universe.summary import summarize
from app.services.universe.views import UniverseParams

router = APIRouter(prefix="/universe", tags=["universe (research only)"])
ViewName = Literal["all", "pair_research", "manual_review"]


class VerifyRequest(BaseModel):
    status: Literal["UNKNOWN", "MANUALLY_VERIFIED", "MANUALLY_REJECTED"]
    note: str | None = Field(default=None, max_length=1000)


class OverrideRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    sector: str | None = None
    industry: str | None = None
    security_type: str | None = None
    is_reit: bool | None = None
    earnings_date_if_known: str | None = None
    known_major_event_date: str | None = None
    event_risk_notes: str | None = None


def _filters(
    sector: str | None, exchange: str | None, security_type: str | None, adr: bool | None,
    tradable_status: str | None, min_adv: float | None, q: str | None,
) -> dict[str, Any]:  # fmt: skip
    if sector and sector.upper() not in TARGET_SECTORS:
        raise HTTPException(422, f"sector must be one of {list(TARGET_SECTORS)}")
    return {"sector": sector, "exchange": exchange, "security_type": security_type, "adr": adr,
            "tradable_status": tradable_status, "min_adv": min_adv, "q": q}  # fmt: skip


@router.get("/target-sectors")
def list_target_sector_universe(
    view: ViewName = "all",
    sector: str | None = None,
    exchange: str | None = None,
    security_type: str | None = None,
    adr: bool | None = None,
    tradable_status: str | None = None,
    min_adv: float | None = None,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    rows = filter_rows(
        load_universe(store),
        view,
        **_filters(sector, exchange, security_type, adr, tradable_status, min_adv, q),
    )
    recs = export_records(rows[offset : offset + limit], utcnow())
    return {"view": view, "total": len(rows), "limit": limit, "offset": offset,
            "disclaimer": DISCLAIMER, "records": recs}  # fmt: skip


@router.get("/target-sectors/export")
def export_target_sector_universe(
    format: Literal["csv", "json"] = "csv",
    view: ViewName = "all",
    sector: str | None = None,
    store: Store = Depends(get_store),
) -> Response:
    now = utcnow()
    rows = filter_rows(
        load_universe(store), view, **_filters(sector, None, None, None, None, None, None)
    )
    recs = export_records(rows, now)
    if format == "json":
        return Response(render_json(recs, now, view), media_type="application/json")
    return Response(
        render_csv(recs),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=us_target_sector_universe.csv"},
    )


@router.get("/target-sectors/summary")
def target_sector_summary(store: Store = Depends(get_store)) -> dict[str, Any]:
    return {"disclaimer": DISCLAIMER, **summarize(load_universe(store))}


@router.get("/manual-review")
def manual_review_queue(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    rows = filter_rows(load_universe(store), "manual_review")
    return {"total": len(rows), "disclaimer": DISCLAIMER,
            "records": export_records(rows[offset : offset + limit], utcnow())}  # fmt: skip


@router.get("/{ticker}/research-packet")
def research_packet(ticker: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    """All collected research data for one company, with gaps listed. Read-only."""
    try:
        return build_packet(store, ticker)
    except KeyError as exc:
        raise HTTPException(404, f"unknown ticker '{ticker}'") from exc


@router.post("/{ticker}/manual-verify")
def post_manual_verify(
    ticker: str,
    body: VerifyRequest,
    db: DB = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Record a manual note. Research metadata only; nothing is sent to WSR/Trader View."""
    try:
        return manual_verify(
            db, ticker, body.status, body.note, utcnow(), UniverseParams.from_settings(settings)
        )
    except UnknownTicker as exc:
        raise HTTPException(404, str(exc)) from exc
    except InvalidChange as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{ticker}/manual-override")
def post_manual_override(
    ticker: str,
    body: OverrideRequest,
    db: DB = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    changes = body.model_dump(exclude={"reason"}, exclude_none=True)
    try:
        return manual_override(
            db, ticker, changes, body.reason, utcnow(), UniverseParams.from_settings(settings)
        )
    except UnknownTicker as exc:
        raise HTTPException(404, str(exc)) from exc
    except InvalidChange as exc:
        raise HTTPException(400, str(exc)) from exc
