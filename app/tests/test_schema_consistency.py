"""Keep SQL migrations, Python enums and the compliance guards in sync."""

import importlib.util
import re
from pathlib import Path

from app.models import enums
from app.models import tables as t

ROOT = Path(__file__).resolve().parents[2]
SQL = "\n".join(p.read_text() for p in sorted((ROOT / "supabase" / "migrations").glob("*.sql")))

EXPECTED_TABLES = {
    "profiles",
    "securities",
    "companies",
    "sector_etf_mappings",
    "peer_pairs",
    "peer_pair_members",
    "market_bars",
    "daily_quotes",
    "liquidity_metrics",
    "market_context_snapshots",
    "macro_context_snapshots",
    "filing_documents",
    "corporate_catalysts",
    "news_items",
    "research_packets",
    "pair_rankings",
    "pair_research_reviews",
    "manual_portfolios",
    "manual_positions",
    "manual_trades",
    "portfolio_daily_values",
    "score_snapshots",
    "data_quality_issues",
    "provider_run_logs",
    "mcp_audit_logs",
    "system_control_state",
    "security_event_blackouts",
    "pair_blackout_overrides",
    "pair_weekly_eligibility",
    "weekly_portfolios",
    "pair_manual_decisions",
    "manual_pair_records",
    "security_master",
    "security_master_view_state",
    "security_master_audit",
    "short_interest",
    "short_sale_volume_daily",
    "earnings_calendar",
    "analyst_recommendations",
    "earnings_transcripts",
    "company_fundamentals",
    "sec_filing_feed",
    "options_iv_snapshots",
    "dividend_events",
    "universe_price_history",
    "competitor_map",
    "leader_laggard_books",
}


def test_enums_match_sql():
    for m in re.finditer(r"create type public\.(\w+) as enum \(([^)]*)\)", SQL):
        name, body = m.groups()
        cls = {
            "direction": enums.Direction,
            "pair_status": enums.PairStatus,
            "data_status": enums.DataStatus,
            "evidence_quality": enums.EvidenceQuality,
            "catalyst_type": enums.CatalystType,
            "review_status": enums.ReviewStatus,
            "system_mode": enums.SystemMode,
        }[name]
        assert [v.strip(" '") for v in body.split(",")] == [e.value for e in cls], name


def test_all_tables_exist_with_rls():
    created = set(re.findall(r"create table public\.(\w+)", SQL))
    assert created == EXPECTED_TABLES
    rls_block = SQL.split("foreach t in array array[")[1].split("]")[0]
    explicit = set(re.findall(r"alter table public\.(\w+) enable row level security", SQL))
    in_loops = set(re.findall(r"'(\w+)'", rls_block)) | {"manual_pair_records", "dividend_events"}
    assert in_loops | explicit == EXPECTED_TABLES
    forced = set(re.findall(r"alter table public\.(\w+) force row level security", SQL))
    assert {
        "security_event_blackouts",
        "pair_blackout_overrides",
        "pair_weekly_eligibility",
    } <= forced


def test_row_models_cover_tables():
    names = {n for n in dir(t) if n[0].isupper()}
    for cls in [
        "Profile",
        "Security",
        "Company",
        "SectorEtfMapping",
        "PeerPair",
        "PeerPairMember",
        "MarketBar",
        "DailyQuote",
        "LiquidityMetric",
        "MarketContextSnapshot",
        "MacroContextSnapshot",
        "FilingDocument",
        "CorporateCatalyst",
        "NewsItem",
        "ResearchPacket",
        "PairRanking",
        "PairResearchReview",
        "ManualPortfolio",
        "ManualPosition",
        "ManualTrade",
        "PortfolioDailyValue",
        "ScoreSnapshot",
        "DataQualityIssue",
        "ProviderRunLog",
        "McpAuditLog",
        "SystemControlState",
    ]:
        assert cls in names


def test_seed_contents():
    for tk in [
        "SPY",
        "QQQ",
        "IWM",
        "XLB",
        "XLC",
        "XLE",
        "XLF",
        "XLI",
        "XLK",
        "XLP",
        "XLRE",
        "XLU",
        "XLV",
        "XLY",
    ]:
        assert f"('{tk}'," in SQL


def test_safe_defaults_in_sql():
    assert "signal_sending_enabled = false" in SQL
    assert "mode public.system_mode not null default 'PAUSED'" in SQL
    assert "force row level security" in SQL
    assert not re.search(r"grant\s+(insert|update|delete)[^;]*mcp_readonly", SQL, re.I)


def test_no_execution_code():
    spec = importlib.util.spec_from_file_location(
        "guard", ROOT / "scripts" / "check_no_execution.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.scan() == []
