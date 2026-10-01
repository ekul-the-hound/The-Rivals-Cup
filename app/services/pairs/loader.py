"""DB -> PairInputs. READ-ONLY (uses the Store protocol; no writes)."""

from datetime import date, datetime, timedelta
from typing import Any

from app.db.store import Store
from app.models.enums import PairStatus
from app.services.pairs.blackout import scoring_week
from app.services.pairs.models import LegInputs, PairInputs

ACTIVE_STATUSES = {PairStatus.CANDIDATE.value, PairStatus.APPROVED_FOR_REVIEW.value}


def _bars(store: Store, sid: str, limit: int = 130) -> list[dict[str, Any]]:
    rows = store.select(
        "market_bars",
        eq={"security_id": sid, "timeframe": "1D"},
        order="bar_date",
        desc=True,
        limit=limit,
    )
    return sorted(rows, key=lambda r: str(r["bar_date"]))


def _by_security(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("security_id"):
            out.setdefault(r["security_id"], []).append(r)
    return out


def load_week_inputs(
    store: Store,
    now: datetime,
    week_start: date | None = None,
    pair_ids: list[str] | None = None,
    include_all_statuses: bool = False,
) -> tuple[list[PairInputs], date, date]:
    """Load everything the engine needs for every active pair for the scoring week."""
    start, end = scoring_week(week_start or now.date())
    if week_start is not None:
        start, end = week_start, week_start + timedelta(days=4)
    today = now.date()

    secs = {s["id"]: s for s in store.select("securities")}
    by_ticker = {s["ticker"]: s for s in secs.values()}
    comps = {c["security_id"]: c for c in store.select("companies")}
    etf_by_sector = {
        m["sector"]: m["etf_security_id"]
        for m in store.select("sector_etf_mappings")
        if m.get("is_primary", True)
    }
    members: dict[str, list[dict[str, Any]]] = {}
    for m in store.select("peer_pair_members"):
        members.setdefault(m["pair_id"], []).append(m)

    blackout = {
        r["security_id"]: r
        for r in store.select("security_event_blackouts", eq={"week_start": start})
    }
    overrides: dict[str, dict[str, Any]] = {}
    for o in store.select("pair_blackout_overrides", eq={"week_start": start}):
        overrides[o["pair_id"]] = o
    decisions = {
        d["pair_id"]: d["decision"]
        for d in store.select("pair_manual_decisions", eq={"week_start": start})
    }
    cats = _by_security(
        store.select("corporate_catalysts", gte={"event_date": today - timedelta(days=14)})
    )
    filings = _by_security(
        store.select("filing_documents", gte={"filed_at": today - timedelta(days=30)})
    )
    news = _by_security(
        store.select("news_items", gte={"published_at": today - timedelta(days=14)})
    )
    macro_rows = store.select("macro_context_snapshots", order="as_of_date", desc=True, limit=1)
    mk_rows = store.select("market_context_snapshots", order="snapshot_date", desc=True, limit=1)
    spy = by_ticker.get("SPY")
    spy_bars = _bars(store, spy["id"]) if spy else []

    bar_cache: dict[str, list[dict[str, Any]]] = {}

    def bars_for(sid: str) -> list[dict[str, Any]]:
        if sid not in bar_cache:
            bar_cache[sid] = _bars(store, sid)
        return bar_cache[sid]

    out: list[PairInputs] = []
    for pair in sorted(store.select("peer_pairs"), key=lambda p: p["name"]):
        if pair_ids is not None and pair["id"] not in pair_ids:
            continue
        if not include_all_statuses and pair.get("status") not in ACTIVE_STATUSES:
            continue
        ms = sorted(members.get(pair["id"], []), key=lambda m: m["role"])
        legs = []
        for m in ms:
            s = secs.get(m["security_id"])
            if s is None:
                continue
            legs.append(
                LegInputs(security=s, company=comps.get(s["id"], {}), bars=bars_for(s["id"]))
            )
        sector = (legs[0].security.get("sector") if legs else None) or pair.get("sector")
        etf_id = etf_by_sector.get(sector or "")
        etf = secs.get(etf_id) if etf_id else None
        out.append(
            PairInputs(
                pair=pair,
                legs=legs,
                sector_etf=etf["ticker"] if etf else None,
                sector_etf_bars=bars_for(etf["id"]) if etf else [],
                spy_bars=spy_bars,
                week_start=start,
                week_end=end,
                now=now,
                blackout_rows=blackout,
                override=overrides.get(pair["id"]),
                catalysts={sid: v for sid, v in cats.items()},
                filings=filings,
                news=news,
                macro=macro_rows[0] if macro_rows else None,
                market_ctx=mk_rows[0] if mk_rows else None,
                manual_decision=decisions.get(pair["id"]),
            )
        )
    return out, start, end
