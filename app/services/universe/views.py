"""The three research universes, as pure functions over security_master rows.

  all_target_sector_listings   active U.S.-listed common-stock-like securities in the five target
                               sectors (ADRs and low-liquidity names kept, flagged)
  pair_research_eligible       the above + fresh quote, liquidity floor, no known event this week,
                               not a SPAC/shell, not manually rejected
  manual_review                names a human has to classify or verify

Membership never claims Rival Cup / Trader View availability: that stays a manual check.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.models.enums import DataStatus
from app.models.universe import TARGET_SECTORS, SecurityType, TradableStatus
from app.services.pairs.blackout import scoring_week

NORMAL_FINANCIAL_STATUS = {None, "", "N"}
TYPE_REASON = {
    SecurityType.ETF: "ETF",
    SecurityType.ETN: "ETN",
    SecurityType.FUND: "FUND",
    SecurityType.PREFERRED: "PREFERRED",
    SecurityType.WARRANT: "WARRANT",
    SecurityType.RIGHT: "RIGHT",
    SecurityType.UNIT: "UNIT",
    SecurityType.DEBT: "DEBT",
}


@dataclass(frozen=True)
class UniverseParams:
    price_max_age_days: int = 5
    min_adv_usd: float = 1_000_000.0
    min_price: float = 1.0
    allow_unverified_sector: bool = False

    @classmethod
    def from_settings(cls, s: Any) -> "UniverseParams":
        return cls(
            price_max_age_days=s.universe_price_max_age_days,
            min_adv_usd=s.universe_min_adv_usd,
            min_price=s.universe_min_price,
            allow_unverified_sector=s.universe_allow_unverified_sector,
        )


@dataclass
class Evaluation:
    in_all_target: bool = False
    in_pair_eligible: bool = False
    in_manual_review: bool = False
    exclusion_reasons: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _d(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def evaluate(
    row: dict[str, Any],
    p: UniverseParams,
    today: date,
    blackout_dates: list[date] | None = None,
) -> Evaluation:
    """Classify one security_master row. `blackout_dates` are extra known event dates (e.g. from
    security_event_blackouts) that apply to the current scoring week."""
    ev = Evaluation()
    stype = SecurityType(row.get("security_type") or SecurityType.UNKNOWN)
    sector = row.get("sector")
    s_status = row.get("sector_data_status") or DataStatus.MISSING
    tradable = row.get("competition_tradable_status") or TradableStatus.UNKNOWN
    active = bool(row.get("is_active", True))
    reasons: list[str] = []
    flags: list[str] = []

    # ---- listing / security-type filters ----
    if not active:
        reasons.append("INACTIVE_OR_DELISTED")
    if row.get("is_test_issue"):
        reasons.append("TEST_ISSUE")
    if row.get("is_otc"):
        reasons.append("OTC")
    if stype in TYPE_REASON:
        reasons.append(TYPE_REASON[stype])
    elif stype == SecurityType.UNKNOWN and not row.get("is_test_issue"):
        reasons.append("SECURITY_TYPE_UNCERTAIN")
    if row.get("is_leveraged_product"):
        reasons.append("LEVERAGED_PRODUCT")
    if not row.get("is_common_stock") and not reasons:
        reasons.append("NOT_COMMON_STOCK")
    if (row.get("listing_financial_status") or "").upper() == "Q":
        reasons.append("LISTING_BANKRUPT")

    # ---- sector filters ----
    allowed = {DataStatus.AVAILABLE.value, DataStatus.MANUAL.value}
    if p.allow_unverified_sector:
        allowed.add(DataStatus.UNVERIFIED.value)
    if reasons:  # not a candidate security at all: its sector is irrelevant
        pass
    elif sector in TARGET_SECTORS:
        if str(s_status) not in allowed:
            reasons.append("SECTOR_UNVERIFIED")
    elif sector in (None, ""):
        reasons.append("SECTOR_MISSING")
    else:
        reasons.append("NON_TARGET_SECTOR")

    in_all = not reasons
    ev.in_all_target = in_all

    # ---- informational flags ----
    if row.get("is_adr"):
        flags.append("ADR")
    if row.get("is_reit"):
        flags.append("REIT")
    if row.get("is_spac"):
        flags.append("SPAC")
    if tradable == TradableStatus.UNKNOWN:
        flags.append("TRADABILITY_UNKNOWN")
    elif tradable == TradableStatus.MANUALLY_VERIFIED:
        flags.append("MANUALLY_VERIFIED")
    elif tradable == TradableStatus.MANUALLY_REJECTED:
        flags.append("MANUALLY_REJECTED")
    if row.get("manually_overridden"):
        flags.append("MANUALLY_OVERRIDDEN")
    if str(s_status) == DataStatus.MANUAL.value:
        flags.append("MANUAL_SECTOR")
    if str(s_status) == DataStatus.UNVERIFIED.value:
        flags.append("SECTOR_UNVERIFIED")
    if row.get("sector_source") == "SEC_SIC":
        flags.append("SECTOR_FROM_SIC")
    if not row.get("cik"):
        flags.append("NO_SEC_MATCH")
    fin = (row.get("listing_financial_status") or "").upper()
    if fin not in NORMAL_FINANCIAL_STATUS:
        flags.append(f"LISTING_STATUS_{fin}")

    # ---- market data / event filters (pair-research view only) ----
    adv, price = _num(row.get("average_dollar_volume_20d")), _num(row.get("last_price"))
    as_of = _d(row.get("liquidity_data_as_of"))
    pair_reasons: list[str] = []
    if as_of is None or price is None or adv is None:
        pair_reasons.append("MARKET_DATA_MISSING")
        flags.append("MARKET_DATA_MISSING")
    else:
        if (today - as_of) > timedelta(days=p.price_max_age_days):
            pair_reasons.append("MARKET_DATA_STALE")
            flags.append("STALE_MARKET_DATA")
        if price < p.min_price:
            pair_reasons.append("BELOW_MIN_PRICE")
        if adv < p.min_adv_usd:
            pair_reasons.append("ILLIQUID")
            flags.append("LOW_LIQUIDITY")
    start, end = scoring_week(today)
    event_dates = [_d(row.get("earnings_date_if_known")), _d(row.get("known_major_event_date"))]
    event_dates += blackout_dates or []
    if any(d and start <= d <= end for d in event_dates):
        pair_reasons.append("EVENT_BLACKOUT")
        flags.append("EVENT_WITHIN_WEEK")
    if row.get("is_spac"):
        pair_reasons.append("SPAC_OR_SHELL")
    if tradable == TradableStatus.MANUALLY_REJECTED:
        pair_reasons.append("MANUALLY_REJECTED")

    ev.in_pair_eligible = in_all and not pair_reasons
    ev.exclusion_reasons = reasons + (pair_reasons if in_all else [])

    # ---- manual review queue ----
    review: list[str] = []
    plausible_stock = (
        active
        and not row.get("is_test_issue")
        and stype in (SecurityType.COMMON_STOCK, SecurityType.ADR, SecurityType.UNKNOWN)
        and tradable != TradableStatus.MANUALLY_REJECTED
    )
    if plausible_stock:
        if sector in (None, ""):
            review.append("SECTOR_MISSING")
        elif sector in TARGET_SECTORS and str(s_status) == DataStatus.UNVERIFIED.value:
            review.append("SECTOR_UNVERIFIED")
        if stype == SecurityType.UNKNOWN:
            review.append("SECURITY_TYPE_UNCERTAIN")
        if row.get("is_adr") and sector in TARGET_SECTORS and tradable == TradableStatus.UNKNOWN:
            review.append("ADR_VERIFY_TRADABILITY")
        if sector in TARGET_SECTORS and fin not in NORMAL_FINANCIAL_STATUS:
            review.append("LISTING_COMPLIANCE_ISSUE")
    ev.review_reasons = review
    ev.in_manual_review = bool(review)
    ev.flags = flags
    return ev


def current_blackout_dates(store_rows: list[dict[str, Any]]) -> dict[str, list[date]]:
    """ticker -> event dates, from security_event_blackouts rows already joined to a ticker."""
    out: dict[str, list[date]] = {}
    for r in store_rows:
        for key in ("earnings_date_if_known", "known_major_event_date"):
            d = _d(r.get(key))
            if d and r.get("ticker"):
                out.setdefault(r["ticker"], []).append(d)
    return out
