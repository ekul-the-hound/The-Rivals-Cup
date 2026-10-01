# Troubleshooting (macOS / zsh)

Always: run commands from the repository root, with the venv active
(`source .venv/bin/activate`), and read the **first** error line, not the last.

## Quick table

| Symptom | Fix |
|---|---|
| `zsh: command not found: python` | Activate the venv (`source .venv/bin/activate`) or use `python3.12`. |
| `requires-python >=3.12` error on install | You used an older Python. `brew install python@3.12`, recreate the venv: `rm -rf .venv && python3.12 -m venv .venv`. |
| `ModuleNotFoundError: No module named 'app'` | You are not in the repo root, or the venv is not active. `cd` to the folder with `pyproject.toml`. |
| `ModuleNotFoundError: streamlit` / `command not found: streamlit` | `pip install -e ".[dev,dashboard]"`, then `python -m streamlit run app/dashboard/main.py`. |
| Dashboard shows "MOCK MODE" | That is the default. Use `DASHBOARD_MODE=supabase python -m streamlit run ...` (shell variable; **not** read from `.env`). |
| Dashboard page errors in supabase mode | Check `.env` has `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; run the connection test in `supabase_setup.md` step 10. |
| Port already in use (8000/8080/8501) | `lsof -i :8000` to find the process; stop it, or choose another `--port` / `--server.port`. |
| `.env` changes ignored | Restart the process. Settings are cached at startup; the file is read from the **current directory**. |
| `401` from API | Missing/incorrect `Authorization: Bearer ...`; `DEV_BEARER_TOKEN` must be set in the **server's** environment and be 24+ chars; `APP_ENV` must be `development`. |
| `500` from API protected route | Supabase unreachable or wrong key. Read the uvicorn terminal; test connection (step 10 of `supabase_setup.md`). |
| MCP server refuses to start | Production mode needs `MCP_PUBLIC_URL` (https), `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OWNER_USER_ID`, and must **not** have `MCP_DEV_BEARER_TOKEN`. In development the dev token must be 32+ chars. |
| MCP client: `HTTP 401 missing_token` | Pass `--token "$MCP_DEV_BEARER_TOKEN"` and make sure the server was started with the **same** value, `APP_ENV=development`. |
| Job says `SKIPPED ... system mode is PAUSED` | Run the `update system_control_state ... RESEARCH_ONLY` statement (`supabase_setup.md` step 9). |
| Job says `SKIPPED ... FRED_API_KEY not set` | Add `FRED_API_KEY` to `.env`. |
| Job says `SEC_USER_AGENT must look like 'Your Name you@example.com'` | Fix `SEC_USER_AGENT` in `.env` (name + email). |
| `PARTIAL` job | Warnings only (e.g. a ticker missing from a provider). Rerun that job later: `--jobs <name>`. |
| Yahoo data empty / 429 | Unofficial endpoint; wait and rerun `--jobs refresh_daily_prices`. Responses are cached in `.cache/providers`. |
| `supabase db push` says migrations out of order / already exists | Do **not** edit applied migrations. See "Rerun migrations safely" below. |
| Supabase project "paused" | Dashboard → **Restore project**, wait, retry. |
| Journal refuses to record | Tick the "already entered in Trader View" box; the ticker must exist in `securities`. |
| `DIVIDENDS_NOT_CHECKED` warning | No rows in `dividend_events` for that ticker. Add ex-date and amount per share, or verify in Trader View. |
| `STALE_MARK` / `NO_MARK_DATA` warning | Run the daily refresh (`--profile daily`); Yahoo may lag. The estimate carries the last close forward. |
| Score differs from Trader View | Expected: the dashboard score is an ESTIMATE; Trader View is authoritative. |
| Page 8 shows no audit lines | Start the MCP server with `2>> logs/mcp_audit.log` and run the dashboard with `MCP_AUDIT_LOG_FILE=logs/mcp_audit.log`. |
| Compliance audit FAIL | Output names file:line and rule. Remove the offending code; never allow-list execution code. |
| Claude Web cannot connect | Optional feature; see `mcp_claude_web_setup.md` and the "NOT IMPLEMENTED" consent-page gap in `dashboard_and_mcp_runbook.md`. |
| `git push` rejected / authentication failed | See `github_workflow.md`. Never force-push. |

## Inspect logs

* API, MCP and Streamlit print to the terminal where you started them.
* MCP audit lines (JSON, metadata only): `tail -f logs/mcp_audit.log` (see runbook D1).
* Provider history: in the SQL editor `select job_name, status, rows_read, rows_written, error_message, started_at from provider_run_logs order by started_at desc limit 20;`
* Data quality: dashboard page 7, or `select * from data_quality_issues where resolved_at is null order by detected_at desc;`.

## Re-run the tests

```zsh
pytest -x                      # stop at the first failure
pytest app/tests/test_scenarios.py -k "F"    # one scenario
ruff check . && ruff format --check .
```

Tests are fully mocked (no network, no Supabase).

## Reset data and rerun migrations safely

**Local Docker stack (disposable):**

```zsh
supabase db reset              # drops the LOCAL database and reapplies all 10 migrations + seed
```

**Hosted project:** there is no safe one-line reset; do **not** drop the schema. Choose what you need:

1. *Migrations not yet applied (new files only):* `supabase migration list`, then `supabase db push --dry-run`, then `supabase db push`. Pushing is idempotent per file: already-applied files are skipped.
2. *A migration failed half-way:* read the error, fix the cause (e.g. missing earlier migration), and run `supabase db push` again. Never edit a migration that is already applied on the remote; add a **new** file with a later timestamp instead.
3. *Clear only ingested data* (keeps seed pairs, your manual blackout rows, journal records and the owner): in the SQL editor,

   ```sql
   delete from pair_weekly_eligibility;
   delete from weekly_portfolios;
   delete from pair_rankings;
   delete from research_packets;
   delete from news_items;
   delete from corporate_catalysts;
   delete from filing_documents;
   delete from daily_quotes;
   delete from liquidity_metrics;
   delete from market_bars;
   delete from market_context_snapshots;
   delete from macro_context_snapshots;
   delete from data_quality_issues;
   delete from provider_run_logs;
   ```

   Then rerun `python -m scripts.refresh_weekly_research --profile sunday`. **Do not** delete from `manual_*`, `security_event_blackouts`, `pair_blackout_overrides`, `pair_manual_decisions` or `profiles` unless you really mean to lose that manual work.
4. *Start completely over:* create a new Supabase project, `supabase link` to it, and push the migrations again.

Local-only cleanup: `rm -rf .cache/providers` (provider response cache) and `rm -rf logs/` (log files).

## Reporting something odd

Never paste keys, tokens or `.env` contents anywhere. Share the command you ran and the first error
line only.
