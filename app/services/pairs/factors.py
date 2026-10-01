"""Factor clusters and overlap penalties (avoid redundant exposures)."""

from dataclasses import dataclass

from app.services.pairs.params import EngineParams

CLUSTER_OF_SECTOR = {
    "Information Technology": "growth_beta",
    "Communication Services": "growth_beta",
    "Consumer Discretionary": "growth_beta",
    "Energy": "cyclical_commodity",
    "Materials": "cyclical_commodity",
    "Financials": "rate_sensitive",
    "Real Estate": "rate_sensitive",
    "Utilities": "rate_sensitive",
    "Consumer Staples": "defensive",
    "Health Care": "defensive",
    "Industrials": "industrial_cycle",
}
DEFAULT_DRIVER = {
    "Information Technology": "growth_semis_software",
    "Communication Services": "growth_advertising",
    "Consumer Discretionary": "consumer_cyclical",
    "Energy": "oil_price",
    "Materials": "commodities_cycle",
    "Financials": "rates_credit",
    "Real Estate": "rates_real_assets",
    "Utilities": "rates_regulated",
    "Consumer Staples": "consumer_defensive",
    "Health Care": "healthcare_defensive",
    "Industrials": "industrial_cycle",
}
CROWDED_CLUSTERS = {"growth_beta", "rate_sensitive"}


@dataclass(frozen=True)
class FactorProfile:
    sector: str | None
    cluster: str
    driver: str


def factor_profile(sector: str | None, macro_driver: str | None) -> FactorProfile:
    return FactorProfile(
        sector=sector,
        cluster=CLUSTER_OF_SECTOR.get(sector or "", "unclassified"),
        driver=macro_driver or DEFAULT_DRIVER.get(sector or "", "unclassified"),
    )


def pair_overlap(a: FactorProfile, b: FactorProfile) -> tuple[float, list[str]]:
    pts, why = 0.0, []
    if a.driver == b.driver and a.driver != "unclassified":
        pts += 20
        why.append(f"same macro driver '{a.driver}'")
    if a.cluster == b.cluster and a.cluster != "unclassified":
        pts += 12
        why.append(f"same factor cluster '{a.cluster}'")
    if a.sector and a.sector == b.sector:
        pts += 6
        why.append(f"same sector '{a.sector}'")
    return pts, why


def factor_overlap_penalty(
    cand: FactorProfile, selected: list[FactorProfile], p: EngineParams
) -> tuple[float, list[str]]:
    total, reasons = 0.0, []
    for s in selected:
        pts, why = pair_overlap(cand, s)
        total += pts
        reasons += why
    return min(total, p.overlap_cap), reasons
