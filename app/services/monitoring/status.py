"""Read-only status builders for /controls/status, /research/status, /data-quality."""

from collections import Counter

from app.db.store import Store
from app.models.enums import SystemMode
from app.models.tables import DataQualityIssue, ProviderRunLog, SystemControlState
from app.schemas.api import ControlsStatus, DataQualityResponse, ResearchStatus
from app.services.validation.guards import PROHIBITED_CAPABILITIES

_COUNT_TABLES = (
    "securities",
    "peer_pairs",
    "market_bars",
    "research_packets",
    "pair_rankings",
    "manual_positions",
    "manual_trades",
)


def _control_state(store: Store) -> tuple[SystemControlState, str]:
    rows = store.select("system_control_state", limit=1)
    if not rows:
        return SystemControlState(), "default"  # fail safe: PAUSED, nothing enabled
    state = SystemControlState.model_validate(rows[0])
    return state, "database"


def get_controls_status(store: Store) -> ControlsStatus:
    state, source = _control_state(store)
    return ControlsStatus(
        mode=state.mode,
        provider_ingestion_enabled=state.provider_ingestion_enabled,
        state_source=source,  # type: ignore[arg-type]
        reason=state.reason,
        updated_at=state.updated_at,
        prohibited_capabilities=list(PROHIBITED_CAPABILITIES),
    )


def get_research_status(store: Store) -> ResearchStatus:
    state, source = _control_state(store)
    counts = {t: store.count(t) for t in _COUNT_TABLES}
    pairs = store.select("peer_pairs", columns="status")
    by_status = dict(Counter(p["status"] for p in pairs))
    packet = store.select("research_packets", columns="as_of", order="as_of", desc=True, limit=1)
    ranking = store.select(
        "pair_rankings", columns="ranked_at", order="ranked_at", desc=True, limit=1
    )
    runs = store.select("provider_run_logs", order="started_at", desc=True, limit=5)
    notes = ["Research only. All trades are entered manually by the owner in Trader View."]
    if state.mode == SystemMode.PAUSED:
        notes.append("System mode is PAUSED: no ingestion or ranking should run.")
    if source == "default":
        notes.append("system_control_state row missing; defaulting to PAUSED.")
    return ResearchStatus(
        mode=state.mode,
        counts=counts,
        pairs_by_status=by_status,
        latest_packet_at=packet[0]["as_of"] if packet else None,
        latest_ranking_at=ranking[0]["ranked_at"] if ranking else None,
        recent_provider_runs=[ProviderRunLog.model_validate(r) for r in runs],
        notes=notes,
    )


def get_data_quality(store: Store, limit: int = 100) -> DataQualityResponse:
    rows = store.select(
        "data_quality_issues", is_null=["resolved_at"], order="detected_at", desc=True, limit=limit
    )
    issues = [DataQualityIssue.model_validate(r) for r in rows]
    return DataQualityResponse(
        open_issue_count=len(issues),
        by_severity=dict(Counter(i.severity for i in issues)),
        issues=issues,
    )
