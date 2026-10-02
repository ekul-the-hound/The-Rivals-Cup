"""Counts and coverage for the universe dashboard / API (pure functions over loaded rows)."""

from collections import Counter
from typing import Any

from app.models.universe import TARGET_SECTORS
from app.services.universe.repo import in_view

LIQUIDITY_BUCKETS = [
    ("no data", None, None),
    ("< $1M", 0.0, 1e6),
    ("$1M - $5M", 1e6, 5e6),
    ("$5M - $25M", 5e6, 25e6),
    ("$25M - $100M", 25e6, 100e6),
    ("$100M+", 100e6, float("inf")),
]


def liquidity_bucket(adv: Any) -> str:
    try:
        v = None if adv is None else float(adv)
    except (TypeError, ValueError):
        v = None
    if v is None:
        return "no data"
    for label, lo, hi in LIQUIDITY_BUCKETS[1:]:
        if lo <= v < hi:  # type: ignore[operator]
            return label
    return "$100M+"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = [r for r in rows if in_view(r, "all")]
    pair = [r for r in rows if in_view(r, "pair_research")]
    review = [r for r in rows if in_view(r, "manual_review")]
    by_sector = {s: 0 for s in TARGET_SECTORS}
    pair_by_sector = {s: 0 for s in TARGET_SECTORS}
    for r in all_rows:
        by_sector[r["sector"]] = by_sector.get(r["sector"], 0) + 1
    for r in pair:
        pair_by_sector[r["sector"]] = pair_by_sector.get(r["sector"], 0) + 1
    excluded = Counter(
        reason
        for r in rows
        if not in_view(r, "all")
        for reason in (r.get("exclusion_reasons") or [])[:1]
    )
    pair_excluded = Counter(
        reason
        for r in all_rows
        if not in_view(r, "pair_research")
        for reason in (r.get("exclusion_reasons") or [])
    )
    built = [str(r["view_built_at"]) for r in rows if r.get("view_built_at")]
    verified = [str(r["listing_verified_at"]) for r in rows if r.get("listing_verified_at")]
    classified = [r for r in rows if r.get("sector")]
    return {
        "total_listings": len(rows),
        "active_listings": sum(bool(r.get("is_active", True)) for r in rows),
        "all_target_sector_listings": len(all_rows),
        "pair_research_eligible": len(pair),
        "manual_review": len(review),
        "by_sector": by_sector,
        "pair_eligible_by_sector": pair_by_sector,
        "by_exchange": dict(Counter(r.get("exchange") or "UNKNOWN" for r in all_rows)),
        "by_security_type_all_listings": dict(
            Counter(r.get("security_type") or "UNKNOWN" for r in rows)
        ),
        "excluded_primary_reason": dict(excluded),
        "pair_research_exclusion_reasons": dict(pair_excluded),
        "manual_review_reasons": dict(
            Counter(x for r in review for x in (r.get("review_reasons") or []))
        ),
        "liquidity_distribution": dict(
            Counter(liquidity_bucket(r.get("average_dollar_volume_20d")) for r in all_rows)
        ),
        "sector_classification_coverage": {
            "classified": len(classified),
            "unclassified": len(rows) - len(classified),
        },
        "tradability": dict(
            Counter(r.get("competition_tradable_status") or "UNKNOWN" for r in all_rows)
        ),
        "listing_verified_latest": max(verified) if verified else None,
        "views_built_latest": max(built) if built else None,
    }
