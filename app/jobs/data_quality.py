"""refresh_data_quality: idempotent auto-generated issues; auto-resolves cleared ones."""

from app.jobs._util import active_securities
from app.models.enums import DataStatus
from app.services.ingestion.runner import JobContext, JobResult
from app.services.pairs.blackout import evaluate_leg, scoring_week
from app.services.validation.freshness import age_status, price_status


async def refresh_data_quality(ctx: JobContext) -> JobResult:
    issues: dict[str, dict] = {}

    def add(key, sev, typ, desc, sid=None, table=None):
        issues[key] = {
            "dedupe_key": key, "severity": sev, "issue_type": typ, "description": desc,
            "security_id": sid, "entity_table": table, "source": "auto",
            "data_status": "STALE" if "STALE" in typ else "MISSING", "resolved_at": None, "resolution_notes": None,
        }  # fmt: skip

    secs = active_securities(ctx.db)
    comps = {c["security_id"]: c for c in ctx.db.select("companies")}
    for s in secs:
        tk = s["ticker"]
        last = ctx.db.select(
            "market_bars",
            columns="bar_date",
            eq={"security_id": s["id"]},
            order="bar_date",
            desc=True,
            limit=1,
        )
        st = price_status(
            __import__("datetime").date.fromisoformat(str(last[0]["bar_date"])[:10])
            if last
            else None,
            ctx.today,
        )
        if st == DataStatus.MISSING:
            add(
                f"prices:missing:{tk}",
                "ERROR",
                "MISSING_PRICES",
                f"{tk}: no daily bars",
                s["id"],
                "market_bars",
            )
        elif st == DataStatus.STALE:
            add(
                f"prices:stale:{tk}",
                "WARNING",
                "STALE_PRICES",
                f"{tk}: latest bar {last[0]['bar_date']} (holidays can cause false positives)",
                s["id"],
                "market_bars",
            )
        if s["is_etf"]:
            continue
        c = comps.get(s["id"], {})
        if not c.get("cik"):
            add(
                f"cik:missing:{tk}",
                "WARNING",
                "MISSING_CIK",
                f"{tk}: no SEC CIK mapped",
                s["id"],
                "companies",
            )
        if not c.get("market_cap_usd"):
            add(
                f"mcap:missing:{tk}",
                "INFO",
                "MISSING_MARKET_CAP",
                f"{tk}: market cap unavailable (Yahoo best-effort)",
                s["id"],
                "companies",
            )
        liq = ctx.db.select(
            "liquidity_metrics",
            columns="as_of_date",
            eq={"security_id": s["id"]},
            order="as_of_date",
            desc=True,
            limit=1,
        )
        if not liq:
            add(
                f"liq:missing:{tk}",
                "WARNING",
                "MISSING_LIQUIDITY",
                f"{tk}: no 20d ADV estimate",
                s["id"],
                "liquidity_metrics",
            )
        elif age_status(liq[0]["as_of_date"], "liquidity", ctx.now) == DataStatus.STALE:
            add(
                f"liq:stale:{tk}",
                "WARNING",
                "STALE_LIQUIDITY",
                f"{tk}: ADV as of {liq[0]['as_of_date']}",
                s["id"],
                "liquidity_metrics",
            )
    macro = ctx.db.select("macro_context_snapshots", order="as_of_date", desc=True, limit=1)
    if not macro:
        add(
            "macro:missing",
            "WARNING",
            "MISSING_MACRO",
            "No FRED macro snapshot",
            table="macro_context_snapshots",
        )
    else:
        m = macro[0]
        for sid_, d in (m.get("series_dates") or {}).items():
            kind = "macro_monthly" if sid_ == "FEDFUNDS" else "macro_daily"
            if age_status(d, kind, ctx.now) == DataStatus.STALE:
                add(
                    f"macro:stale:{sid_}",
                    "WARNING",
                    "STALE_MACRO",
                    f"{sid_} last observation {d}",
                    table="macro_context_snapshots",
                )
        if m.get("vix") is None:
            add(
                "macro:vix",
                "INFO",
                "MISSING_MACRO",
                "VIXCLS unavailable",
                table="macro_context_snapshots",
            )
    sec_runs = ctx.db.select(
        "provider_run_logs",
        eq={"job_name": "refresh_sec_filings", "status": "SUCCEEDED"},
        order="started_at",
        desc=True,
        limit=1,
    )
    if (
        not sec_runs
        or age_status(sec_runs[0]["started_at"], "sec_run", ctx.now) != DataStatus.AVAILABLE
    ):
        add(
            "sec:stale",
            "WARNING",
            "STALE_SEC",
            "No successful SEC refresh in the last 3 days",
            table="filing_documents",
        )
    news = ctx.db.select(
        "news_items",
        columns="retrieved_at,link_confirmed",
        order="retrieved_at",
        desc=True,
        limit=200,
    )
    if not news or age_status(news[0]["retrieved_at"], "news", ctx.now) != DataStatus.AVAILABLE:
        add(
            "news:stale",
            "INFO",
            "STALE_NEWS",
            "No news refreshed in the last 7 days",
            table="news_items",
        )
    if any(not n.get("link_confirmed") for n in news):
        add(
            "news:unconfirmed",
            "INFO",
            "UNCONFIRMED_NEWS_LINKS",
            "Some news items lack a confirmed source link/timestamp",
            table="news_items",
        )
    start, end = scoring_week(ctx.today)
    brows = {
        r["security_id"]: r
        for r in ctx.db.select("security_event_blackouts", eq={"week_start": start})
    }
    by_id = {s["id"]: s for s in secs}
    members = {m["security_id"] for m in ctx.db.select("peer_pair_members")}
    for sid in members:
        if sid in by_id:
            leg = evaluate_leg(by_id[sid]["ticker"], brows.get(sid), start, end, ctx.now)
            if leg.warnings and any("verif" in w or "no blackout row" in w for w in leg.warnings):
                add(
                    f"blackout:unverified:{by_id[sid]['ticker']}:{start}",
                    "WARNING",
                    "BLACKOUT_UNVERIFIED",
                    leg.warnings[0],
                    sid,
                    "security_event_blackouts",
                )

    written = ctx.db.upsert("data_quality_issues", list(issues.values()), "dedupe_key")
    open_auto = ctx.db.select("data_quality_issues", eq={"source": "auto"}, is_null=["resolved_at"])
    resolved = [
        {"dedupe_key": o["dedupe_key"], "issue_type": o["issue_type"], "severity": o["severity"], "source": "auto",
         "resolved_at": ctx.now, "resolution_notes": "auto-resolved: condition cleared"}
        for o in open_auto if o["dedupe_key"] not in issues
    ]  # fmt: skip
    written += ctx.db.upsert("data_quality_issues", resolved, "dedupe_key")
    return JobResult(
        job="refresh_data_quality",
        rows_read=len(secs),
        rows_written=written,
        message=f"{len(issues)} open, {len(resolved)} resolved",
    )
