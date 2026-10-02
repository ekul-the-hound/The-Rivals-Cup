"""Security-type classification of exchange listings, from the published name and flags.

Deliberately conservative: a name that matches no known pattern is UNKNOWN (sent to manual
review), never silently treated as a common stock. Sector is NOT inferred here (see sectors.py).
"""

import re
from dataclasses import dataclass

from app.models.universe import SecurityType

_I = re.IGNORECASE
LEVERAGED = re.compile(
    r"\b[1-9](?:\.\d+)?x\b|\bultra(?:pro|short)?\b|\bleveraged\b|\binverse\b|\b(?:bull|bear)\b"
    r"|\bdaily\s+(?:long|short|target)\b|\b-[1-9]x\b",
    _I,
)
ETN = re.compile(r"\bETNs?\b|exchange[- ]traded notes?", _I)
FUND = re.compile(r"\bfunds?\b|closed[- ]end|\bmutual\b", _I)
DEBT = re.compile(
    r"\bnotes?\b(?!\s+(?:and|payable))|\bdebentures?\b|\bbonds?\b|\bsenior notes\b|\bsubordinated\b"
    r"|\d+(?:\.\d+)?%\s",
    _I,
)
WARRANT = re.compile(r"\bwarrants?\b", _I)
RIGHT = re.compile(r"\brights?\b", _I)
UNIT = re.compile(r"\bunits?\b", _I)
COMMON_UNITS = re.compile(
    r"\bcommon units?\b|\blimited partner(?:ship)? (?:interests?|units?)\b", _I
)
PREFERRED = re.compile(r"\bpreferred\b|\bpfd\b|\bpreference\b", _I)
ADR = re.compile(r"american depositary|\bADSs?\b|\bADRs?\b", _I)
DEPOSITARY = re.compile(r"\bdepositary (?:shares|interests?)\b", _I)
COMMON = re.compile(
    r"\bcommon (?:stock|shares?|equity)\b|\bordinary shares?\b|\bshares of beneficial interest\b"
    r"|\bcapital stock\b|\bvoting shares\b|\bclass [a-z] (?:shares?|stock)\b|\bcommon\b|\bcommon units?\b",
    _I,
)
REIT_NAME = re.compile(r"\bREITs?\b|real estate investment trust", _I)
SPAC_NAME = re.compile(
    r"\bacquisition (?:corp|corporation|co|company|holdings|limited|ltd)\b|\bblank check\b", _I
)


@dataclass(frozen=True)
class ListingClass:
    security_type: SecurityType
    is_common_stock: bool
    is_adr: bool
    is_etf: bool
    is_leveraged_product: bool
    is_preferred: bool
    is_warrant: bool
    is_right: bool
    is_unit: bool
    is_spac: bool
    is_reit_by_name: bool
    is_test_issue: bool
    note: str | None = None


def normalize_symbol(symbol: str) -> str:
    """Yahoo and the SEC use '-' for share classes (BRK.B -> BRK-B). '$' marks preferreds."""
    return symbol.strip().upper().replace(".", "-").replace(" ", "")


def classify_listing(
    symbol: str, name: str, etf_flag: bool = False, test_issue: bool = False
) -> ListingClass:
    n = name or ""
    leveraged = bool(LEVERAGED.search(n))
    spac = bool(SPAC_NAME.search(n))
    reit_name = bool(REIT_NAME.search(n))
    note: str | None = None
    stype: SecurityType
    if test_issue:
        stype = SecurityType.UNKNOWN
        note = "test issue"
    elif etf_flag:
        stype = SecurityType.ETF
    elif ETN.search(n):
        stype = SecurityType.ETN
    elif FUND.search(n):
        stype = SecurityType.FUND
    elif WARRANT.search(n):
        stype = SecurityType.WARRANT
    elif RIGHT.search(n) and not COMMON.search(n):
        stype = SecurityType.RIGHT
    elif PREFERRED.search(n) or "$" in symbol:
        stype = SecurityType.PREFERRED
    elif UNIT.search(n) and not COMMON_UNITS.search(n):
        stype = SecurityType.UNIT
    elif DEBT.search(n) and not COMMON.search(n):
        stype = SecurityType.DEBT
    elif ADR.search(n):
        stype = SecurityType.ADR
    elif COMMON.search(n):
        stype = SecurityType.COMMON_STOCK
    elif DEPOSITARY.search(n):
        stype = SecurityType.UNKNOWN
        note = "depositary shares of unclear type"
    else:
        stype = SecurityType.UNKNOWN
        note = "name does not identify a security type"
    return ListingClass(
        security_type=stype,
        is_common_stock=stype in (SecurityType.COMMON_STOCK, SecurityType.ADR),
        is_adr=stype == SecurityType.ADR,
        is_etf=stype == SecurityType.ETF,
        is_leveraged_product=leveraged
        and stype in (SecurityType.ETF, SecurityType.ETN, SecurityType.FUND, SecurityType.UNKNOWN),
        is_preferred=stype == SecurityType.PREFERRED,
        is_warrant=stype == SecurityType.WARRANT,
        is_right=stype == SecurityType.RIGHT,
        is_unit=stype == SecurityType.UNIT,
        is_spac=spac,
        is_reit_by_name=reit_name,
        is_test_issue=test_issue,
        note=note,
    )


def flags_for_type(stype: SecurityType | str) -> dict[str, bool]:
    """Boolean columns implied by a (possibly manually chosen) security type."""
    st = SecurityType(stype)
    return {
        "is_common_stock": st in (SecurityType.COMMON_STOCK, SecurityType.ADR),
        "is_adr": st == SecurityType.ADR,
        "is_etf": st == SecurityType.ETF,
        "is_preferred": st == SecurityType.PREFERRED,
        "is_warrant": st == SecurityType.WARRANT,
        "is_right": st == SecurityType.RIGHT,
        "is_unit": st == SecurityType.UNIT,
    }
