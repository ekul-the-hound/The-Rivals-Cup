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
