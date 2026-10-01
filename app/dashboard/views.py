"""The nine dashboard pages. READ-ONLY research views plus the manual journal form.

Nothing here places, queues or manages a trade. The journal only records trades you have already
entered yourself in Trader View.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.config.clock import utcnow
from app.dashboard.common import ROOT, get_db, mode, page_header
from app.services.pairs.params import EngineParams

ET = ZoneInfo("America/New_York")


def _ps(x):
    return x if isinstance(x, dict) else x.model_dump()


def _week_monday(today: date) -> date:
    return today - timedelta(days=today.weekday())


# 1 ------------------------------------------------------------------
def page_monday_builder() -> None:
    page_header("1. Monday portfolio builder")
    from app.services.pairs.portfolio import build_weekly_portfolio

    db = get_db()
    now = utcnow()
    c1, c2 = st.columns(2)
    with c1:
        max_pairs = st.slider("Max pairs", 1, 6, 6)
    with c2:
        pv = st.number_input(
            "Portfolio value assumption (USD)", 10_000.0, 100_000_000.0, 100_000.0, 10_000.0
        )
    pf = build_weekly_portfolio(db, now, EngineParams(max_pairs=max_pairs, portfolio_value_usd=pv))
    st.subheader(f"Research draft for week of {pf.week_start}")
    st.info(
        "RESEARCH DRAFT. Nothing here is an order or a recommendation to trade; you decide and enter manually."
    )
    rows = [
        {
            "rank": s.rank,
            "pair": s.name,
            "long (momentum rule)": s.long_ticker,
            "short": s.short_ticker,
            "score": s.pair_quality_score,
            "adjusted": s.adjusted_score,
            "max gross % (both legs)": s.suggested_max_gross_pct,
        }
        for s in pf.selected
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    for w in pf.warnings:
        st.warning(w)
    with st.expander("Excluded pairs and reasons"):
        st.dataframe(
            pd.DataFrame(
                [
                    {"pair": e.name, "reasons": "; ".join(f"{r['code']}" for r in e.reasons)}
                    for e in pf.excluded
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    with st.expander("Alternates"):
        st.dataframe(
            pd.DataFrame(
                [
                    a.model_dump(include={"rank", "name", "pair_quality_score"})
                    for a in pf.alternates
                ]
            ),
            hide_index=True,
        )
    st.caption(pf.note)


# 2 ------------------------------------------------------------------
def page_candidates() -> None:
    page_header("2. Peer-pair candidates")
    from app.services.pairs.portfolio import compute_evaluations

    evals, start, end = compute_evaluations(get_db(), utcnow(), EngineParams())
    st.caption(f"Scoring week {start} to {end}")
    only = st.checkbox("Eligible only", value=False)
    rows = [
        {
            "pair": e.name,
            "eligible": e.eligible,
            "score": e.score,
            "data quality": e.data_quality_score,
            "blackout": e.packet.event_blackout.get("status"),
            "exclusion reasons": ", ".join(r.code for r in e.ineligible_reasons),
        }
        for e in sorted(evals, key=lambda x: -x.score)
        if e.eligible or not only
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# 3 ------------------------------------------------------------------
def page_pair_detail() -> None:
    page_header("3. Pair research detail")
    from app.services.pairs.portfolio import compute_evaluations

    evals, _, _ = compute_evaluations(get_db(), utcnow(), EngineParams())
    by_name = {e.name: e for e in evals}
    if not by_name:
        st.info("No active pairs.")
        return
    name = st.selectbox("Pair", sorted(by_name))
    e = by_name[name]
    st.metric("Pair quality score", e.score)
    st.write(
        "Eligible"
        if e.eligible
        else "NOT eligible: " + ", ".join(r.message for r in e.ineligible_reasons)
    )
    pk = e.packet
    st.subheader("Why it ranks")
    for r in pk.top_reasons:
        st.write(f"- {r}")
    st.subheader("Counter-thesis")
    for r in pk.counter_thesis:
        st.write(f"- {r}")
    st.subheader("Concepts (not orders)")
    st.write(pk.invalidation_concept)
    st.write(pk.target_concept)
    st.subheader("Reference zones")
    st.dataframe(pd.DataFrame([z.model_dump() for z in pk.entry_zones]), hide_index=True)
    st.subheader("Manual checklist")
    for c in pk.manual_checklist:
        st.checkbox(c, key=f"chk_{name}_{c[:30]}")
    with st.expander("Metrics / risk flags / missing data"):
        st.json(
            {
                "metrics": pk.metrics,
                "risk_flags": pk.risk_flags,
                "missing_or_stale": pk.missing_or_stale,
            },
            expanded=False,
        )


# 4 ------------------------------------------------------------------
def page_market() -> None:
    page_header("4. Market and sector dashboard")
    from app.services.features.metrics import close_series, trailing_returns

    db = get_db()
    rows = []
    for s in db.select("securities"):
        if not s.get("is_etf") or not (s.get("is_benchmark") or s.get("sector")):
            continue
        bars = db.select(
            "market_bars",
            eq={"security_id": s["id"], "timeframe": "1D"},
            order="bar_date",
            desc=True,
            limit=70,
        )
        if not bars:
            continue
        cs = close_series(sorted(bars, key=lambda b: str(b["bar_date"])))
        r = trailing_returns(cs, (1, 5, 20, 60))
        rows.append(
            {
                "etf": s["ticker"],
                "sector": s.get("sector") or "benchmark",
                "1d %": None if r[1] is None else round(r[1] * 100, 2),
                "5d %": None if r[5] is None else round(r[5] * 100, 2),
                "20d %": None if r[20] is None else round(r[20] * 100, 2),
                "60d %": None if r[60] is None else round(r[60] * 100, 2),
                "last bar": cs.index[-1],
            }
        )
    st.dataframe(
        pd.DataFrame(rows).sort_values("20d %", ascending=False) if rows else pd.DataFrame(),
        width="stretch",
        hide_index=True,
    )
    mk = db.select("market_context_snapshots", order="snapshot_date", desc=True, limit=1)
    mc = db.select("macro_context_snapshots", order="as_of_date", desc=True, limit=1)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Market context")
        st.json(mk[0] if mk else {"status": "no snapshot"}, expanded=False)
    with c2:
        st.subheader("Macro context")
        st.json(mc[0] if mc else {"status": "no snapshot"}, expanded=False)


# 5 ------------------------------------------------------------------
def _ts(d: date, t) -> datetime:
    return datetime.combine(d, t, tzinfo=ET)


def page_journal() -> None:
    page_header("5. Manual portfolio journal")
    from app.services.journal.records import (
        JournalError,
        LegEntry,
        ensure_portfolio,
        record_manual_exit,
        record_manual_pair,
    )

    db = get_db()
    st.info(
        "This journal only RECORDS trades you already entered yourself in Trader View. "
        "A recommended or reviewed pair is never assumed to be traded, and WSR orders are never imported."
    )
    if mode() != "supabase":
        st.caption("Mock mode: records go to the in-memory mock database only.")
    pid = ensure_portfolio(db)
    tickers = sorted(s["ticker"] for s in db.select("securities") if not s.get("is_benchmark"))
    secs = {s["id"]: s["ticker"] for s in db.select("securities")}

    with st.form("pair_form"):
        st.subheader("Record a pair (two separate legs, linked)")
        name = st.text_input("Pair name", "KO / PEP")
        peer = st.selectbox(
            "Link to research pair (optional)",
            ["(none)"] + [p["name"] for p in db.select("peer_pairs")],
        )
        d = st.date_input("Entry date (ET)", date.today())
        t = st.time_input(
            "Entry time (ET)", datetime.now(ET).time().replace(second=0, microsecond=0)
        )
        cl, cs_ = st.columns(2)
        with cl:
            lt = st.selectbox("Long ticker", tickers, key="lt")
            lq = st.number_input("Long quantity", 0.0, 1e9, 100.0, key="lq")
            lp = st.number_input("Long entry price", 0.0, 1e7, 0.0, key="lp")
        with cs_:
            stt = st.selectbox("Short ticker", tickers, index=min(1, len(tickers) - 1), key="st")
            sq = st.number_input("Short quantity", 0.0, 1e9, 100.0, key="sq")
            sp = st.number_input("Short entry price", 0.0, 1e7, 0.0, key="sp")
        stop = st.text_input("Stop concept (your own words)")
        target = st.text_input("Target concept (your own words)")
        notes = st.text_area("Notes")
        confirm = st.checkbox("I have ALREADY entered both legs myself in Trader View.")
        go = st.form_submit_button("Record in journal")
    if go:
        try:
            peer_id = next((p["id"] for p in db.select("peer_pairs") if p["name"] == peer), None)
            ts = _ts(d, t)

            def mk(tk, side, q, px):
                return LegEntry(
                    ticker=tk,
                    side=side,
                    quantity=q,
                    entry_price=px,
                    entered_at=ts,
                    notes=notes or None,
                    stop_concept=stop or None,
                    target_concept=target or None,
                )

            out = record_manual_pair(
                db,
                portfolio_id=pid,
                name=name,
                long_leg=mk(lt, "LONG", lq, lp),
                short_leg=mk(stt, "SHORT", sq, sp),
                confirmed_entered_manually=confirm,
                peer_pair_id=peer_id,
                notes=notes or None,
            )
            st.success("Recorded. " + out["notice"])
        except (JournalError, ValueError) as e:
            st.error(str(e))

    st.subheader("Open recorded legs")
    open_rows = [
        r for r in db.select("manual_positions", eq={"portfolio_id": pid}) if r.get("is_open", True)
    ]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "id": r["id"],
                    "ticker": secs.get(r["security_id"]),
                    "side": r["side"],
                    "qty": r["quantity"],
                    "entry": r["avg_entry_price"],
                    "opened": r.get("opened_at"),
                    "stop concept": r.get("stop_concept"),
                    "target concept": r.get("target_concept"),
                }
                for r in open_rows
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    if open_rows:
        with st.form("exit_form"):
            st.subheader("Record a manual exit (you already exited in Trader View)")
            label = {
                f"{secs.get(r['security_id'])} {r['side']} {r['quantity']:g} ({r['id'][:8]})": r[
                    "id"
                ]
                for r in open_rows
            }
            pick = st.selectbox("Leg", list(label))
            xp = st.number_input("Exit price", 0.0, 1e7, 0.0)
            xd = st.date_input("Exit date (ET)", date.today(), key="xd")
            xt = st.time_input(
                "Exit time (ET)", datetime.now(ET).time().replace(second=0, microsecond=0), key="xt"
            )
            why = st.text_input("Reason")
            xc = st.checkbox("I have ALREADY exited this leg myself in Trader View.")
            gox = st.form_submit_button("Record exit")
        if gox:
            try:
                record_manual_exit(
                    db,
                    position_id=label[pick],
                    exit_price=xp,
                    exited_at=_ts(xd, xt),
                    reason=why,
                    confirmed_entered_manually=xc,
                )
                st.success("Exit recorded.")
            except JournalError as e:
                st.error(str(e))
    st.subheader("Recent journal trades")
    tr = db.select(
        "manual_trades", eq={"portfolio_id": pid}, order="traded_at", desc=True, limit=20
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "when": x["traded_at"],
                    "ticker": secs.get(x["security_id"]),
                    "side": x["side"],
                    "action": x["action"],
                    "qty": x["quantity"],
                    "price": x["price"],
                }
                for x in tr
            ]
        ),
        hide_index=True,
    )


# 6 ------------------------------------------------------------------
def page_score() -> None:
    page_header("6. Estimated score and exposure")
    from app.services.journal.records import ensure_portfolio
    from app.services.scoring.wsr import (
        AUTHORITY,
        METHODOLOGY,
        estimate_portfolio,
        save_score_snapshot,
    )

    db = get_db()
    pid = ensure_portfolio(db)
    st.error("ESTIMATED. " + AUTHORITY)
    wk = st.date_input("Weekly round (Monday)", _week_monday(date.today()))
    wk = wk - timedelta(days=wk.weekday())
    as_of = st.date_input("As of", min(date.today(), wk + timedelta(days=4)))
    est = estimate_portfolio(db, pid, wk, as_of)
    c = st.columns(4)
    c[0].metric("ESTIMATED total return R (%)", f"{est.total_return_pct:.3f}")
    c[1].metric("ESTIMATED DD (%)", f"{est.downside_deviation_pct:.3f}")
    c[2].metric("ESTIMATED PlayerScore", f"{est.player_score:.3f}")
    c[3].metric("Days valued / scheduled", f"{est.days_valued}/{est.scheduled_days}")
    g = st.columns(3)
    g[0].metric("Modeled gross exposure (latest, % of V)", f"{est.modeled_gross_exposure_pct:.1f}")
    g[1].metric(
        "Modeled gross exposure (peak, % of V)", f"{est.modeled_gross_exposure_peak_pct:.1f}"
    )
    g[2].metric(
        "Manually recorded gross (cost basis, USD)", f"{est.manual_recorded_gross_usd:,.0f}"
    )
    st.caption(
        "Modeled gross is marked to market inside this estimate; manual recorded gross is cost basis of the legs you recorded. They are separate numbers."
    )
    for w in est.warnings:
        st.warning(f"{w.code}: {w.message}")
    st.subheader("Daily values (ESTIMATED)")
    st.dataframe(
        pd.DataFrame([d.model_dump() for d in est.daily]), width="stretch", hide_index=True
    )
    st.subheader(
        "Liquidity (ESTIMATED 1% of trailing 20-day dollar volume; WSR data authoritative)"
    )
    st.dataframe(pd.DataFrame(est.liquidity), width="stretch", hide_index=True)
    if st.button("Save this ESTIMATE as a snapshot"):
        save_score_snapshot(db, pid, est)
        st.success("Estimate snapshot saved (is_estimate = true).")
    with st.expander("Methodology"):
        st.write(METHODOLOGY)


# 7 ------------------------------------------------------------------
def page_quality() -> None:
    page_header("7. Data quality")
    from app.services.monitoring.status import get_data_quality, get_research_status

    db = get_db()
    dq = get_data_quality(db)
    st.metric("Open data-quality issues", dq.open_issue_count)
    st.dataframe(
        pd.DataFrame([i.model_dump(mode="json") for i in dq.issues]),
        width="stretch",
        hide_index=True,
    )
    rs = get_research_status(db)
    st.subheader("Row counts")
    st.json(rs.counts, expanded=False)
    for n in rs.notes:
        st.write(f"- {n}")
    st.subheader("Latest bar per security (staleness)")
    rows = []
    for s in db.select("securities"):
        b = db.select(
            "market_bars",
            eq={"security_id": s["id"], "timeframe": "1D"},
            order="bar_date",
            desc=True,
            limit=1,
        )
        if b:
            last = date.fromisoformat(str(b[0]["bar_date"])[:10])
            rows.append(
                {"ticker": s["ticker"], "last bar": last, "days old": (date.today() - last).days}
            )
    st.dataframe(
        pd.DataFrame(rows).sort_values("days old", ascending=False) if rows else pd.DataFrame(),
        hide_index=True,
    )
    st.info(
        "Dividend data: if `dividend_events` is empty, estimates carry a DIVIDENDS_NOT_CHECKED warning."
    )
    st.caption(f"dividend_events rows: {db.count('dividend_events')}")


# 8 ------------------------------------------------------------------
def read_audit_lines(path: str, limit: int = 200) -> list[dict]:
    p = Path(path)
    if not path or not p.is_file():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]:
        i = line.find("{")
        if i < 0:
            continue
        try:
            out.append(json.loads(line[i:]))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


def page_mcp() -> None:
    page_header("8. MCP status and audit")
    from app.config import get_settings
    from app.mcp.registry import TOOLS

    try:
        s = get_settings()
        url, dev = s.mcp_public_url or "(not configured)", s.mcp_dev_token_enabled
    except Exception:  # missing .env in mock mode
        url, dev = "(settings not loaded)", False
    st.write(f"Public MCP URL: `{url}`")
    st.write(
        "Server: ONE read-only MCP endpoint (`POST /mcp`). It exposes no write tools and cannot trade."
    )
    if dev:
        st.warning("MCP_DEV_BEARER_TOKEN is set: DEVELOPMENT ONLY. Unset it in production.")
    st.dataframe(
        pd.DataFrame([{"tool": t.name, "read-only": True, "cost units": t.cost} for t in TOOLS]),
        width="stretch",
        hide_index=True,
    )
    path = os.environ.get("MCP_AUDIT_LOG_FILE", "")
    st.write(f"Audit log file (`MCP_AUDIT_LOG_FILE`): `{path or 'not set'}`")
    rows = read_audit_lines(path)
    if rows:
        st.dataframe(pd.DataFrame(rows[::-1]), width="stretch", hide_index=True)
    else:
        st.info(
            "No audit lines found. Run the MCP server with stderr redirected to that file (see docs/mcp_claude_web_setup.md)."
        )
    st.caption(
        "Audit lines hold metadata only (request id, tool, argument hash, status, duration). No tokens, arguments or content."
    )


# 9 ------------------------------------------------------------------
def page_compliance() -> None:
    page_header("9. Compliance status")
    from app.compliance.rules import audit
    from app.services.monitoring.status import get_controls_status

    cs = get_controls_status(get_db())
    c = st.columns(3)
    c[0].metric("Mode", cs.mode.value if hasattr(cs.mode, "value") else str(cs.mode))
    c[1].metric("Signal sending enabled", str(cs.signal_sending_enabled))
    c[2].metric("Execution code present", str(cs.execution_code_present))
    st.subheader("Prohibited capabilities (permanently absent)")
    for p in cs.prohibited_capabilities:
        st.write(f"- {p}")
    st.subheader("Static repository audit")
    rep = audit(ROOT)
    if rep.ok:
        st.success(
            f"PASS: no execution or platform-integration code found ({rep.files_scanned} files scanned)."
        )
    else:
        st.error(f"FAIL: {len(rep.findings)} finding(s)")
    st.code(rep.render())
