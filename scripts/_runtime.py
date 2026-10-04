"""Shared CLI plumbing: real (Supabase) or --mock (offline, in-memory) context."""

import asyncio
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.config.clock import utcnow
from app.db.mock_seed import build_mock_db
from app.db.store import create_supabase_client
from app.db.writer import DB, SupabaseDB
from app.services.ingestion.runner import JobContext
from app.services.providers.mock_transport import MockWorld
from app.services.providers.registry import build_providers


def build_context(mock: bool, week_start=None) -> JobContext:
    now = utcnow()
    if mock:
        settings = Settings(
            sec_user_agent="Mock Runner mock@example.com",
            fred_api_key="mock-key",
            finnhub_api_key="mock-key",
            alpha_vantage_api_key="mock-key",
            options_iv_enabled=True,
            app_env="development",
        )
        world = MockWorld(now.astimezone(UTC).date())
        providers = build_providers(settings, world.transport(), sleep=lambda _x: asyncio.sleep(0))
        db: DB = build_mock_db(now.date(), now)
    else:
        settings = get_settings()
        providers = build_providers(settings)
        db = SupabaseDB(
            create_supabase_client(
                settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
            )
        )
    return JobContext(settings=settings, db=db, providers=providers, now=now, week_start=week_start)


def _unused(_: datetime) -> None: ...
