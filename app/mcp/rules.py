"""Versioned rules summary + manual checklist. Static text; no I/O.

IMPORTANT: this is NOT the official Wall Street Rivals rule set and nothing here has been verified
against it. Items are either (a) hard boundaries of this research system, (b) the owner's own
strategy constraints, or (c) estimates. The official rules and Trader View always control.
"""

from typing import Any

from app.mcp import RULES_VERSION

SYSTEM_BOUNDARIES = [
    "This research system never places, modifies, cancels, schedules or closes a trade.",
    "It never connects to Wall Street Rivals, Trader View, a brokerage, an exchange or a browser.",
    "It stores no WSR/Trader View credentials and sends no Telegram or other messages.",
    "The MCP server is read-only: it cannot write to the database or change pairs, portfolios, settings or event blackouts.",
    "The user decides and enters every trade manually in Trader View, and records it manually afterward.",
    "Data discrepancies, stale prices or scoring ambiguity are warnings to review, never opportunities.",
]
OWNER_STRATEGY_CONSTRAINTS = [
    {"rule": "Both legs must be U.S.-listed equities.", "source": "owner strategy (Option A)", "verified_against_wsr": False},
    {"rule": "No ETF leveraged more than 2x.", "source": "owner strategy (Option A)", "verified_against_wsr": False},
    {"rule": "By default, skip a pair if either leg has a known earnings date or major binary event in the scoring week (Mon-Fri).", "source": "owner strategy (Option A)", "verified_against_wsr": False},
    {"rule": "Prefer 4-6 high-quality pairs over more; do not force a pair or a sector.", "source": "owner strategy (Option A)", "verified_against_wsr": False},
]  # fmt: skip
ESTIMATES = [
    {"item": "Leg size cap", "method": "1% of trailing 20-day average dollar volume", "status": "ESTIMATE supplied by the owner; not a verified WSR limit"},
    {"item": "Gross/net exposure", "method": "cost basis of manually logged positions", "status": "ESTIMATE; Trader View is authoritative"},
    {"item": "Player Score and drawdown", "method": "stored estimate snapshots", "status": "ESTIMATE only; never the official score"},
]  # fmt: skip
CHECKLIST = [
    "Read the current official Rival Cup rules; confirm outside research tools (including an AI assistant) are allowed.",
    "Confirm each security is available to you in Trader View, and that long/short permissions and position limits allow it.",
    "Confirm neither leg is an ETF leveraged more than 2x, and neither has a pending split, merger, spin-off or ticker change.",
    "Verify earnings and major-event dates for both legs for Monday-Friday on each company's investor-relations calendar.",
    "Compare live prices with the reference prices here; skip if either leg gapped or the data looks wrong.",
    "Check Trader View's real liquidity and exposure limits against the estimated cap.",
    "Choose dollar sizes yourself; keep total gross exposure within your own limit.",
    "Decide your own exit review points (stop/target are reminders for you, not instructions).",
    "Enter any trade manually in Trader View, then record it manually in your own log.",
]


def rules_summary() -> dict[str, Any]:
    return {
        "rules_version": RULES_VERSION,
        "is_official_rule_set": False,
        "disclaimer": "Not the official Wall Street Rivals rules. Verify everything against the current official rules and Trader View.",
        "system_boundaries": SYSTEM_BOUNDARIES,
        "owner_strategy_constraints": OWNER_STRATEGY_CONSTRAINTS,
        "estimates": ESTIMATES,
        "manual_compliance_checklist": CHECKLIST,
        "manual_entry_required": True,
    }
