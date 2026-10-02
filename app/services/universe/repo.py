"""Read side of the universe: load master + view state, filter, and rebuild view state."""

from datetime import date, datetime
from typing import Any

from app.db.store import Store, select_all
from app.db.writer import DB
from app.models.universe import TARGET_SECTORS
from app.services.pairs.blackout import scoring_week
from app.services.universe.master import upsert_uniform
from app.services.universe.views import Evaluation, UniverseParams, current_blackout_dates, evaluate

VIEWS = {
    "all": "in_all_target_sector_listings",
    "pair_research": "in_pair_research_eligible",
    "manual_review": "in_manual_review",
}
VIEW_NAMES = {
    "all": "all_target_sector_listings",
    "pair_research": "pair_research_eligible_universe",
    "manual_review": "manual_review_universe",
}


def load_universe(store: Store) -> list[dict[str, Any]]:
    """Every security_master row merged with its latest view state (read-only)."""
    state = {r["ticker"]: r for r in select_all(store, "security_master_view_state")}
    out = []
    for m in select_all(store, "security_master"):
        s = state.get(m["ticker"], {})
        out.append(
            {
                **m,
                "in_all_target_sector_listings": bool(s.get("in_all_target_sector_listings")),
                "in_pair_research_eligible": bool(s.get("in_pair_research_eligible")),
                "in_manual_review": bool(s.get("in_manual_review")),
                "exclusion_reasons": list(s.get("exclusion_reasons") or []),
                "review_reasons": list(s.get("review_reasons") or []),
                "flags": list(s.get("flags") or []),
                "view_built_at": s.get("built_at"),
            }
        )
    return out


def in_view(row: dict[str, Any], view: str) -> bool:
    return bool(row.get({"all": "in_all_target_sector_listings",
                         "pair_research": "in_pair_research_eligible",
                         "manual_review": "in_manual_review"}[view]))  # fmt: skip


def filter_rows(
    rows: list[dict[str, Any]],
    view: str = "all",
    sector: str | None = None,
    exchange: str | None = None,
    security_type: str | None = None,
    adr: bool | None = None,
    tradable_status: str | None = None,
    min_adv: float | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not in_view(r, view):
            continue
        if sector and r.get("sector") != sector.upper():
            continue
        if exchange and (r.get("exchange") or "").lower() != exchange.lower():
            continue
        if security_type and r.get("security_type") != security_type.upper():
            continue
        if adr is not None and bool(r.get("is_adr")) != adr:
            continue
        if tradable_status and r.get("competition_tradable_status") != tradable_status.upper():
            continue
        if min_adv is not None and float(r.get("average_dollar_volume_20d") or 0) < min_adv:
            continue
        if q and q.lower() not in f"{r.get('ticker')} {r.get('company_name')}".lower():
            continue
        out.append(r)
    return sorted(out, key=lambda r: (str(r.get("sector")), r["ticker"]))


def event_dates_this_week(store: Store, today: date) -> dict[str, list[date]]:
    """Known event dates for the current scoring week from security_event_blackouts."""
    start, _ = scoring_week(today)
    bl = store.select("security_event_blackouts", eq={"week_start": start})
    if not bl:
        return {}
    tickers = {s["id"]: s["ticker"] for s in select_all(store, "securities")}
    rows = [{**b, "ticker": tickers.get(b["security_id"])} for b in bl]
    return current_blackout_dates(rows)


def view_state_row(ticker: str, ev: Evaluation, built_at: datetime) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "in_all_target_sector_listings": ev.in_all_target,
        "in_pair_research_eligible": ev.in_pair_eligible,
        "in_manual_review": ev.in_manual_review,
        "exclusion_reasons": ev.exclusion_reasons,
        "review_reasons": ev.review_reasons,
        "flags": ev.flags,
        "built_at": built_at,
    }


def rebuild_view_state(
    db: DB,
    p: UniverseParams,
    today: date,
    now: datetime,
    tickers: list[str] | None = None,
) -> dict[str, int]:
    """Recompute and store view membership (all rows, or just `tickers`). Returns counts."""
    if tickers is None:
        master = select_all(db, "security_master")
    else:
        master = [
            r for tk in tickers for r in db.select("security_master", eq={"ticker": tk}, limit=1)
        ]
    events = event_dates_this_week(db, today)
    states, counts = [], {"all": 0, "pair_research": 0, "manual_review": 0}
    for m in master:
        ev = evaluate(m, p, today, events.get(m["ticker"]))
        states.append(view_state_row(m["ticker"], ev, now))
        counts["all"] += ev.in_all_target
        counts["pair_research"] += ev.in_pair_eligible
        counts["manual_review"] += ev.in_manual_review
    upsert_uniform(db, "security_master_view_state", states, "ticker")
    counts["rows"] = len(master)
    return counts


def is_target_sector(row: dict[str, Any]) -> bool:
    return row.get("sector") in TARGET_SECTORS
