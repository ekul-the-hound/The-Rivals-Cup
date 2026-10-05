"""Transparent probability model: log-odds = logit(prior) + sum(weight_i * value_i).

Every signal is scaled to [-1, +1] (positive = bullish for the stock). The weights below are
HAND-SET PRIORS chosen to be modest and to favour the evidence that research tends to find
useful (surprise history, analyst drift, insider open-market buying). They are NOT fitted and the
model has NOT been backtested; scripts/earnings_scan.py --score measures how it does on your own
logged predictions once reports have happened.
"""

import math
from dataclasses import dataclass, field

from app.earnings.extra import filing_tilt
from app.earnings.models import (
    HistoryStats,
    InsiderSummary,
    InstitutionHolding,
    PoliticianTrade,
    Signal,
)

SPECS: dict[str, tuple[str, float, str]] = {
    "beat_history": ("EPS beat history", 0.60, "finnhub"),
    "reaction_history": ("Past earnings-day reactions", 0.35, "finnhub + yahoo"),
    "analyst_level": ("Analyst consensus", 0.45, "finnhub"),
    "analyst_trend": ("Analyst trend (3 months)", 0.25, "finnhub"),
    "news": ("News tone (7 days)", 0.30, "google news"),
    "insider": ("Insider buying / selling", 0.40, "sec form 4"),
    "politicians": ("Politician trades", 0.20, "politician feed"),
    "institutions": ("Big-holder 13F change", 0.15, "sec 13f"),
    "momentum": ("20-day momentum vs SPY", 0.10, "yahoo"),
    "peers": ("Peer read-through", 0.25, "finnhub + yahoo"),
    "options_skew": ("Options put/call skew", 0.10, "yahoo options"),
    "filings": ("SEC 8-K event risk", 0.15, "sec 8-k"),
}
TOTAL_WEIGHT = sum(w for _, w, _ in SPECS.values())
TYPICAL_BEAT_RATE = 0.74  # about three in four S&P 500 reports beat, so 75% is NOT informative
P_MIN, P_MAX = 0.25, 0.75  # guardrail: no earnings setup is ever this certain


def logit(p: float) -> float:
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def make_signal(name: str, value: float | None, detail: str) -> Signal:
    label, weight, source = SPECS[name]
    if value is None:
        return Signal(
            name=name, label=label, available=False, weight=weight, detail=detail, source=source
        )
    v = round(clip(value), 4)
    return Signal(
        name=name,
        label=label,
        available=True,
        value=v,
        weight=weight,
        contribution=round(weight * v, 4),
        detail=detail,
        source=source,
    )


@dataclass
class Evidence:
    """Everything collected for one company (any field may be missing)."""

    history: HistoryStats = field(default_factory=HistoryStats)
    analyst_level: float | None = None
    analyst_trend: float | None = None
    analyst_detail: str = ""
    headlines_scores: list[int] = field(default_factory=list)
    insiders: InsiderSummary | None = None
    politicians: list[PoliticianTrade] | None = None  # None = feed not available
    institutions: list[InstitutionHolding] = field(default_factory=list)
    excess_20d: float | None = None
    peer_moves: list[float] = field(default_factory=list)
    put_call_ratio: float | None = None
    filing_events: list | None = None  # None = SEC 8-K data unavailable


