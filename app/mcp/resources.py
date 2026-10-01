"""The 4 read-only MCP resources."""

from collections.abc import Callable
from typing import Any

from app.mcp import tools as T
from app.mcp.common import ToolContext
from app.mcp.envelope import ToolResult
from app.mcp.rules import rules_summary


def _rules(ctx: ToolContext) -> ToolResult:
    return ToolResult(data=rules_summary(), warnings=["Not the official rule set."])


def _market(ctx: ToolContext) -> ToolResult:
    from app.mcp.schemas import MarketDashboardIn

    return T.get_market_dashboard(ctx, MarketDashboardIn())


def _portfolio(ctx: ToolContext) -> ToolResult:
    data, missing, fresh = T.portfolio_view(ctx)
    return ToolResult(data=data, freshness=fresh, missing_or_stale=missing, warnings=["Estimates only; Trader View is authoritative."])  # fmt: skip


RESOURCES: dict[str, tuple[str, str, Callable[[ToolContext], ToolResult]]] = {
    "rules://rival-cup/current": (
        "Rival Cup rules summary",
        "Versioned boundaries, strategy constraints and manual checklist (not the official rules).",
        _rules,
    ),  # fmt: skip
    "market://dashboard/current": (
        "Market dashboard",
        "SPY/QQQ/IWM, sector ETF ranking, macro context, freshness.",
        _market,
    ),  # fmt: skip
    "portfolio://manual/current": (
        "Manual portfolio",
        "Manually logged positions and model-estimated score, clearly separated.",
        _portfolio,
    ),  # fmt: skip
    "quality://current": (
        "Data quality",
        "Open data-quality issues and source freshness.",
        T.quality_current,
    ),  # fmt: skip
}


def list_resources() -> list[dict[str, Any]]:
    return [{"uri": u, "name": u.split("://")[0], "title": t, "description": d, "mimeType": "application/json"} for u, (t, d, _f) in RESOURCES.items()]  # fmt: skip
