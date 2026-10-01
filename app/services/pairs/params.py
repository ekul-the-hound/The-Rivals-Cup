"""All tunable thresholds in one place. Defaults are documented in docs/option_a_peer_pair_strategy.md."""

from pydantic import BaseModel, Field

WEIGHTS: dict[str, float] = {  # sum to 100
    "relationship_quality": 15,
    "relative_strength_divergence": 15,
    "trend_consistency": 12,
    "sector_relative_divergence": 12,
    "liquidity": 10,
    "correlation_beta_compatibility": 14,
    "catalyst_evidence_quality": 7,
    "data_completeness": 15,
}
assert sum(WEIGHTS.values()) == 100


class EngineParams(BaseModel):
    # --- hard eligibility ---
    intended_leg_notional_usd: float = 10_000.0
    adv_cap_pct: float = (
        0.01  # ESTIMATED WSR cap = 1% of 20d dollar volume (not a verified WSR rule)
    )
    min_relationship_quality: int = 3
    min_correlation: float = 0.50
    min_corr_obs: int = 40
    min_bars: int = 21  # 20 daily returns
    max_single_day_move: float = (
        0.35  # larger adjusted-price jump => possible unresolved corporate action
    )
    max_leverage: float = 2.0
    us_exchanges: tuple[str, ...] = ("NYSE", "NASDAQ", "NYSE American", "NYSE Arca", "Cboe BZX")
    require_verified_blackout: bool = False
    # --- portfolio selection ---
    max_pairs: int = Field(6, ge=1, le=6)
    target_min_pairs: int = 4
    min_score: float = 60.0
    min_adjusted_score: float = 55.0
    max_per_cluster: int = 2
    max_per_driver: int = 1
    total_gross_budget_pct: float = 60.0  # of portfolio value, summed over both legs of all pairs
    max_pair_gross_pct: float = 15.0
    portfolio_value_usd: float = 100_000.0  # ASSUMPTION; set to your real WSR portfolio size
    overlap_cap: float = 30.0
    net_beta_warn_pct: float = 5.0
    cluster_gross_warn_share: float = 0.40