def build_signals(ev: Evidence) -> list[Signal]:
    s: list[Signal] = []
    h = ev.history

    if h.beat_rate is not None and h.events_used:
        n = h.events_used
        s.append(
            make_signal(
                "beat_history",
                (h.beat_rate - TYPICAL_BEAT_RATE) / (1 - TYPICAL_BEAT_RATE) * n / (n + 4),
                f"beat EPS in {round(h.beat_rate * n)} of last {n} reports; "
                f"avg surprise {h.avg_surprise_pct}%",
            )
        )
    else:
        s.append(make_signal("beat_history", None, "no earnings history (needs FINNHUB_API_KEY)"))

    if h.p_up_hist is not None and h.reactions:
        r = h.reactions
        s.append(
            make_signal(
                "reaction_history",
                (h.p_up_hist - 0.5) * 2 * r / (r + 6),
                f"stock rose after {round(h.p_up_hist * r)} of last {r} reports; "
                f"typical move {(h.mean_abs_move or 0) * 100:.1f}%",
            )
        )
    else:
        s.append(make_signal("reaction_history", None, "no earnings-day price reactions found"))

    if ev.analyst_level is not None:
        # Analysts lean bullish by construction (typical level ~ +0.35), so centre on that.
        s.append(make_signal("analyst_level", (ev.analyst_level - 0.35) / 0.5, ev.analyst_detail))
    else:
        s.append(make_signal("analyst_level", None, ev.analyst_detail or "no analyst data"))
    if ev.analyst_trend is not None:
        s.append(
            make_signal(
                "analyst_trend", ev.analyst_trend / 0.15, f"consensus moved {ev.analyst_trend:+.2f}"
            )
        )
    else:
        s.append(make_signal("analyst_trend", None, "no analyst trend"))

    sc = ev.headlines_scores
    if sc:
        hits = [x for x in sc if x != 0]
        mean = sum(hits) / len(hits) if hits else 0.0
        depth = min(1.0, len(hits) / 5)
        s.append(
            make_signal(
                "news",
                math.tanh(2 * mean) * depth,
                f"{len(sc)} headlines, {sum(x > 0 for x in sc)} positive / {sum(x < 0 for x in sc)} negative",
            )
        )
    else:
        s.append(make_signal("news", None, "no recent headlines"))

    ins = ev.insiders
    if ins is not None:
        bull = math.tanh(ins.buy_value_usd / 500_000) * (0.5 + 0.5 * min(1.0, ins.buyers / 2))
        bear = -min(0.3, math.tanh(ins.sell_value_usd / 5_000_000) * 0.3)
        s.append(
            make_signal(
                "insider",
                bull + bear,
                f"{ins.open_market_buys} open-market buys (${ins.buy_value_usd:,.0f}, "
                f"{ins.buyers} insiders) vs {ins.open_market_sells} sells (${ins.sell_value_usd:,.0f}); "
                f"{ins.filings_seen} Form 4 filings",
            )
        )
    else:
        s.append(make_signal("insider", None, "SEC Form 4 data unavailable"))

    pol = ev.politicians
    if pol is not None:
        buyers = {t.politician for t in pol if t.side == "buy"}
        sellers = {t.politician for t in pol if t.side == "sell"}
        s.append(
            make_signal(
                "politicians",
                math.tanh((len(buyers) - len(sellers)) / 3),
                f"{len(buyers)} politicians bought, {len(sellers)} sold in the last 120 days "
                "(disclosed up to 45 days late)",
            )
        )
    else:
        s.append(
            make_signal("politicians", None, "no politician-trade feed (set POLITICIAN_TRADES_URL)")
        )

    chg = [i.change_pct for i in ev.institutions if i.change_pct is not None]
    if chg:
        mean = sum(chg) / len(chg)
        s.append(
            make_signal(
                "institutions",
                math.tanh(mean / 0.05),
                ", ".join(
                    f"{i.institution} {i.change_pct * 100:+.1f}%"
                    for i in ev.institutions
                    if i.change_pct is not None
                )
                + " (quarter over quarter, filed up to 45 days after quarter end)",
            )
        )
    else:
        s.append(make_signal("institutions", None, "no matched 13F positions"))

    if ev.excess_20d is not None:
        s.append(
            make_signal(
                "momentum",
                math.tanh(ev.excess_20d / 0.08),
                f"20-day return vs SPY {ev.excess_20d * 100:+.1f}%",
            )
        )
    else:
        s.append(make_signal("momentum", None, "no price history"))

    if len(ev.peer_moves) >= 2:
        mean = sum(ev.peer_moves) / len(ev.peer_moves)
        s.append(
            make_signal(
                "peers",
                math.tanh(mean / 0.05),
                f"{len(ev.peer_moves)} peers reported in the last 14 days, average move {mean * 100:+.1f}%",
            )
        )
    else:
        s.append(make_signal("peers", None, "no recent peer reports (second pass only)"))

    if ev.put_call_ratio is not None:
        s.append(
            make_signal(
                "options_skew",
                -math.tanh((ev.put_call_ratio - 0.8) * 1.5),
                f"put/call volume ratio {ev.put_call_ratio:.2f}",
            )
        )
    else:
        s.append(
            make_signal("options_skew", None, "options data off (set OPTIONS_IV_ENABLED=true)")
        )
    if ev.filing_events is not None:
        notes = [e.note for e in ev.filing_events if e.note]
        s.append(
            make_signal(
                "filings",
                filing_tilt(ev.filing_events),
                f"{len(ev.filing_events)} 8-K filings in the window"
                + (f"; warnings: {'; '.join(notes)}" if notes else "; none flagged"),
            )
        )
    else:
        s.append(make_signal("filings", None, "SEC 8-K data unavailable"))
    return s


def score(
    signals: list[Signal], history: HistoryStats, base_p_up: float
) -> tuple[float, float, float]:
    """Returns (p_up, confidence, log_odds)."""
    lo = logit(base_p_up) + sum(x.contribution for x in signals if x.available)
    p = clip(sigmoid(lo), P_MIN, P_MAX)
    coverage = sum(x.weight for x in signals if x.available) / TOTAL_WEIGHT
    depth = min(1.0, (history.events_used + history.reactions) / 16)
    return round(p, 4), round(0.6 * coverage + 0.4 * depth, 4), round(lo, 4)


def p_beat(signals: list[Signal]) -> float:
    v = {x.name: x.value for x in signals if x.available}
    lo = (
        logit(0.74)
        + 1.2 * v.get("beat_history", 0.0)
        + 0.5 * v.get("analyst_level", 0.0)
        + 0.25 * v.get("news", 0.0)
        + 0.3 * v.get("insider", 0.0)
    )
    return round(clip(sigmoid(lo), 0.40, 0.95), 4)


def choose_expected_move(
    hist: HistoryStats, implied: float | None, daily_sigma: float | None
) -> tuple[float, str]:
    """Typical absolute earnings-day move, preferring the market's own price (options)."""
    if implied and hist.mean_abs_move:
        return round(0.5 * implied + 0.5 * hist.mean_abs_move, 4), "options implied + history"
    if implied:
        return round(implied, 4), "options implied"
    if hist.mean_abs_move and hist.reactions >= 3:
        return round(hist.mean_abs_move, 4), f"history ({hist.reactions} reports)"
    if daily_sigma:
        return round(
            clip(2.5 * daily_sigma, 0.02, 0.20), 4
        ), "volatility fallback (2.5 x daily sigma)"
    return 0.06, "default 6% (no price data)"
