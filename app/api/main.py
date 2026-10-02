"""FastAPI app: read-only research endpoints. The only non-GET routes write data-refresh jobs
(/admin) or research-review state (/pairs/*). No trade/order routes exist."""

from fastapi import Depends, FastAPI

from app.api.admin import router as admin_router
from app.api.deps import get_store
from app.api.pairs import router as pairs_router
from app.api.universe import router as universe_router
from app.config import Settings, get_settings
from app.config.clock import to_display, utcnow
from app.db.store import Store
from app.schemas.api import (
    ControlsStatus,
    DataQualityResponse,
    HealthResponse,
    ManualPortfolioResponse,
    ResearchStatus,
    ScoreResponse,
)
from app.services.monitoring import get_controls_status, get_data_quality, get_research_status
from app.services.portfolio import get_manual_portfolios
from app.services.scoring import get_latest_scores

app = FastAPI(
    title="WSR Rival Cup Research (read-only)",
    description="Research-only. Never connects to Wall Street Rivals / Trader View or any broker.",
    version="0.1.0",
)

app.include_router(admin_router)
app.include_router(pairs_router)
app.include_router(universe_router)


@app.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    now = utcnow()
    return HealthResponse(
        service="wsr-rival-cup-research",
        version=settings.app_version,
        environment=settings.app_env,
        time_utc=now,
        time_display=to_display(now, settings.display_timezone),
        display_timezone=settings.display_timezone,
    )


@app.get("/research/status", response_model=ResearchStatus)
def research_status(store: Store = Depends(get_store)) -> ResearchStatus:
    return get_research_status(store)


@app.get("/data-quality", response_model=DataQualityResponse)
def data_quality(store: Store = Depends(get_store)) -> DataQualityResponse:
    return get_data_quality(store)


@app.get("/portfolio/manual", response_model=ManualPortfolioResponse)
def portfolio_manual(store: Store = Depends(get_store)) -> ManualPortfolioResponse:
    return get_manual_portfolios(store)


@app.get("/portfolio/score", response_model=ScoreResponse)
def portfolio_score(store: Store = Depends(get_store)) -> ScoreResponse:
    return get_latest_scores(store)


@app.get("/controls/status", response_model=ControlsStatus)
def controls_status(store: Store = Depends(get_store)) -> ControlsStatus:
    return get_controls_status(store)
