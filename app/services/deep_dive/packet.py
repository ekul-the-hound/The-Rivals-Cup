"""Per-company research packet: everything collected for one ticker, in one structure, with the
gaps listed. Built for a human (or Claude) to read when choosing longs and comparing competitors.
Read-only; it is research input, not a recommendation and not a trade instruction."""

from datetime import UTC, datetime
from typing import Any

from app.db.store import Store
from app.models.universe import DISCLAIMER
from app.services.universe.repo import load_universe


def _latest(store: Store, table: str, ticker: str, order: str, n: int = 1) -> list[dict[str, Any]]:
    return store.select(table, eq={"ticker": ticker}, order=order, desc=True, limit=n)


def analyst_score(r: dict[str, Any]) -> float | None:
    """-2 (all strong sell) .. +2 (all strong buy), from the recommendation counts."""
    n = sum(r.get(k) or 0 for k in ("strong_buy", "buy", "hold", "sell", "strong_sell"))
    if not n:
        return None
    s = (
        2 * (r.get("strong_buy") or 0)
        + (r.get("buy") or 0)
        - (r.get("sell") or 0)
        - 2 * (r.get("strong_sell") or 0)
    )
    return round(s / n, 3)


def peer_candidates(
    rows: list[dict[str, Any]], me: dict[str, Any], limit: int = 12
) -> list[dict[str, Any]]:
    """Same sector and SEC industry, pair-research eligible, ranked by closeness in dollar volume.

    Closest-in-size first, so a mega-cap is matched with mega-caps rather than micro-cap names."""
    out = [
        r for r in rows
        if r["ticker"] != me["ticker"] and r.get("sector") == me.get("sector")
        and r.get("in_pair_research_eligible")
        and (me.get("industry") and r.get("industry") == me.get("industry"))
    ]  # fmt: skip
    import math

    def _lv(r: dict[str, Any]) -> float:
        return math.log10(max(float(r.get("average_dollar_volume_20d") or 0), 1.0))

    mine = _lv(me)
    out.sort(key=lambda r: abs(_lv(r) - mine))
    return [
        {"ticker": r["ticker"], "company_name": r.get("company_name"), "industry": r.get("industry"),
         "average_dollar_volume_20d": r.get("average_dollar_volume_20d"), "market_cap": r.get("market_cap")}
        for r in out[:limit]
    ]  # fmt: skip


