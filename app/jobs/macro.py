"""refresh_macro_context: FRED DGS2, DGS10, T10Y2Y, FEDFUNDS, VIXCLS (regime context only)."""

from app.models.enums import DataStatus
from app.services.ingestion.runner import JobContext, JobResult
from app.services.validation.freshness import age_status

REQUIRED = ("DGS2", "DGS10", "T10Y2Y", "FEDFUNDS")


def macro_regime(vix: float | None, curve: float | None) -> str:
    parts = []
    if vix is not None:
        parts.append("high_vol" if vix >= 25 else "elevated_vol" if vix >= 18 else "calm_vol")
    if curve is not None:
        parts.append("inverted_curve" if curve < 0 else "positive_curve")
    return "+".join(parts) or "unknown"


async def refresh_macro_context(ctx: JobContext) -> JobResult:
    fred = ctx.providers.require("fred")
    got, errors = await fred.latest_all()
    warnings = [f"{k}: {v}" for k, v in errors.items()]
    v = {k: o.value for k, o in got.items()}
    stale = [
        k for k, o in got.items()
        if age_status(o.obs_date, "macro_monthly" if k == "FEDFUNDS" else "macro_daily", ctx.now) == DataStatus.STALE
    ]  # fmt: skip
    status = DataStatus.AVAILABLE
    if any(k not in got for k in REQUIRED):
        status = DataStatus.MISSING
    elif stale:
        status = DataStatus.STALE
    row = {
        "as_of_date": ctx.today,
        "rates": {k: {"value": o.value, "date": o.obs_date} for k, o in got.items()},
        "series_dates": {k: o.obs_date for k, o in got.items()},
        "dgs2": v.get("DGS2"), "dgs10": v.get("DGS10"), "yield_curve_10y2y": v.get("T10Y2Y"),
        "fed_funds": v.get("FEDFUNDS"), "vix": v.get("VIXCLS"),
        "regime": macro_regime(v.get("VIXCLS"), v.get("T10Y2Y")),
        "summary": f"2y {v.get('DGS2')}, 10y {v.get('DGS10')}, 10y-2y {v.get('T10Y2Y')}, FFR {v.get('FEDFUNDS')}, VIX {v.get('VIXCLS')}",
        "source": "fred", "data_status": status,
    }  # fmt: skip
    if stale:
        warnings.append(f"stale series: {', '.join(stale)}")
    return JobResult(
        job="refresh_macro_context",
        rows_read=len(got),
        rows_written=ctx.db.upsert("macro_context_snapshots", [row], "as_of_date"),
        warnings=warnings,
    )
