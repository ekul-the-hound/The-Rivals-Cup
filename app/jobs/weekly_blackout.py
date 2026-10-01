"""build_weekly_event_blackout_list: ensure manual blackout rows exist, compute pair eligibility."""

from datetime import timedelta

from app.services.ingestion.runner import JobContext, JobResult
from app.services.pairs.blackout import evaluate_leg, evaluate_pair, scoring_week


async def build_weekly_event_blackout_list(ctx: JobContext) -> JobResult:
    start, end = scoring_week(ctx.today)
    if ctx.week_start:
        start, end = ctx.week_start, ctx.week_start + timedelta(days=4)
    secs = {s["id"]: s for s in ctx.db.select("securities")}
    members: dict[str, list[str]] = {}
    for m in ctx.db.select("peer_pair_members"):
        members.setdefault(m["pair_id"], []).append(m["security_id"])
    pairs = [p for p in ctx.db.select("peer_pairs") if p["status"] != "REJECTED"]
    leg_ids = sorted({sid for p in pairs for sid in members.get(p["id"], [])})
    # Placeholder rows so the owner can fill them in; never overwrites manual entries.
    written = ctx.db.upsert(
        "security_event_blackouts",
        [{"security_id": sid, "week_start": start} for sid in leg_ids],
        "security_id,week_start",
        ignore_duplicates=True,
    )
    rows = {
        r["security_id"]: r
        for r in ctx.db.select("security_event_blackouts", eq={"week_start": start})
    }
    overrides = {
        o["pair_id"]: o for o in ctx.db.select("pair_blackout_overrides", eq={"week_start": start})
    }
    recent_ma = {
        c["security_id"]
        for c in ctx.db.select("corporate_catalysts", eq={"catalyst_type": "M_AND_A"}, gte={"event_date": ctx.today - timedelta(days=14)})
    }  # fmt: skip
    out = []
    for p in pairs:
        legs = [
            evaluate_leg(secs[sid]["ticker"], rows.get(sid), start, end, ctx.now)
            for sid in members.get(p["id"], [])
            if sid in secs
        ]
        ov = overrides.get(p["id"])
        e = evaluate_pair(legs, ov)
        for sid in members.get(p["id"], []):
            if sid in recent_ma:
                e.warnings.append(
                    f"{secs[sid]['ticker']}: M&A disclosure in last 14 days; verify no binary event"
                )
        out.append(
            {
                "pair_id": p["id"], "week_start": start, "eligible": e.eligible,
                "blocked_reasons": e.blocked_reasons, "warnings": e.warnings,
                "override_id": ov["id"] if ov and e.override_reason else None, "computed_at": ctx.now,
            }
        )  # fmt: skip
    ctx.db.upsert("pair_weekly_eligibility", out, "pair_id,week_start")
    n_ex = sum(1 for o in out if not o["eligible"])
    return JobResult(
        job="build_weekly_event_blackout_list", rows_read=len(pairs), rows_written=written + len(out),
        message=f"week {start}: {len(out) - n_ex} eligible, {n_ex} excluded",
    )  # fmt: skip
