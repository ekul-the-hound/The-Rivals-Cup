# Competition compliance

This describes the self-imposed boundaries of this codebase. **It is not the official rule set.** Before
relying on any workflow, read the current official 2026 Wall Street Rivals Rival Cup rules and confirm
that using outside research tools, and an AI assistant for research, is permitted. Nothing here has
been verified against those rules.

## Core principle

Research in, human decision, **manual entry in Trader View**, manual record-keeping back here.

## Prohibited (and absent from the code)

1. Submitting, creating, modifying, cancelling, queueing, scheduling or closing any order.
2. A paper broker or order simulator.
3. Any connection to Wall Street Rivals, Trader View, a brokerage, or an execution system.
4. Browser automation (Selenium, Playwright, etc.).
5. Storing WSR/Trader View credentials (no such env vars exist).
6. Telegram (or any messenger) to execute or manage a trade.
7. Automated or scheduled trading; the project has no scheduler dependency. Refreshing public research
   *data* on a timer you configure outside the repo is not trading automation (see `docs/data_providers.md`).
8. Exploiting bugs, stale/erroneous prices, scoring ambiguity, roster rules or data discrepancies.
   Data-quality issues are surfaced as warnings to review, never as opportunities.
9. Claiming a signal was entered. `manual_trades` rows are written only by the owner *after* he has
   entered the trade himself; they are records, not instructions.

## What the system may do

Retrieve public/authorized data, rank peer-pair candidates, build research packets, compute price
history, correlation, liquidity estimates and market context, show **estimated** WSR liquidity, exposure
and score warnings, expose read-only tools via one MCP endpoint, and produce manual checklists.

## Enforcement

| Control | Where |
|---|---|
| Signal sending impossible | CHECK constraint on `system_control_state` |
| Default PAUSED | table default + API fail-safe |
| Manual-only records | CHECKs on `manual_trades.source`, `manual_positions.data_status` |
| Estimated scores only | CHECK on `score_snapshots.is_estimate`; response says "ESTIMATE ONLY" |
| MCP cannot write | `mcp_readonly` role: SELECT only, read-only transactions |
| Static guard | `scripts/check_no_execution.py` + `test_no_execution_code` |
| Only data-refresh and research-review routes write | `test_only_refresh_and_research_review_routes_are_non_get` |
| Pair research can write only to an allow-list | `ResearchWriteGuard` (`pair_rankings`, `weekly_portfolios`, `pair_manual_decisions`, `peer_pairs.status` in CANDIDATE/APPROVED_FOR_REVIEW) |
| A weekly portfolio can only be a draft | CHECK `weekly_portfolios.status = 'RESEARCH_DRAFT'` |
| MCP is read-only and execution-free | `app/mcp` is a separate app; compliance tests in `test_mcp.py` (imports, calls, loaded modules, order language) |
| Review state is not a trade | `test_manual_review_state_is_not_a_trade`; approving never sets `ACTIVE_MANUALLY` |

Estimated liquidity/exposure/score values must be treated as approximations. Compare against what
Trader View actually shows before entering anything.

## Final compliance audit (Turn 5)

`python -m scripts.compliance_audit` (also page 9 of the dashboard and `app/tests/test_compliance_audit.py`)
scans code, config and SQL (not tests/docs) and **fails** if it finds: broker or WSR clients; Trader
View clients; Selenium/Playwright/Puppeteer/browser automation; `submit_order` / `cancel_order` /
`close_position`-style methods; an execution provider; a paper broker or fill simulator; a
scheduled-order generator; trading credentials; Telegram handlers; or MCP write tools (the MCP registry
must be exactly the 12 read-only tools, with no writer imports). Synthetic violation repos in the tests
prove each rule triggers.

Manual journal rules: records are created only with an explicit "already entered manually" confirmation;
source/data_status are forced to `MANUAL`; recording or approving never changes a research pair; WSR
orders are never imported; the journal can write only `manual_portfolios`, `manual_pair_records`,
`manual_positions`, `manual_trades` and `dividend_events`. All scores are labelled ESTIMATED with
"WSR data authoritative".

## Repository audit (October 2026)
Full-repo search for order, broker, WSR, Trader View, browser-automation, scheduler, messenger and
execution code found none outside the compliance rule definitions and tests. The MCP registry is 12
read-only tools. The REST `POST` routes only record manual review decisions or start data refreshes; none
can place or change an order. See `manual_operating_checklist.md` and `test_and_release_checklist.md`.
