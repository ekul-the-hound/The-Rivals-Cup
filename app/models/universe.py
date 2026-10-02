"""Vocabulary for the U.S.-listed target-sector research universe (mirrors the SQL check constraints)."""

from enum import StrEnum

DISCLAIMER = (
    "This is a research universe. It does not confirm that WSR/Trader View permits trading "
    "every listed symbol. Verify availability manually before any trade."
)


class TargetSector(StrEnum):
    HEALTH_CARE = "HEALTH_CARE"
    INDUSTRIALS = "INDUSTRIALS"
    FINANCIALS = "FINANCIALS"
    UTILITIES = "UTILITIES"
    REAL_ESTATE = "REAL_ESTATE"


TARGET_SECTORS: tuple[str, ...] = tuple(s.value for s in TargetSector)
OTHER_SECTOR = "OTHER"  # a recognised sector that is not one of the five targets


class SecurityType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ADR = "ADR"
    ETF = "ETF"
    ETN = "ETN"
    FUND = "FUND"
    PREFERRED = "PREFERRED"
    WARRANT = "WARRANT"
    RIGHT = "RIGHT"
    UNIT = "UNIT"
    DEBT = "DEBT"
    UNKNOWN = "UNKNOWN"


class TradableStatus(StrEnum):
    """Manual research note only. Nothing in this repository can read or change real availability."""

    UNKNOWN = "UNKNOWN"
    MANUALLY_VERIFIED = "MANUALLY_VERIFIED"
    MANUALLY_REJECTED = "MANUALLY_REJECTED"
