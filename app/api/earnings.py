"""Read-only earnings endpoints. They serve the last saved scan (data/generated/earnings/latest.json,
written by `python -m scripts.earnings_scan`); nothing here places or manages a trade."""

from fastapi import APIRouter, HTTPException, Query

from app.earnings.models import Scan, TickerReport
from app.earnings.service import load_scan

router = APIRouter(prefix="/earnings", tags=["earnings"])

_MISSING = (
    "No earnings scan saved yet. Run: python -m scripts.earnings_scan (or --mock for a demo)."
)


def _scan() -> Scan:
    scan = load_scan()
    if scan is None:
        raise HTTPException(status_code=404, detail=_MISSING)
    return scan


@router.get("/week", response_model=Scan)
def earnings_week(
    week: str = Query("both", pattern="^(this|next|both)$"),
    long_only: bool = False,
) -> Scan:
    scan = _scan()
    rows = [
        r for r in scan.reports if week in ("both", r.week) and (r.long_candidate or not long_only)
    ]
    return scan.model_copy(update={"reports": rows})


@router.get("/{ticker}", response_model=TickerReport)
def earnings_ticker(ticker: str) -> TickerReport:
    t = ticker.upper().replace(".", "-")
    for r in _scan().reports:
        if r.ticker == t:
            return r
    raise HTTPException(status_code=404, detail=f"{t} is not in the latest earnings scan")
