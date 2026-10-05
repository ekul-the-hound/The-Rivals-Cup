"""Writes a plain-text research packet from a Scan: everything the scan collected, per company, in
one markdown file. It exists so a person (or Claude, reading the shared folder) can review the raw
evidence behind each probability without opening the dashboard.
"""

from pathlib import Path

from app.earnings.models import Scan, TickerReport


def _pct(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.0f}"


def render_report(r: TickerReport) -> str:
    L: list[str] = []
    lean = (
        "LONG candidate"
        if r.long_candidate
        else "short candidate"
        if r.short_candidate
        else "no edge"
    )
    L.append(f"## {r.ticker}" + (f" - {r.name}" if r.name else "") + f"  [{lean}]")
    L.append(
        f"- Reports {r.report_date} ({r.week} week), session: {r.session}. "
        f"Price {r.price if r.price is not None else 'n/a'}, avg daily $ volume {_money(r.adv_usd)}."
    )
    L.append(
        f"- Model: P(up) {_pct(r.p_up)}, P(beat EPS) {_pct(r.p_beat, 0)}, confidence "
        f"{_pct(r.confidence, 0)}, typical move {_pct(r.expected_move)} ({r.move_source})."
    )
    if r.mc:
        m = r.mc
        L.append(
            f"- Monte Carlo ({m.n_paths:,} paths): expected {m.mean * 100:+.2f}%, median "
            f"{m.median * 100:+.2f}%, 5th/95th pct {m.p05 * 100:+.1f}%/{m.p95 * 100:+.1f}%, worst-5% "
            f"average {m.cvar5 * 100:+.1f}%, P(>+5%) {_pct(m.p_gain_5, 0)}, P(<-5%) "
            f"{_pct(m.p_loss_5, 0)}, P(<-10%) {_pct(m.p_loss_10, 0)}."
        )
    c = r.consensus
    if c:
        L.append(
            f"- Consensus EPS: Finnhub {c.finnhub_eps}, Nasdaq {c.nasdaq_eps} "
            f"({c.n_estimates} estimates), last year {c.last_year_eps}"
            + (
                f", sources differ by {c.disagreement_pct}%"
                if c.disagreement_pct is not None
                else ""
            )
            + f". Revenue estimate {_money(r.revenue_estimate)}."
        )
    h = r.history
    L.append(
        f"- History: beat {_pct(h.beat_rate, 0)} of last {h.events_used}; stock rose after "
        f"{_pct(h.p_up_hist, 0)} of {h.reactions} reports; last moves "
        f"{', '.join(f'{x * 100:+.1f}%' for x in h.last_moves) or 'n/a'}."
    )
    if r.short_interest:
        si = r.short_interest
        L.append(
            f"- Short interest ({si.settlement_date}): {si.shares} shares, change {si.change_pct}%, "
            f"{si.days_to_cover} days to cover."
        )
    L.append("- Signals (log-odds contribution; + favours up):")
    for s in r.signals:
        if s.available:
            L.append(f"  - {s.label}: {s.contribution:+.2f} - {s.detail}")
        else:
            L.append(f"  - {s.label}: NO DATA - {s.detail}")
    i = r.insiders
    L.append(
        f"- Insiders (SEC Form 4): {i.open_market_buys} open-market buys (${i.buy_value_usd:,.0f}, "
        f"{i.buyers} people), {i.open_market_sells} sales (${i.sell_value_usd:,.0f}, {i.sellers} people)."
    )
    if r.politicians:
        L.append("- Politician trades: " + "; ".join(
            f"{p.politician} {p.side} {p.amount_mid_usd or '?'} on {p.transaction_date}" for p in r.politicians[:8]
        ))  # fmt: skip
    if r.institutions:
        L.append("- Big holders (13F): " + "; ".join(
            f"{x.institution} {x.shares_latest:,.0f} sh ({_pct(x.change_pct)} q/q)" for x in r.institutions
        ))  # fmt: skip
    if r.filings:
        L.append("- Recent 8-Ks: " + "; ".join(
            f"{f.filed} items {','.join(f.items) or '?'}" + (f" [{f.note}]" if f.note else "") for f in r.filings[:8]
        ))  # fmt: skip
    if r.headlines:
        L.append("- Headlines (+1 positive / -1 negative by keyword):")
        for hd in r.headlines[:10]:
            when = f"{hd.published_at:%Y-%m-%d}" if hd.published_at else ""
            L.append(f"  - [{hd.score:+d}] {hd.title} ({hd.publisher or ''} {when})")
    for f in r.flags:
        L.append(f"- FLAG: {f}")
    for sg in r.suggestions:
        L.append(f"- To improve ({sg.kind}): {sg.text}")
    return "\n".join(L)


def render_packet(scan: Scan) -> str:
    L = [
        f"# Earnings research packet - {scan.generated_at:%Y-%m-%d %H:%M} UTC"
        + ("  (SYNTHETIC MOCK DATA)" if scan.mock else ""),
        f"Model {scan.model_version}. This week {scan.weeks['this']['start']}..{scan.weeks['this']['end']}"
        f" ({scan.calendar_counts['this']} reports), next week {scan.weeks['next']['start']}.."
        f"{scan.weeks['next']['end']} ({scan.calendar_counts['next']} reports). Analysed {scan.analysed}.",
        "",
        "## Data sources (what worked on this run)",
    ]
    for s in scan.sources:
        state = "not configured" if not s.configured else f"{s.ok} ok / {s.failed} failed"
        L.append(f"- {s.name}: {state}" + (f" - {s.note}" if s.note else ""))
    for w in scan.warnings:
        L.append(f"- WARNING: {w}")
    if scan.skipped:
        L.append("- Skipped: " + "; ".join(scan.skipped))
    L.append("")
    for r in scan.reports:
        L += [render_report(r), ""]
    L += ["## Suggestions to improve the model"] + [
        f"- ({s.kind}) {s.text}" for s in scan.global_suggestions
    ]
    L += ["", scan.disclaimer]
    return "\n".join(L) + "\n"


def write_packet(scan: Scan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_packet(scan), encoding="utf-8")
    return path
