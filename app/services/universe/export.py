"""Export of the research universe with provenance, flags and the mandatory disclaimer."""

import csv
import io
import json
from datetime import datetime
from typing import Any

from app.models.universe import DISCLAIMER

EXPORT_COLUMNS = [
    "ticker", "company_name", "exchange", "sector", "industry", "security_type", "is_adr", "is_reit",
    "market_cap", "average_dollar_volume_20d", "average_daily_volume_20d", "last_price",
    "liquidity_data_as_of", "earnings_date_if_known", "known_major_event_date", "event_risk_notes",
    "competition_tradable_status", "competition_verification_note", "competition_verified_at",
    "in_all_target_sector_listings", "in_pair_research_eligible", "in_manual_review",
    "exclusion_reasons", "review_reasons", "flags",
    "listing_source", "listing_verified_at", "sector_source", "sector_raw", "sector_data_status",
    "sector_classified_at", "security_type_source", "market_data_source", "cik", "sec_company_name",
    "manually_overridden", "override_reason", "is_active", "listing_financial_status",
    "generated_at", "disclaimer",
]  # fmt: skip
LIST_COLUMNS = {"exclusion_reasons", "review_reasons", "flags"}


def export_records(rows: list[dict[str, Any]], generated_at: datetime) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = {c: r.get(c) for c in EXPORT_COLUMNS}
        for c in LIST_COLUMNS:
            rec[c] = list(r.get(c) or [])
        rec["generated_at"] = generated_at.isoformat()
        rec["disclaimer"] = DISCLAIMER
        out.append(rec)
    return out


def render_csv(records: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    w.writeheader()
    for rec in records:
        flat = {
            k: (";".join(v) if isinstance(v, list) else ("" if v is None else v))
            for k, v in rec.items()
        }
        w.writerow(flat)
    return buf.getvalue()


def render_json(records: list[dict[str, Any]], generated_at: datetime, view: str = "all") -> str:
    return json.dumps(
        {
            "generated_at": generated_at.isoformat(),
            "disclaimer": DISCLAIMER,
            "view": view,
            "count": len(records),
            "records": records,
        },
        default=str,
        indent=2,
    )
