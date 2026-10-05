"""Page 12: earnings reports this week and next, long-focused. Research only."""

import asyncio

import altair as alt
import pandas as pd
import streamlit as st

from app.dashboard.common import page_header
from app.earnings.models import Scan, TickerReport

GREEN, RED, GREY, BLUE = "#2e7d32", "#c62828", "#8a8f98", "#1f6feb"


@st.cache_resource(show_spinner="Building the synthetic demo scan...", ttl=3600)
def _demo_scan() -> Scan:
    from app.config.clock import utcnow
    from app.earnings.mock import EarningsMockWorld
    from app.earnings.service import run_scan
    from app.services.providers.registry import build_providers
    from scripts.earnings_scan import _mock_settings

    async def go() -> Scan:
        settings, today = _mock_settings(), utcnow().date()
        transport = EarningsMockWorld(today).transport()
        providers = build_providers(settings, transport, sleep=lambda _x: asyncio.sleep(0))
        try:
            return await run_scan(settings, providers, today, mock=True, transport=transport)
        finally:
            await providers.aclose()

    return asyncio.run(go())


def _table(rows: list[TickerReport]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": r.ticker,
                "report": r.report_date,
                "when": r.session,
                "week": r.week,
                "P(up)": r.p_up,
                "P(beat EPS)": r.p_beat,
                "confidence": r.confidence,
                "typical move": r.expected_move,
                "MC expected": r.mc.mean if r.mc else None,
                "MC 5th pct": r.mc.p05 if r.mc else None,
                "MC 95th pct": r.mc.p95 if r.mc else None,
                "long?": "LONG" if r.long_candidate else ("short" if r.short_candidate else ""),
                "flags": "; ".join(r.flags),
            }
            for r in rows
        ]
    )


def _charts(rows: list[TickerReport]) -> None:
    df = pd.DataFrame(
        [
            {
                "ticker": r.ticker,
                "P(up)": r.p_up,
                "move": r.expected_move,
                "confidence": r.confidence,
                "report": pd.Timestamp(r.report_date),
                "session": r.session,
                "group": "long candidate"
                if r.long_candidate
                else ("short candidate" if r.short_candidate else "no edge"),
            }
            for r in rows
        ]
    )
    color = alt.Color(
        "group:N",
        scale=alt.Scale(
            domain=["long candidate", "short candidate", "no edge"], range=[GREEN, RED, GREY]
        ),
        legend=alt.Legend(title=None, orient="bottom"),
    )
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Probability the stock rises after the report")
        order = df.sort_values("P(up)", ascending=False)["ticker"].tolist()
        bars = (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("ticker:N", sort=order, title=None),
                y=alt.Y("P(up):Q", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0.2, 0.8])),
                color=color,
                tooltip=[
                    "ticker",
                    alt.Tooltip("P(up):Q", format=".1%"),
                    alt.Tooltip("confidence:Q", format=".0%"),
                ],
            )
        )
        rule = (
            alt.Chart(pd.DataFrame({"y": [0.5]}))
            .mark_rule(strokeDash=[4, 4], color=GREY)
            .encode(y="y:Q")
        )
        st.altair_chart(bars + rule, use_container_width=True)
    with c2:
        st.subheader("Risk vs reward: P(up) against typical move size")
        sc = (
            alt.Chart(df)
            .mark_circle(opacity=0.85)
            .encode(
                x=alt.X("move:Q", axis=alt.Axis(format="%"), title="typical absolute move"),
                y=alt.Y("P(up):Q", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0.2, 0.8])),
                size=alt.Size("confidence:Q", scale=alt.Scale(range=[60, 500]), legend=None),
                color=color,
                tooltip=[
                    "ticker",
                    alt.Tooltip("P(up):Q", format=".1%"),
                    alt.Tooltip("move:Q", format=".1%"),
                ],
            )
        )
        st.altair_chart(sc, use_container_width=True)
    st.subheader("Reports by day")
    cal = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("yearmonthdate(report):T", title=None),
            y=alt.Y("count():Q", title="reports"),
            color=alt.Color("session:N", legend=alt.Legend(title="timing", orient="bottom")),
            tooltip=["ticker", "session"],
        )
    )
    st.altair_chart(cal, use_container_width=True)


