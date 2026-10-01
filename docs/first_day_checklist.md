# First-day checklist (macOS / zsh)

Follow top to bottom. Each box has a command and what you should see. Stop at the first mismatch and
open `troubleshooting.md`. Nothing here places or manages trades; this system cannot.

Accounts/keys, in priority order: **(1)** Supabase free project, **(2)** your name+email for
`SEC_USER_AGENT`, **(3)** free FRED API key, **(4)** GitHub login. Yahoo, Google News and Wikipedia
need nothing. No Anthropic/OpenAI key is used.

## A. Local dashboard (no accounts)

- [ ] `brew install python@3.12 git` then `python3.12 --version` → `Python 3.12.x`
- [ ] `cd` into the repo root (`ls` shows `pyproject.toml`, `app`, `docs`, `scripts`, `supabase`)
- [ ] `python3.12 -m venv .venv && source .venv/bin/activate` → prompt starts with `(.venv)`
- [ ] `pip install -e ".[dev,dashboard]"` → ends with `Successfully installed ...`
- [ ] `cp -n .env.example .env` → no output; `.env` now exists
- [ ] `pytest` → all tests pass
- [ ] `ruff check .` → `All checks passed!`
- [ ] `python scripts/check_no_execution.py` → `OK: no execution/automation code found`
- [ ] `python -m scripts.compliance_audit` → last line `RESULT: PASS - ...`
- [ ] `python -m scripts.refresh_weekly_research --mock` → nine `SUCCEEDED` lines
- [ ] `python -m scripts.inspect_pair KO PEP --mock` → `PAIR INSPECTION  LONG KO / SHORT PEP`
- [ ] `python -m scripts.build_weekly_portfolio --mock` → `[RESEARCH_DRAFT]`
- [ ] `python -m scripts.mcp_test_client --mock` → every line `ok  research_only=True`
- [ ] `python -m streamlit run app/dashboard/main.py` → open <http://localhost:8501>; click through pages 1–9; each shows the research-only banner; `Ctrl+C` to stop

## B. Supabase and free data

- [ ] Create the free project (`supabase_setup.md` §1); save the database password in a password manager
- [ ] Copy Project URL, publishable/anon key, secret/service-role key into `.env` (§2). `git check-ignore -v .env` prints a match
- [ ] `brew install supabase/tap/supabase && supabase --version`
- [ ] `supabase login` → `You are now logged in.`
- [ ] `supabase init` (once) → `supabase/config.toml` exists
- [ ] `supabase link --project-ref <ref>` → `Finished supabase link.`
- [ ] `supabase db push --dry-run` lists 10 migrations; then `supabase db push` → `Finished supabase db push.`
- [ ] SQL editor RLS query returns `33 | 33 | 33`; seed counts `30 / 11 / 8 / 16` (`supabase_setup.md` §7–8)
- [ ] Add the owner user, copy its UUID to `OWNER_USER_ID`, disable sign-ups (§6)
- [ ] Run the `update system_control_state ... RESEARCH_ONLY` statement (§9); verify `RESEARCH_ONLY | false | true`
- [ ] Supabase connection test (§10) → `securities rows: 30`
- [ ] Set `SEC_USER_AGENT="Your Name your@email.com"` and `FRED_API_KEY` in `.env` (`data_provider_setup.md`)
- [ ] `python -m scripts.refresh_weekly_research --jobs refresh_universe` → `SUCCEEDED`
- [ ] `python -m scripts.refresh_weekly_research --profile sunday` → `SUCCEEDED`/`PARTIAL` lines, then `Default Monday candidates ...`
- [ ] `python -m scripts.inspect_pair KO PEP` → real report
- [ ] `python -m scripts.build_weekly_portfolio` → `[RESEARCH_DRAFT]`
- [ ] `DASHBOARD_MODE=supabase python -m streamlit run app/dashboard/main.py` → pages show real data, no "MOCK MODE" caption

## C. Claude reasoning

- [ ] Nothing to do: this code uses no Anthropic key. (Optional Claude Web via MCP is section D.)

## D. Optional: MCP locally, then remote (only after A and B pass)

- [ ] `export MCP_DEV_BEARER_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"`
- [ ] Terminal 1: `mkdir -p logs && uvicorn app.mcp.server:get_app --factory --port 8080 2>> logs/mcp_audit.log`
- [ ] Terminal 2: unauthenticated POST to `/mcp` returns `401`; `python -m scripts.mcp_test_client --url http://127.0.0.1:8080/mcp --token "$MCP_DEV_BEARER_TOKEN" --tool get_competition_rules_summary` → `ok`
- [ ] Public deployment and Claude Web: `dashboard_and_mcp_runbook.md` D2 (consent page is NOT IMPLEMENTED IN THIS REPOSITORY YET)

## E. GitHub

- [ ] Follow `github_workflow.md`: authenticate (`gh auth login`), verify no secrets, commit, `git push -u origin main`
- [ ] Open <https://github.com/ekul-the-hound/The-Rivals-Cup> and confirm **no `.env`** is visible in the file list

## Weekly rhythm (after day one)

Sunday: `--profile sunday`, fill the event blackout table, build the draft. Monday: enter trades
yourself in Trader View, then record them in dashboard page 5. See `docs/manual_monday_workflow.md`.
