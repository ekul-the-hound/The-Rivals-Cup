"""Rule-based corporate catalyst detection from SEC filing metadata + bounded excerpts.

Deliberately skips 8-K Item 2.02 (results of operations): earnings features are out of scope.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.models.enums import CatalystType

INSIDER_MIN_USD = 250_000.0

ITEM_MAP: dict[str, tuple[CatalystType, str]] = {
    "1.01": (CatalystType.MATERIAL_AGREEMENT, "Entry into a material definitive agreement"),
    "1.02": (CatalystType.MATERIAL_AGREEMENT, "Termination of a material agreement"),
    "2.01": (CatalystType.M_AND_A, "Completion of acquisition or disposition"),
    "2.03": (CatalystType.OFFERING, "Creation of a direct financial obligation"),
    "3.02": (CatalystType.OFFERING, "Unregistered sale of equity securities"),
    "5.01": (CatalystType.MANAGEMENT_CHANGE, "Change in control of registrant"),
    "5.02": (CatalystType.MANAGEMENT_CHANGE, "Director/officer departure or appointment"),
}
KEYWORDS: list[tuple[CatalystType, str, re.Pattern]] = [
    (CatalystType.BUYBACK, "Share repurchase language", re.compile(r"repurchase|buyback", re.I)),
    (CatalystType.OFFERING, "Offering language", re.compile(r"public offering|prospectus supplement|senior notes offering", re.I)),
    (CatalystType.M_AND_A, "M&A language", re.compile(r"\bmerger\b|agreed to acquire|definitive agreement to acquire|tender offer", re.I)),
    (CatalystType.REGULATORY, "Regulatory/legal language", re.compile(r"subpoena|investigation|department of justice|\bDOJ\b|\bFTC\b|consent order|class action", re.I)),
]  # fmt: skip


@dataclass
class CatalystHit:
    catalyst_type: CatalystType
    headline: str
    detail: str | None
    dedupe_key: str


def detect_catalysts(filing: dict[str, Any], excerpt: str | None = None) -> list[CatalystHit]:
    acc, form = filing["accession_number"], filing["form_type"]
    hits: dict[CatalystType, CatalystHit] = {}

    def add(ct: CatalystType, headline: str, detail: str | None = None) -> None:
        hits.setdefault(ct, CatalystHit(ct, headline, detail, f"{acc}:{ct.value}"))

    if form == "4":
        f4 = filing.get("form4_summary") or {}
        for tx in f4.get("transactions", []):
            if tx.get("code") in {"P", "S"} and abs(tx.get("value_usd", 0)) >= INSIDER_MIN_USD:
                side = "purchase" if tx["code"] == "P" else "sale"
                add(
                    CatalystType.INSIDER,
                    f"Insider open-market {side} ~${abs(tx['value_usd']):,.0f}",
                    f"{f4.get('owner')} ({f4.get('title') or 'insider'})",
                )
        return list(hits.values())
    if form != "8-K":
        return []  # 10-Q/10-K are context documents, not catalysts
    for item in filing.get("items") or []:
        if item in ITEM_MAP:
            ct, label = ITEM_MAP[item]
            add(ct, f"8-K Item {item}: {label}", (excerpt or "")[:300] or None)
    if excerpt:
        for ct, label, pat in KEYWORDS:
            m = pat.search(excerpt)
            if m:
                s = max(0, m.start() - 80)
                add(ct, f"8-K text: {label}", excerpt[s : m.end() + 120])
    return list(hits.values())