def _detail(r: TickerReport) -> None:
    st.subheader(f"{r.ticker}" + (f" - {r.name}" if r.name else ""))
    c = st.columns(6)
    c[0].metric("P(up)", f"{r.p_up:.1%}")
    c[1].metric("P(beat EPS)", f"{r.p_beat:.0%}" if r.p_beat is not None else "n/a")
    c[2].metric("Confidence", f"{r.confidence:.0%}")
    c[3].metric("Typical move", f"{r.expected_move:.1%}", help=r.move_source)
    if r.mc:
        c[4].metric("MC expected move", f"{r.mc.mean:+.2%}")
        c[5].metric("MC worst 5% avg", f"{r.mc.cvar5:.1%}")
    for f in r.flags:
        st.warning(f)
    left, right = st.columns(2)
    with left:
        st.markdown("**Monte Carlo: first move after the report**")
        if r.mc:
            edges = r.mc.hist_edges
            hist = pd.DataFrame(
                {
                    "lo": edges[:-1],
                    "hi": edges[1:],
                    "paths": r.mc.hist_counts,
                    "side": [
                        "gain" if (a + b) / 2 > 0 else "loss"
                        for a, b in zip(edges[:-1], edges[1:], strict=True)
                    ],
                }
            )
            ch = (
                alt.Chart(hist)
                .mark_bar()
                .encode(
                    x=alt.X("lo:Q", bin="binned", axis=alt.Axis(format="%"), title="return"),
                    x2="hi:Q",
                    y=alt.Y("paths:Q"),
                    color=alt.Color(
                        "side:N",
                        scale=alt.Scale(domain=["gain", "loss"], range=[GREEN, RED]),
                        legend=None,
                    ),
                )
            )
            st.altair_chart(ch, use_container_width=True)
            st.caption(
                f"{r.mc.n_paths:,} paths. Chance of +5% or more {r.mc.p_gain_5:.0%}; "
                f"-5% or worse {r.mc.p_loss_5:.0%}; -10% or worse {r.mc.p_loss_10:.0%}. "
                f"5th / 50th / 95th percentile: {r.mc.p05:.1%} / {r.mc.median:.1%} / {r.mc.p95:.1%}."
            )
    with right:
        st.markdown("**What is pushing the probability (log-odds)**")
        sig = pd.DataFrame(
            [
                {"signal": s.label, "contribution": s.contribution, "detail": s.detail}
                for s in r.signals
                if s.available
            ]
        )
        if not sig.empty:
            ch = (
                alt.Chart(sig)
                .mark_bar()
                .encode(
                    y=alt.Y("signal:N", sort="-x", title=None),
                    x=alt.X("contribution:Q", title="toward up  >"),
                    color=alt.condition(
                        alt.datum.contribution > 0, alt.value(GREEN), alt.value(RED)
                    ),
                    tooltip=["signal", "contribution", "detail"],
                )
            )
            st.altair_chart(ch, use_container_width=True)
        missing = [s.label for s in r.signals if not s.available]
        if missing:
            st.caption("No data for: " + ", ".join(missing))
    with st.expander("Signal details"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "signal": s.label,
                        "available": s.available,
                        "value": s.value,
                        "weight": s.weight,
                        "contribution": s.contribution,
                        "source": s.source,
                        "detail": s.detail,
                    }
                    for s in r.signals
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if r.history.last_moves:
        st.markdown("**Last earnings-day moves**")
        mv = pd.DataFrame(
            {"report": range(len(r.history.last_moves), 0, -1), "move": r.history.last_moves}
        )
        st.altair_chart(
            alt.Chart(mv)
            .mark_bar()
            .encode(
                x=alt.X("report:O", title="reports ago", sort="descending"),
                y=alt.Y("move:Q", axis=alt.Axis(format="%")),
                color=alt.condition(alt.datum.move > 0, alt.value(GREEN), alt.value(RED)),
            ),
            use_container_width=True,
        )
    t1, t2, t3, t4 = st.tabs(["News", "Insiders (SEC Form 4)", "Politicians", "Big holders (13F)"])
    with t1:
        for h in r.headlines:
            icon = {1: "[+]", -1: "[-]", 0: "[ ]"}[h.score]
            st.markdown(f"{icon} [{h.title}]({h.url})" if h.url else f"{icon} {h.title}")
            st.caption(
                f"{h.publisher or ''} {h.published_at:%Y-%m-%d}"
                if h.published_at
                else h.publisher or ""
            )
        if not r.headlines:
            st.caption("No headlines found.")
    with t2:
        i = r.insiders
        st.write(
            f"{i.filings_seen} Form 4 filings. Open-market buys: {i.open_market_buys} "
            f"(${i.buy_value_usd:,.0f}, {i.buyers} insiders). Open-market sales: {i.open_market_sells} "
            f"(${i.sell_value_usd:,.0f}, {i.sellers} insiders). Sales are often pre-planned."
        )
    with t3:
        if r.politicians:
            st.dataframe(
                pd.DataFrame([p.model_dump() for p in r.politicians]),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("No politician trades found (or no feed configured: see docs/earnings.md).")
    with t4:
        if r.institutions:
            st.dataframe(
                pd.DataFrame([x.model_dump() for x in r.institutions]),
                hide_index=True,
                use_container_width=True,
            )
            st.caption("13F filings are quarterly and published up to 45 days late.")
        else:
            st.caption("No matched 13F positions.")
    if r.suggestions:
        st.markdown("**How to improve this estimate**")
        for s in r.suggestions:
            st.write(f"- ({s.kind}) {s.text}")


def page_earnings() -> None:
    from app.earnings.service import load_scan

    page_header("12. Earnings: this week and next (long-focused)")
    st.info(
        "Probability that each stock rises (or falls) after its report, with a Monte Carlo of the "
        "first move. Longs are the main goal; shorts are shown but secondary. Hand-weighted model, "
        "not backtested: use it to rank what to read about, then decide yourself."
    )
    live = load_scan()
    options = (["Latest live scan"] if live else []) + ["Synthetic demo"]
    choice = st.radio("Data", options, horizontal=True)
    scan = live if choice == "Latest live scan" and live else _demo_scan()
    if choice == "Synthetic demo":
        st.caption("SYNTHETIC demo data. Run `python -m scripts.earnings_scan` for real reports.")
    elif not live:
        st.warning("No live scan yet. Run: python -m scripts.earnings_scan")
    st.caption(f"Scan built {scan.generated_at:%Y-%m-%d %H:%M} UTC | model {scan.model_version}")

    for w in scan.warnings:
        st.warning(w)
    longs = [r for r in scan.reports if r.long_candidate]
    c = st.columns(5)
    c[0].metric("Reports this week", scan.calendar_counts.get("this", 0))
    c[1].metric("Reports next week", scan.calendar_counts.get("next", 0))
    c[2].metric("Analysed", scan.analysed)
    c[3].metric("Long candidates", len(longs))
    c[4].metric(
        "Best long P(up)", f"{max((r.p_up for r in longs), default=0):.0%}" if longs else "n/a"
    )

    f1, f2 = st.columns(2)
    week = f1.radio("Week", ["both", "this", "next"], horizontal=True)
    only_long = f2.checkbox("Long candidates only", value=False)
    rows = [
        r for r in scan.reports if week in ("both", r.week) and (r.long_candidate or not only_long)
    ]
    if not rows:
        st.info("Nothing to show for this filter.")
    else:
        st.dataframe(
            _table(rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "P(up)": st.column_config.ProgressColumn(
                    "P(up)", min_value=0.0, max_value=1.0, format="%.0f%%"
                ),
                "P(beat EPS)": st.column_config.NumberColumn(format="%.0f%%"),
                "confidence": st.column_config.NumberColumn(format="%.0f%%"),
                "typical move": st.column_config.NumberColumn(format="%.1f%%"),
                "MC expected": st.column_config.NumberColumn(format="%.2f%%"),
                "MC 5th pct": st.column_config.NumberColumn(format="%.1f%%"),
                "MC 95th pct": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        _charts(rows)
        st.divider()
        pick = st.selectbox("Open a report", [r.ticker for r in rows])
        _detail(next(r for r in rows if r.ticker == pick))

    with st.expander("Data sources: what is live right now"):
        st.dataframe(
            pd.DataFrame([s.model_dump() for s in scan.sources]),
            hide_index=True,
            use_container_width=True,
        )
    with st.expander("Suggestions to improve the estimates"):
        for s in scan.global_suggestions:
            st.write(f"- ({s.kind}) {s.text}")
    st.caption(scan.disclaimer)
