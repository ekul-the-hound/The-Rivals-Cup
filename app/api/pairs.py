"""Peer-pair research endpoints.

GETs are read-only (user JWT, RLS). The POSTs only write research output / review state
(pair_rankings, weekly_portfolios, pair_manual_decisions, peer_pairs.status) through
ResearchWriteGuard. No order, trade, position or execution record is ever created.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_admin_db, get_store
from app.config import Settings, get_settings
from app.config.clock import utcnow
from app.db.store import Store
from app.db.writer import DB
from app.schemas.pairs import (
    ApproveRequest,
    BuildRequest,
    CandidatesResponse,
    CandidateSummary,
    ExcludeRequest,
    PairPacketResponse,
    WeeklyPortfolioResponse,
)
from app.services.pairs.blackout import scoring_week
from app.services.pairs.engine import PeerPairEngine
from app.services.pairs.loader import load_week_inputs
from app.services.pairs.models import PairEvaluation
from app.services.pairs.params import EngineParams
from app.services.pairs.portfolio import build_weekly_portfolio
from app.services.pairs.review import (
    PairNotFound,
    ReviewBlocked,
    ReviewResult,
    approve_for_review,
    exclude,
)

router = APIRouter(prefix="/pairs", tags=["pairs (research only)"])


def _params(settings: Settings, **kw) -> EngineParams:
    return EngineParams(
        adv_cap_pct=settings.wsr_est_adv_pct_cap,
        intended_leg_notional_usd=kw.pop("intended_leg_notional_usd", None)
        or settings.default_leg_size_usd,
        **kw,
    )


def _summary(e: PairEvaluation) -> CandidateSummary:
    return CandidateSummary(
        pair_id=e.pair_id, name=e.name, long_ticker=e.long_ticker, short_ticker=e.short_ticker,
        eligible=e.eligible, pair_quality_score=e.score, data_quality_score=e.data_quality_score,
        event_blackout_status=(e.packet.event_blackout or {}).get("status"),
        manual_decision=e.packet.manual_decision, factor=e.factor,
        ineligible_reasons=e.ineligible_reasons, risk_flags=e.packet.risk_flags,
    )  # fmt: skip


def _evaluate(store: Store, settings: Settings, week_start: date | None, pair_ids=None):
    now = utcnow()
    inputs, start, end = load_week_inputs(
        store, now, week_start, pair_ids, include_all_statuses=bool(pair_ids)
    )
    evals = PeerPairEngine(_params(settings)).evaluate_all(inputs)
    return evals, start, end


@router.get("/candidates", response_model=CandidatesResponse)
def candidates(
    week_start: date | None = None,
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> CandidatesResponse:
    evals, start, end = _evaluate(store, settings, week_start)
    return CandidatesResponse(
        week_start=start, week_end=end,
        eligible=[_summary(e) for e in evals if e.eligible],
        ineligible=[_summary(e) for e in evals if not e.eligible],
    )  # fmt: skip


@router.get("/weekly-portfolio", response_model=WeeklyPortfolioResponse)
def weekly_portfolio(
    week_start: date | None = None, store: Store = Depends(get_store)
) -> WeeklyPortfolioResponse:
    start = week_start or scoring_week(utcnow().date())[0]
    rows = store.select(
        "weekly_portfolios", eq={"week_start": start}, order="built_at", desc=True, limit=1
    )
    if not rows:
        raise HTTPException(
            404, f"no weekly portfolio built for week {start}; POST /pairs/build-weekly-portfolio"
        )
    r = rows[0]
    return WeeklyPortfolioResponse(
        week_start=start, run_id=r["run_id"], built_at=str(r["built_at"]), status=r["status"],
        selected=r.get("selected") or [], alternates=r.get("alternates") or [],
        warnings=r.get("warnings") or [], summary=r.get("summary") or {},
    )  # fmt: skip


@router.post("/build-weekly-portfolio", response_model=dict)
def build_weekly(
    body: BuildRequest | None = None,
    db: DB = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Compute and (optionally) persist a RESEARCH_DRAFT. Gated on system mode RESEARCH_ONLY."""
    body = body or BuildRequest()
    ctl = db.select("system_control_state", limit=1)
    if not ctl or ctl[0].get("mode") != "RESEARCH_ONLY":
        raise HTTPException(409, "system mode is not RESEARCH_ONLY; weekly build refused")
    p = _params(
        settings, max_pairs=min(body.max_pairs, 6),
        intended_leg_notional_usd=body.intended_leg_notional_usd,
    )  # fmt: skip
    pf = build_weekly_portfolio(db, utcnow(), p, body.week_start, db=db if body.persist else None)
    return pf.model_dump(mode="json")


@router.get("/{pair_id}/packet", response_model=PairPacketResponse)
def pair_packet(
    pair_id: str,
    week_start: date | None = None,
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> PairPacketResponse:
    if not store.select("peer_pairs", eq={"id": pair_id}, limit=1):
        raise HTTPException(404, "unknown pair")
    evals, start, _ = _evaluate(store, settings, week_start, [pair_id])
    e = evals[0]
    return PairPacketResponse(
        week_start=start, eligible=e.eligible, ineligible_reasons=e.ineligible_reasons,
        components=e.components, penalties=e.penalties, packet=e.packet,
    )  # fmt: skip


def _week(week_start: date | None) -> date:
    return week_start or scoring_week(utcnow().date())[0]


@router.post("/{pair_id}/manual-approve-for-review", response_model=ReviewResult)
def manual_approve(
    pair_id: str,
    body: ApproveRequest | None = None,
    db: DB = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> ReviewResult:
    """Pin a pair into this week's review list. Review state only; NOT a trade."""
    start = _week(body.week_start if body else None)
    if not db.select("peer_pairs", eq={"id": pair_id}, limit=1):
        raise HTTPException(404, "unknown pair")
    evals, _, _ = _evaluate(db, settings, start, [pair_id])
    e = evals[0]
    try:
        return approve_for_review(
            db, pair_id, start, utcnow(), e.eligible,
            "; ".join(f"{r.code}" for r in e.ineligible_reasons if r.code != "MANUALLY_EXCLUDED"),
        )  # fmt: skip
    except PairNotFound as exc:
        raise HTTPException(404, "unknown pair") from exc
    except ReviewBlocked as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{pair_id}/manual-exclude", response_model=ReviewResult)
def manual_exclude(
    pair_id: str, body: ExcludeRequest, db: DB = Depends(get_admin_db)
) -> ReviewResult:
    """Exclude a pair from this week's portfolio. Review state only; NOT a trade."""
    try:
        return exclude(db, pair_id, _week(body.week_start), utcnow(), body.reason)
    except PairNotFound as exc:
        raise HTTPException(404, "unknown pair") from exc
    except ReviewBlocked as exc:
        raise HTTPException(409, str(exc)) from exc
