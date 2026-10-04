"""SEC Standard Industrial Classification (SIC) code -> target sector.

SIC codes come from the issuer's own SEC filings (data.sec.gov submissions), so this is a rule
table over an official field, never a guess from a ticker or company name. SIC is NOT GICS: the
boundaries differ, so rows classified this way carry sector_source = SEC_SIC and the
SECTOR_FROM_SIC flag, and a manual override always wins.

Returns a target sector code, OTHER (a SIC code that maps to none of the five), or None (no code).
"""

REIT_SIC = 6798
BLANK_CHECK_SIC = 6770

# (low, high inclusive, sector). First match wins, so specific carve-outs come first.
_RULES: list[tuple[int, int, str]] = [
    # ---- Health Care ----
    (2830, 2836, "HEALTH_CARE"),  # drugs, biological products, diagnostics
    (3826, 3826, "HEALTH_CARE"),  # laboratory analytical instruments
    (3841, 3845, "HEALTH_CARE"),  # surgical, medical, dental, electromedical devices
    (3851, 3851, "HEALTH_CARE"),  # ophthalmic goods
    (5047, 5047, "HEALTH_CARE"),  # medical equipment wholesale
    (5122, 5122, "HEALTH_CARE"),  # drugs wholesale
    (6324, 6324, "HEALTH_CARE"),  # hospital and medical service plans
    (8000, 8099, "HEALTH_CARE"),  # health services
    (8731, 8731, "HEALTH_CARE"),  # commercial physical and biological research
    # ---- Utilities ----
    (4900, 4900, "UTILITIES"),
    (4910, 4941, "UTILITIES"),  # electric, gas, combined, water
    (4991, 4991, "UTILITIES"),  # cogeneration / independent power
    # ---- Real Estate ----
    (6500, 6553, "REAL_ESTATE"),  # real estate operators, lessors, developers
    (6798, 6798, "REAL_ESTATE"),  # real estate investment trusts
    # ---- Financials ----
    (6000, 6323, "FINANCIALS"),  # banks, credit, brokers, insurance
    (6325, 6499, "FINANCIALS"),  # insurance (excl. 6324 above), title insurance, agents
    (6700, 6797, "FINANCIALS"),  # holding / investment offices, blank checks
    (6799, 6799, "FINANCIALS"),  # investors, NEC
    # ---- Industrials ----
    (1500, 1799, "INDUSTRIALS"),  # construction
    (3410, 3569, "INDUSTRIALS"),  # fabricated metal, engines, machinery
    (3580, 3599, "INDUSTRIALS"),  # refrigeration, service-industry and misc machinery
    (3610, 3699, "INDUSTRIALS"),  # electrical equipment (excl. 3570s computers, 3660s comms)
    (3710, 3799, "INDUSTRIALS"),  # vehicles parts, aircraft, ships, rail, defense hardware
    (3812, 3812, "INDUSTRIALS"),  # search, detection, navigation (aerospace / defense)
    (4000, 4299, "INDUSTRIALS"),  # railroads, transit, trucking
    (4400, 4599, "INDUSTRIALS"),  # water and air transportation
    (
        4731,
        4789,
        "INDUSTRIALS",
    ),  # freight and transportation services (4700-4729 travel agencies fall to OTHER)
    (4950, 4961, "INDUSTRIALS"),  # refuse, hazardous waste, environmental services
    (5063, 5063, "INDUSTRIALS"),  # electrical apparatus wholesale
    (5080, 5084, "INDUSTRIALS"),  # machinery and industrial equipment wholesale
    (7340, 7359, "INDUSTRIALS"),  # building services, equipment rental
    (7361, 7363, "INDUSTRIALS"),  # staffing
    (7381, 7382, "INDUSTRIALS"),  # security services
    (8700, 8744, "INDUSTRIALS"),  # engineering, management, testing services (excl. 8731)
]
# 3660-3699 contains communications equipment; carve those out so they fall to OTHER.
_OTHER_OVERRIDES = [(3660, 3679)]


def sector_from_sic(sic: int | str | None) -> str | None:
    try:
        code = int(sic) if sic not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if not code:
        return None
    if any(lo <= code <= hi for lo, hi in _OTHER_OVERRIDES):
        return "OTHER"
    for lo, hi, sector in _RULES:
        if lo <= code <= hi:
            return sector
    return "OTHER"
