"""Provider sector label -> exactly one of five target sectors, OTHER (recognised, not a target),
or None (unrecognised). Never infers a sector from a ticker or company name.

`SectorMappingAdapter` is the replaceable, DISABLED-BY-DEFAULT fallback for symbols Yahoo cannot
classify. The only adapter shipped reads a CSV *you* provide; nothing is downloaded or guessed.
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.models.universe import OTHER_SECTOR, TargetSector

_TARGET_ALIASES: dict[str, TargetSector] = {
    "health care": TargetSector.HEALTH_CARE, "healthcare": TargetSector.HEALTH_CARE,
    "health": TargetSector.HEALTH_CARE, "health care sector": TargetSector.HEALTH_CARE,
    "industrials": TargetSector.INDUSTRIALS, "industrial": TargetSector.INDUSTRIALS,
    "industrial goods": TargetSector.INDUSTRIALS,
    "financials": TargetSector.FINANCIALS, "financial": TargetSector.FINANCIALS,
    "financial services": TargetSector.FINANCIALS, "finance": TargetSector.FINANCIALS,
    "utilities": TargetSector.UTILITIES, "utility": TargetSector.UTILITIES,
    "real estate": TargetSector.REAL_ESTATE, "realestate": TargetSector.REAL_ESTATE,
}  # fmt: skip
# Recognised non-target labels (GICS names and Yahoo's labels). Anything else is "unrecognised".
_OTHER_LABELS = {
    "technology", "information technology", "consumer cyclical", "consumer discretionary",
    "consumer defensive", "consumer staples", "energy", "basic materials", "materials",
    "communication services", "communications", "telecommunication services",
}  # fmt: skip


def _key(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_&/-]+", " ", label.strip().lower())).strip()


def normalize_sector(label: str | None) -> str | None:
    """-> 'HEALTH_CARE' | ... | 'OTHER' | None (missing or unrecognised label)."""
    if not label or not label.strip():
        return None
    k = _key(label)
    if k in _TARGET_ALIASES:
        return _TARGET_ALIASES[k].value
    code = k.replace(" ", "_").upper()
    if code in {s.value for s in TargetSector}:
        return code
    if k in _OTHER_LABELS:
        return OTHER_SECTOR
    return None


@dataclass(frozen=True)
class SectorGuess:
    sector_raw: str
    industry: str | None
    source: str


class SectorMappingAdapter(Protocol):
    name: str

    def lookup(self, ticker: str) -> SectorGuess | None: ...


class CsvSectorMappingAdapter:
    """Optional, DISABLED BY DEFAULT (SECTOR_MAPPING_ADAPTER_ENABLED=false). Reads a CSV that you
    supply with columns ticker,sector[,industry]. Rows from it are marked UNVERIFIED, never AVAILABLE."""

    name = "SECTOR_MAPPING_CSV"

    def __init__(self, path: str | Path) -> None:
        self._rows: dict[str, SectorGuess] = {}
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                tk = (row.get("ticker") or row.get("symbol") or "").upper().replace(".", "-")
                if tk and row.get("sector"):
                    self._rows[tk] = SectorGuess(
                        row["sector"], row.get("industry") or None, self.name
                    )

    def lookup(self, ticker: str) -> SectorGuess | None:
        return self._rows.get(ticker.upper())
