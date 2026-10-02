"""Manual research-metadata edits for security_master, each written with an audit row.

These functions change RESEARCH METADATA ONLY (sector, industry, security type, event notes, and a
manual note about competition availability). They never contact Wall Street Rivals, Trader View,
a browser or any trading system, and they cannot change prices, positions or orders.
"""

import uuid
from datetime import date, datetime
from typing import Any

from app.db.writer import DB
from app.models.enums import DataStatus
from app.models.universe import OTHER_SECTOR, TARGET_SECTORS, SecurityType, TradableStatus
from app.services.universe.classify import flags_for_type
from app.services.universe.master import UniverseWriteGuard
from app.services.universe.repo import rebuild_view_state
from app.services.universe.sectors import normalize_sector
from app.services.universe.views import UniverseParams

OVERRIDABLE_FIELDS = {
    "sector", "industry", "security_type", "is_reit",
    "earnings_date_if_known", "known_major_event_date", "event_risk_notes",
}  # fmt: skip
MASTER_COLUMNS = {
    "sector", "sector_raw", "sector_source", "sector_data_status", "sector_classified_at",
    "industry", "security_type", "security_type_source", "is_common_stock", "is_adr", "is_etf",
    "is_preferred", "is_warrant", "is_right", "is_unit", "is_reit", "earnings_date_if_known",
    "known_major_event_date", "event_risk_notes", "manually_overridden", "override_reason",
    "competition_tradable_status", "competition_verification_note", "competition_verified_at",
}  # fmt: skip


class UnknownTicker(ValueError):
    pass


class InvalidChange(ValueError):
    pass


def _row(db: DB, ticker: str) -> dict[str, Any]:
    t = ticker.strip().upper().replace(".", "-")
    rows = db.select("security_master", eq={"ticker": t}, limit=1)
    if not rows:
        raise UnknownTicker(f"unknown ticker '{ticker}'")
    return rows[0]


def _audit(
    db: DB,
    ticker: str,
    action: str,
    changes: dict[str, Any],
    reason: str | None,
    actor: str,
    now: datetime,
) -> str:
    audit_id = str(uuid.uuid4())
    db.upsert(
        "security_master_audit",
        [{"id": audit_id, "ticker": ticker, "action": action, "field_changes": changes,
          "reason": reason, "actor": actor, "created_at": now}],
        "id",
    )  # fmt: skip
    return audit_id


def _norm_date(v: Any) -> str | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError as exc:
        raise InvalidChange(f"invalid date '{v}' (use YYYY-MM-DD)") from exc


def manual_verify(
    db: DB,
    ticker: str,
    status: str,
    note: str | None,
    now: datetime,
    params: UniverseParams | None = None,
    actor: str = "owner",
) -> dict[str, Any]:
    """Record a MANUAL note about competition availability. Research metadata only."""
    try:
        st = TradableStatus(status)
    except ValueError as exc:
        raise InvalidChange(f"status must be one of {[s.value for s in TradableStatus]}") from exc
    note = (note or "").strip()
    if st == TradableStatus.MANUALLY_REJECTED and len(note) < 5:
        raise InvalidChange("a rejection needs a note (at least 5 characters) saying why")
    cur = _row(db, ticker)
    tk = cur["ticker"]
    new = {
        "competition_tradable_status": st.value,
        "competition_verification_note": note or None,
        "competition_verified_at": None if st == TradableStatus.UNKNOWN else now,
    }
    changes = {
        k: {"old": cur.get(k), "new": v}
        for k, v in new.items()
        if k != "competition_verified_at" and cur.get(k) != v
    }
    if not changes and cur.get("competition_tradable_status") == st.value:
        raise InvalidChange("no change: status and note are already recorded")
    guard = UniverseWriteGuard(db, MASTER_COLUMNS)
    guard.upsert("security_master", [{"ticker": tk, **new}], "ticker")
    audit_id = _audit(guard, tk, "MANUAL_VERIFY", changes, note or None, actor, now)
    _rebuild(guard, tk, params, now)
    return {"ticker": tk, "competition_tradable_status": st.value, "audit_id": audit_id,
            "note": "Manual research note only. It does not contact or change WSR/Trader View."}  # fmt: skip


def manual_override(
    db: DB,
    ticker: str,
    changes: dict[str, Any],
    reason: str,
    now: datetime,
    params: UniverseParams | None = None,
    actor: str = "owner",
) -> dict[str, Any]:
    """Correct sector / industry / security type / event fields. Requires a written reason."""
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise InvalidChange("override_reason is required (at least 5 characters)")
    bad = set(changes) - OVERRIDABLE_FIELDS
    if bad or not changes:
        raise InvalidChange(
            f"overridable fields: {sorted(OVERRIDABLE_FIELDS)}; got {sorted(bad) or 'none'}"
        )
    cur = _row(db, ticker)
    tk = cur["ticker"]
    patch: dict[str, Any] = {}
    for k, v in changes.items():
        if k == "sector":
            code = normalize_sector(v)
            if code is None or (code != OTHER_SECTOR and code not in TARGET_SECTORS):
                raise InvalidChange(f"unrecognised sector '{v}'")
            patch.update(
                sector=code, sector_raw=str(v), sector_source="MANUAL",
                sector_data_status=DataStatus.MANUAL.value, sector_classified_at=now,
            )  # fmt: skip
        elif k == "security_type":
            try:
                stype = SecurityType(str(v).upper())
            except ValueError as exc:
                raise InvalidChange(
                    f"security_type must be one of {[s.value for s in SecurityType]}"
                ) from exc
            patch.update(
                security_type=stype.value, security_type_source="MANUAL", **flags_for_type(stype)
            )
        elif k in ("earnings_date_if_known", "known_major_event_date"):
            patch[k] = _norm_date(v)
        elif k == "is_reit":
            patch[k] = bool(v)
        else:
            patch[k] = (str(v).strip() or None) if v is not None else None
    diff = {k: {"old": cur.get(k), "new": v} for k, v in patch.items() if cur.get(k) != v
            and k not in ("sector_classified_at", "sector_raw")}  # fmt: skip
    if not diff:
        raise InvalidChange("no change: those values are already recorded")
    patch.update(manually_overridden=True, override_reason=reason)
    guard = UniverseWriteGuard(db, MASTER_COLUMNS)
    guard.upsert("security_master", [{"ticker": tk, **patch}], "ticker")
    audit_id = _audit(guard, tk, "MANUAL_OVERRIDE", diff, reason, actor, now)
    state = _rebuild(guard, tk, params, now)
    return {"ticker": tk, "changed": sorted(diff), "audit_id": audit_id, "views": state}


def _rebuild(db: DB, ticker: str, params: UniverseParams | None, now: datetime) -> dict[str, Any]:
    rebuild_view_state(db, params or UniverseParams(), now.date(), now, [ticker])
    s = db.select("security_master_view_state", eq={"ticker": ticker}, limit=1)
    s = s[0] if s else {}
    return {
        k: bool(s.get(k))
        for k in ("in_all_target_sector_listings", "in_pair_research_eligible", "in_manual_review")
    }
