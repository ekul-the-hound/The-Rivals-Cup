# WSR Rival Cup Research (read-only)

Local-first, Supabase-backed **research** system for a **manual** U.S. equity pair-trading workflow in the
2026 Wall Street Rivals Rival Cup. You enter every trade by hand in Trader View. This system never
connects to Wall Street Rivals, Trader View, or any brokerage, and contains no execution code.
See `docs/competition_compliance.md`.

**Status:** schema/RLS/seed (10 migrations, 33 tables), a free data stack (SEC EDGAR, FRED, Yahoo,
Google News RSS, Wikipedia) with 9 refresh jobs, Option A peer-pair ranking and weekly portfolio draft,
a read-only MCP server (12 tools), a manual trade **journal**, an ESTIMATED score calculator, a 9-page
Streamlit dashboard, and a static compliance audit. **This system cannot place or manage trades.**

> **WARNING: never commit `.env`, service-role/secret keys, API keys, tokens or credential files.**
> `.gitignore` protects them, but check before every commit (`docs/github_workflow.md`, section 4).

## Quick Start (macOS / zsh, mock demo, no keys needed)

```zsh
git clone https://github.com/ekul-the-hound/The-Rivals-Cup.git && cd The-Rivals-Cup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
python -m scripts.refresh_weekly_research --mock       # expect nine SUCCEEDED lines
python -m scripts.build_weekly_portfolio --mock        # expect a [RESEARCH_DRAFT] pair list
python -m streamlit run app/dashboard/main.py          # open http://localhost:8501
```

Then: `pytest`, `ruff check .`, `python -m scripts.compliance_audit`.

## Setup guides (read in this order)

1. [`docs/local_setup_guide.md`](docs/local_setup_guide.md): venv, install, `.env`, mock demo, tests
2. [`docs/supabase_setup.md`](docs/supabase_setup.md): free Supabase project, keys, CLI, migrations, RLS checks, seed
3. [`docs/data_provider_setup.md`](docs/data_provider_setup.md): SEC EDGAR, FRED, Yahoo, Google News RSS, Wikipedia
4. [`docs/dashboard_and_mcp_runbook.md`](docs/dashboard_and_mcp_runbook.md): start/test API, dashboard, MCP; optional Claude Web
5. [`docs/first_day_checklist.md`](docs/first_day_checklist.md): line-by-line checklist
6. [`docs/troubleshooting.md`](docs/troubleshooting.md): fixes, logs, safe resets
7. [`docs/github_workflow.md`](docs/github_workflow.md): auth, pre-commit secret checks, push

Also: `docs/manual_monday_workflow.md`, `docs/score_methodology.md`, `docs/option_a_peer_pair_strategy.md`,
`docs/data_providers.md`, `docs/mcp_*.md`, `docs/competition_compliance.md`.

## GitHub

Repository: <https://github.com/ekul-the-hound/The-Rivals-Cup>. Authenticate once with `gh auth login`
(or SSH), then `git push -u origin main`. Never force-push. Full steps: `docs/github_workflow.md`.

## Layout

```
app/        config, db (read-only Store), models, schemas, services/*, api, jobs, tests
supabase/   migrations (schema, RLS, seed), functions (reserved for the single MCP endpoint)
docs/       compliance, Supabase setup, architecture / MCP decision
scripts/    dev helpers + check_no_execution.py (static guard)
```

## Endpoints

| Path | Auth | Purpose |
|---|---|---|
| `/health` | none | liveness, UTC + America/Chicago time |
| `/research/status` | owner | counts, pair status, latest packet/ranking, recent provider runs |
| `/data-quality` | owner | open data-quality issues |
| `/portfolio/manual` | owner | manually recorded positions/trades (cost-basis exposure) |
| `/portfolio/score` | owner | latest ESTIMATED score snapshot (never official) |
| `/controls/status` | owner | system mode (default `PAUSED`), locked-off signal sending |
| `GET /pairs/candidates` | owner | eligible and ineligible peer pairs for the scoring week, with scores and reasons |
| `GET /pairs/{pair_id}/packet` | owner | full research packet for one pair |
| `GET /pairs/weekly-portfolio` | owner | latest saved weekly RESEARCH_DRAFT (404 if none built) |
| `POST /pairs/build-weekly-portfolio` | owner (admin) | compute + save the 4-6 pair draft; 409 unless `RESEARCH_ONLY`; `max_pairs` capped at 6 |
| `POST /pairs/{pair_id}/manual-approve-for-review`, `POST /pairs/{pair_id}/manual-exclude` | owner (admin) | write **review state only** (never a trade); approval cannot bypass hard eligibility |
| `POST /admin/refresh/{job}`, `POST /admin/refresh-profile/{profile}` | owner (admin) | manual DATA refresh only; 409 unless mode is `RESEARCH_ONLY` and ingestion enabled |

## MCP server (Claude Web, read-only)

One separate read-only MCP server, `app.mcp.server`, with one endpoint `POST /mcp`: 12 research tools and 4
resources. It never writes, never trades, never connects to WSR/Trader View. See `docs/mcp_claude_web_setup.md`,
`docs/mcp_security.md` and `docs/mcp_deployment.md`.

```zsh
python -m scripts.mcp_test_client --mock
uvicorn app.mcp.server:get_app --factory --port 8080
```

## Dashboard, journal, score estimate, compliance audit

```zsh
pip install -e ".[dev,dashboard]"
python -m streamlit run app/dashboard/main.py                      # mock data (default)
DASHBOARD_MODE=supabase python -m streamlit run app/dashboard/main.py          # your data, local only (macOS/zsh; shell variable, not read from .env)
python -m scripts.compliance_audit                                 # final compliance audit (exit 1 on findings)
```

Nine pages, each stating: "Research only. Trades must be independently entered manually in Trader
View. This system cannot place or manage trades." See `docs/manual_monday_workflow.md`,
`docs/score_methodology.md`, `docs/troubleshooting.md`.

## Local development (Windows / PowerShell)

```powershell
cd "D:\Ary Fund\mini_competition_ai"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env        # then fill in values (docs/supabase_setup.md)

ruff check . ; ruff format --check .
pytest                              # fully mocked: no network or Supabase needed
python scripts/check_no_execution.py
python -m scripts.refresh_weekly_research --mock
python -m scripts.inspect_pair KO PEP --mock
python -m scripts.build_weekly_portfolio --mock      # Option A weekly draft (see docs/option_a_peer_pair_strategy.md)
uvicorn app.api.main:app --reload --port 8000
```

Call a protected endpoint in dev-token mode:

```powershell
curl -H "Authorization: Bearer $env:DEV_BEARER_TOKEN" http://127.0.0.1:8000/controls/status
```

Dev-token mode (`DEV_BEARER_TOKEN`) is **development only**: it uses the service role and bypasses RLS,
and the app refuses to start with it when `APP_ENV=production`.

## Safety design (enforced, not just documented)

- `system_control_state.signal_sending_enabled` has a `CHECK (... = false)`; it cannot be set true.
- Default mode `PAUSED`. If the row is missing, the API reports `PAUSED`.
- `manual_trades.source` and `manual_positions.data_status` are CHECK-constrained to `MANUAL`;
  `score_snapshots.is_estimate` is always true.
- RLS is enabled **and forced** on all 33 tables; owner-only. `anon` has no privileges.
- A NOLOGIN `mcp_readonly` Postgres role (SELECT only, `default_transaction_read_only`) is reserved for the future MCP endpoint.
- `scripts/check_no_execution.py` (also a pytest) fails on order/automation/browser/Telegram code or dependencies.
