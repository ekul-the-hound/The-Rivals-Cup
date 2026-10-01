"""Default Monday candidate list: pairs excluded by blackout unless a logged override exists."""

from typing import Any

from app.db.store import Store

ACTIVE_STATUSES = {"CANDIDATE", "APPROVED_FOR_REVIEW"}


def monday_candidates(store: Store, week_start: str) -> dict[str, list[dict[str, Any]]]:
    pairs = {p["id"]: p for p in store.select("peer_pairs")}
    elig = store.select("pair_weekly_eligibility", eq={"week_start": week_start})
    out: dict[str, list[dict[str, Any]]] = {"included": [], "excluded": [], "not_evaluated": []}
    seen = set()
    for e in elig:
        p = pairs.get(e["pair_id"])
        if not p or p["status"] not in ACTIVE_STATUSES:
            continue
        seen.add(p["id"])
        item = {
            "pair": p["name"],
            "warnings": e.get("warnings", []),
            "reasons": e.get("blocked_reasons", []),
        }
        (out["included"] if e["eligible"] else out["excluded"]).append(item)
    for pid, p in pairs.items():
        if pid not in seen and p["status"] in ACTIVE_STATUSES:
            out["not_evaluated"].append({"pair": p["name"]})
    return out
