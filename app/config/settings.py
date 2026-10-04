"""Environment-driven settings. Secrets come from .env only."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    app_version: str = "0.1.0"
    display_timezone: str = "America/Chicago"

    supabase_url: str = ""
    supabase_anon_key: SecretStr = SecretStr("")
    supabase_service_role_key: SecretStr = SecretStr("")
    owner_user_id: str = ""

    # Free data stack
    sec_user_agent: str = ""  # SEC requires "Name email@example.com"
    fred_api_key: SecretStr = SecretStr("")
    web_user_agent: str = ""  # Yahoo / Google News / Wikipedia; defaults to sec_user_agent
    provider_cache_dir: str = ".cache/providers"
    http_timeout_seconds: float = 20.0
    wsr_est_adv_pct_cap: float = 0.01  # ESTIMATE: max leg size as a fraction of 20d ADV (USD)
    default_leg_size_usd: float = 10000.0

    # Optional free-tier keys for the deep-dive data (adapters stay off until a key is set)
    finnhub_api_key: SecretStr = SecretStr("")  # free: finnhub.io (60 calls/min)
    alpha_vantage_api_key: SecretStr = SecretStr("")  # free: alphavantage.co (~25 calls/day)
    alpha_vantage_daily_budget: int = 20  # calls per run kept under the free daily limit
    deep_dive_tickers: str = ""  # comma-separated shortlist for per-company data
    options_iv_enabled: bool = False  # Yahoo options chain: unofficial, best effort, off by default

    @property
    def deep_dive_list(self) -> list[str]:
        return [
            x.strip().upper().replace(".", "-")
            for x in self.deep_dive_tickers.split(",")
            if x.strip()
        ]

    # U.S.-listed target-sector universe (docs/universe_builder.md)
    universe_classify_batch: int = (
        4000  # sector lookups per run (SEC 8 req/s; Yahoo fallback 1 req/s)
    )
    universe_market_data_batch: int = 800  # Yahoo price lookups per run (1 request/second)
    universe_reclassify_days: int = 30  # re-check Yahoo sector labels at most this often
    universe_price_max_age_days: int = 5  # "current enough" daily quote, in calendar days
    universe_min_adv_usd: float = 1_000_000.0  # pair-research liquidity floor (20d avg $ volume)
    universe_min_price: float = 1.0
    universe_allow_unverified_sector: bool = False  # explicitly allow UNVERIFIED sector labels
    sector_mapping_adapter_enabled: bool = False  # DISABLED by default (CSV you supply)
    sector_mapping_csv: str = ""

    # Leaders & laggards (docs/leaders_laggards.md)
    leaders_history_batch: int = 800  # Yahoo price-history downloads per run (1 request/second)
    leaders_history_fresh_hours: int = 18  # skip tickers downloaded more recently than this
    leaders_candidates_per_sector: int = (
        8  # strongest names per sector whose competitors are mapped
    )
    leaders_min_adv_usd: float = 5_000_000.0  # both legs: 20d average $ volume floor
    leaders_min_price: float = 5.0
    leaders_min_history_bars: int = 120
    leaders_min_competitors: int = 3
    leaders_min_strength_gap: float = 15.0  # long strength minus short strength, in score points
    leaders_portfolio_usd: float = 1_000_000.0  # WSR rounds start at $1,000,000 (estimate)
    leaders_max_leg_pct: float = 15.0  # no single leg above this % of portfolio value
    leaders_gross_target_pct: float = 100.0  # both legs together; the WSR gross limit is 200%

    # DEVELOPMENT ONLY bearer-token mode. See app/api/deps.py.
    dev_bearer_token: SecretStr | None = None

    # --- MCP server (app/mcp) ---
    mcp_public_url: str = ""  # exact public URL entered in Claude, e.g. https://mcp.example.com/mcp
    mcp_oauth_issuer: str = ""  # defaults to {SUPABASE_URL}/auth/v1
    mcp_allowed_origins: str = "https://claude.ai,https://claude.com"
    mcp_rate_limit_per_minute: int = 60
    mcp_max_response_bytes: int = 80_000
    mcp_dev_bearer_token: SecretStr | None = None  # DEVELOPMENT ONLY

    @model_validator(mode="after")
    def _guard_mcp_dev_token(self) -> "Settings":
        token = self.mcp_dev_bearer_token.get_secret_value() if self.mcp_dev_bearer_token else ""
        if token and self.app_env != "development":
            raise ValueError("MCP_DEV_BEARER_TOKEN is development-only; unset it in production")
        if token and len(token) < 32:
            raise ValueError("MCP_DEV_BEARER_TOKEN must be at least 32 characters")
        return self

    @property
    def mcp_dev_token_enabled(self) -> bool:
        return self.app_env == "development" and bool(
            self.mcp_dev_bearer_token and self.mcp_dev_bearer_token.get_secret_value()
        )

    @model_validator(mode="after")
    def _guard_dev_token(self) -> "Settings":
        token = self.dev_bearer_token.get_secret_value() if self.dev_bearer_token else ""
        if token and self.app_env != "development":
            raise ValueError(
                "DEV_BEARER_TOKEN is development-only; unset it when APP_ENV=production"
            )
        if token and len(token) < 24:
            raise ValueError("DEV_BEARER_TOKEN must be at least 24 characters")
        return self

    @property
    def effective_web_user_agent(self) -> str:
        return self.web_user_agent or self.sec_user_agent or "wsr-research (set SEC_USER_AGENT)"

    @property
    def dev_token_enabled(self) -> bool:
        return self.app_env == "development" and bool(
            self.dev_bearer_token and self.dev_bearer_token.get_secret_value()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
