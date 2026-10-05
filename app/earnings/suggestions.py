"""Plain-language ways to make each probability better, plus module-wide improvements.

The per-report suggestions are generated from what was missing or risky in that report. The global
list says which model improvements are already coded in and which still need data or time.
"""

from app.earnings.models import Suggestion, TickerReport

MISSING_SIGNAL_FIX = {
    "beat_history": "Add a free FINNHUB_API_KEY: it unlocks past EPS surprises and report dates.",
    "reaction_history": "Needs Finnhub report dates plus Yahoo prices to measure past earnings-day moves.",
    "analyst_level": "Add FINNHUB_API_KEY for the analyst buy/hold/sell counts.",
    "analyst_trend": "Add FINNHUB_API_KEY; the trend needs at least two months of analyst counts.",
    "news": "Google News returned nothing: re-run later or check the connection.",
    "insider": "Set SEC_USER_AGENT to 'Your Name your@email.com' so SEC Form 4 data can load.",
    "politicians": "Set POLITICIAN_TRADES_URL to a House/Senate trade JSON feed (see docs/earnings.md).",
    "institutions": "13F data not matched; check SEC_USER_AGENT. Holdings are quarterly and stale.",
    "momentum": "Yahoo price history failed for this ticker.",
    "peers": "Peer read-through only runs for the top names, and needs peers that reported recently.",
    "options_skew": "Set OPTIONS_IV_ENABLED=true to use options positioning (unofficial Yahoo data).",
}


def report_suggestions(r: TickerReport) -> list[Suggestion]:
    out: list[Suggestion] = []
    for s in r.signals:
        if not s.available and s.name in MISSING_SIGNAL_FIX and s.name != "peers":
            out.append(Suggestion(kind="data", text=f"{s.label}: {MISSING_SIGNAL_FIX[s.name]}"))
    if r.confidence < 0.5:
        out.append(
            Suggestion(
                kind="risk",
                text=f"Confidence is only {r.confidence:.0%}. Fill the missing data above before "
                "relying on this probability.",
            )
        )
    if abs(r.p_up - 0.5) < 0.05:
        out.append(
            Suggestion(kind="risk", text="P(up) is within 5 points of a coin flip: no edge, skip.")
        )
    if r.move_source.startswith(("volatility", "default")):
        out.append(
            Suggestion(
                kind="data",
                text="The move size is a volatility guess. Earnings history or options data would "
                "make the Monte Carlo tails far more reliable.",
            )
        )
    bull = [s for s in r.signals if s.available and s.contribution >= 0.2]
    bear = [s for s in r.signals if s.available and s.contribution <= -0.2]
    if bull and bear:
        out.append(
            Suggestion(
                kind="risk",
                text="Signals disagree ("
                + ", ".join(s.label for s in bull)
                + " vs "
                + ", ".join(s.label for s in bear)
                + "). Look at the news and filings by hand before acting.",
            )
        )
    if r.mc and r.mc.p_loss_10 >= 0.12:
        out.append(
            Suggestion(
                kind="risk",
                text=f"{r.mc.p_loss_10:.0%} of simulated paths lose more than 10%: keep the position "
                "small (this report is a large coin-flip on the gap).",
            )
        )
    if any("priced in" in f.lower() for f in r.flags):
        out.append(
            Suggestion(
                kind="risk",
                text="The stock already ran up before the report, so good news may be priced in. "
                "Longs work better when expectations are low.",
            )
        )
    when = {
        "bmo": "reports before the open: the reaction happens at the next open, so you would need to "
        "hold the position over the prior night.",
        "amc": "reports after the close: the reaction is the next session, so you would need to hold "
        "the position through the report.",
        "dmh": "reports during market hours: the reaction can happen intraday.",
    }.get(r.session)
    if when:
        out.append(Suggestion(kind="risk", text=f"{r.ticker} {when}"))
    return out


def global_suggestions(
    sources_ok: dict[str, bool], logged_predictions: int, scored_predictions: int
) -> list[Suggestion]:
    cal = (
        f"Calibration: {scored_predictions} logged predictions already scored."
        if scored_predictions
        else "Calibration: none scored yet; run the scan each week and use --score after reports."
    )
    items = [
        (
            "model",
            "[coded] Calibrate on your own results: every scan logs its predictions "
            f"({logged_predictions} logged) and `--score` reports Brier score and hit rate. " + cal,
        ),
        (
            "model",
            "[coded] Peer read-through: how peers that reported in the last 14 days moved "
            "tilts the top names (second pass).",
        ),
        (
            "model",
            "[coded] Options-implied move and put/call skew set the move size and a small "
            "direction tilt (turn on with OPTIONS_IV_ENABLED=true).",
        ),
        ("model", "[coded] Analyst level and 3-month trend (a free proxy for estimate revisions)."),
        (
            "model",
            "[coded] Insider open-market buys are weighed far more than sales, which are often "
            "pre-planned; clusters of different insiders count more.",
        ),
        ("model", "[coded] Priced-in check: a large run-up before the report is flagged."),
        (
            "data",
            "[needs data] Real estimate revisions and whisper numbers: paid feeds (Finnhub "
            "premium, Zacks, Estimize).",
        ),
        (
            "data",
            "[needs data] Short interest from FINRA is already collected by the deep-dive jobs; "
            "wiring it in would help flag squeeze setups on a beat.",
        ),
        (
            "data",
            "[needs data] Earnings-call transcript tone (Alpha Vantage, 25 calls a day) as a "
            "check on guidance language.",
        ),
        (
            "model",
            "[next] After 30+ scored reports, refit the weights with a simple logistic "
            "regression on the logged data instead of the hand-set priors.",
        ),
    ]
    missing = [k for k, ok in sources_ok.items() if not ok]
    out = [Suggestion(kind=k, text=t) for k, t in items]
    if missing:
        out.insert(
            0,
            Suggestion(
                kind="data",
                text="Data sources not working right now: "
                + ", ".join(sorted(missing))
                + ". Run `python -m scripts.earnings_scan --live-check` to see why.",
            ),
        )
    return out