def build_packet(
    store: Store,
    ticker: str,
    universe: list[dict[str, Any]] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """`now` is injectable so "upcoming earnings" and generated_at are reproducible in tests."""
    now = now or datetime.now(UTC)
    tk = ticker.strip().upper().replace(".", "-")
    uni = universe if universe is not None else load_universe(store)
    me = next((r for r in uni if r["ticker"] == tk), None)
    if me is None:
        raise KeyError(tk)
    today = now.date().isoformat()
    si = _latest(store, "short_interest", tk, "settlement_date", 2)
    sv = _latest(store, "short_sale_volume_daily", tk, "trade_date", 5)
    ec = [
        r
        for r in store.select("earnings_calendar", eq={"ticker": tk}, order="earnings_date")
        if str(r["earnings_date"]) >= today
    ]
    ar = _latest(store, "analyst_recommendations", tk, "period", 2)
    fund = store.select("company_fundamentals", eq={"ticker": tk}, limit=1)
    tr = _latest(store, "earnings_transcripts", tk, "fiscal_quarter", 1)
    iv = _latest(store, "options_iv_snapshots", tk, "as_of", 1)
    filings = _latest(store, "sec_filing_feed", tk, "filed_at", 10)
    packet: dict[str, Any] = {
        "ticker": tk,
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "security": {
            k: me.get(k)
            for k in (
                "company_name",
                "exchange",
                "sector",
                "industry",
                "sector_source",
                "security_type",
                "is_adr",
                "is_reit",
                "market_cap",
                "last_price",
                "average_dollar_volume_20d",
                "liquidity_data_as_of",
                "competition_tradable_status",
                "flags",
                "exclusion_reasons",
                "review_reasons",
            )
        },  # fmt: skip
        "in_pair_research_universe": bool(me.get("in_pair_research_eligible")),
        "short_interest": {
            "latest": si[0] if si else None,
            "previous": si[1] if len(si) > 1 else None,
        },
        "short_sale_volume_recent": sv,
        "avg_short_volume_ratio_5d": round(
            sum(float(r.get("short_volume_ratio") or 0) for r in sv) / len(sv), 4
        )
        if sv
        else None,
        "next_earnings": ec[0] if ec else None,
        "analyst": {
            "latest": ar[0] if ar else None,
            "previous": ar[1] if len(ar) > 1 else None,
            "score_latest": analyst_score(ar[0]) if ar else None,
            "score_previous": analyst_score(ar[1]) if len(ar) > 1 else None,
        },  # fmt: skip
        "fundamentals": fund[0] if fund else None,
        "latest_transcript": tr[0] if tr else None,
        "options": iv[0] if iv else None,
        "recent_sec_filings": filings,
        "peer_candidates": peer_candidates(uni, me),
    }
    gaps = []
    if not si:
        gaps.append("short_interest")
    if not sv:
        gaps.append("short_sale_volume")
    if not ec:
        gaps.append("next_earnings")
    if not ar:
        gaps.append(
            "analyst_recommendations (needs FINNHUB_API_KEY and ticker in DEEP_DIVE_TICKERS)"
        )
    if not fund:
        gaps.append("fundamentals (ticker in DEEP_DIVE_TICKERS)")
    if not tr:
        gaps.append(
            "earnings_transcript (needs ALPHA_VANTAGE_API_KEY and ticker in DEEP_DIVE_TICKERS)"
        )
    if not iv:
        gaps.append("options (OPTIONS_IV_ENABLED, unofficial source)")
    packet["data_gaps"] = gaps
    return packet


def render_markdown(p: dict[str, Any]) -> str:
    s = p["security"]
    lines = [
        f"# {p['ticker']}: {s.get('company_name')}",
        f"Sector {s.get('sector')} / {s.get('industry')} | {s.get('exchange')} | price {s.get('last_price')} | "
        f"20d $ volume {s.get('average_dollar_volume_20d')} | flags: {', '.join(s.get('flags') or [])}",
        "",
        f"- Pair-research eligible: {p['in_pair_research_universe']}; tradable status (manual note): {s.get('competition_tradable_status')}",
    ]
    si = p["short_interest"]["latest"]
    if si:
        lines.append(
            f"- Short interest {si.get('settlement_date')}: {si.get('short_interest_shares')} shares, days to cover {si.get('days_to_cover')}, change {si.get('change_percent')}%"
        )
    if p["avg_short_volume_ratio_5d"] is not None:
        lines.append(f"- Short-sale volume ratio (5d avg): {p['avg_short_volume_ratio_5d']}")
    if p["next_earnings"]:
        lines.append(
            f"- Next earnings: {p['next_earnings']['earnings_date']} ({p['next_earnings'].get('time_of_day')}), EPS est {p['next_earnings'].get('eps_estimate')}"
        )
    a = p["analyst"]
    if a["latest"]:
        lines.append(
            f"- Analyst score {a['score_latest']} (prev {a['score_previous']}); counts SB/B/H/S/SS = {a['latest']['strong_buy']}/{a['latest']['buy']}/{a['latest']['hold']}/{a['latest']['sell']}/{a['latest']['strong_sell']}"
        )
    f = p["fundamentals"]
    if f:
        lines.append(
            f"- Fundamentals (FY {f.get('period_end')}): revenue {f.get('revenue')}, growth {f.get('revenue_growth_pct')}%, net income {f.get('net_income')}, EPS {f.get('eps_diluted')}, equity {f.get('stockholders_equity')}"
        )
    o = p["options"]
    if o:
        lines.append(
            f"- Options ({o.get('as_of')}): ATM IV {o.get('atm_implied_vol')}, put/call volume {o.get('put_call_volume_ratio')}"
        )
    if p["recent_sec_filings"]:
        lines.append(
            "- Recent SEC filings: "
            + "; ".join(
                f"{x.get('form_type')} {str(x.get('filed_at'))[:10]}"
                for x in p["recent_sec_filings"][:6]
            )
        )
    if p["peer_candidates"]:
        lines.append(
            "- Same-industry peers: " + ", ".join(x["ticker"] for x in p["peer_candidates"])
        )
    if p["latest_transcript"]:
        lines.append(
            f"\nTranscript excerpt ({p['latest_transcript']['fiscal_quarter']}): {p['latest_transcript'].get('excerpt', '')[:1200]}"
        )
    if p["data_gaps"]:
        lines.append("\nData gaps: " + "; ".join(p["data_gaps"]))
    lines.append(f"\n_{p['disclaimer']}_")
    return "\n".join(lines)
