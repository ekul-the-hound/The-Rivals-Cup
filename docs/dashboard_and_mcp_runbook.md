# Dashboard, API and MCP runbook (macOS / zsh)

Covers starting and testing every long-running piece. Run everything from the repository root with
the venv active (`source .venv/bin/activate`). **None of these processes can place or manage
trades**; the API and dashboard are research/bookkeeping tools and the MCP server is read-only.

## What runs, and what does not

| Process | Command | URL | Needs Supabase? |
|---|---|---|---|
| Dashboard (Streamlit) | `python -m streamlit run app/dashboard/main.py` | <http://localhost:8501> | No in mock mode (default); yes in `supabase` mode |
| REST API (FastAPI) | `uvicorn app.api.main:app --reload --port 8000` | <http://127.0.0.1:8000> | Yes for protected routes; `/health` needs nothing |
| MCP server (read-only) | `uvicorn app.mcp.server:get_app --factory --port 8080` | `http://127.0.0.1:8080/mcp` (POST only) | Yes for most tools (see below) |
| Scheduler / worker | **NOT IMPLEMENTED IN THIS REPOSITORY YET** | n/a | Refresh jobs are one-shot CLI commands, see `data_provider_setup.md` |

Use one terminal tab per long-running process. Stop any of them with `Ctrl+C`.

---

## A. Dashboard

### A1. Mock mode (no keys, default)

```zsh
python -m streamlit run app/dashboard/main.py
```

Expected: `Local URL: http://localhost:8501`. Open it; the sidebar lists nine pages:
1 Monday portfolio builder · 2 Peer-pair candidates · 3 Pair research detail · 4 Market/sector
dashboard · 5 Manual portfolio journal · 6 Estimated score & exposure · 7 Data quality · 8 MCP
status/audit · 9 Compliance status. Every page shows "Research only. Trades must be independently
entered manually in Trader View. This system cannot place or manage trades." and "MOCK MODE".

Health check from another tab: `curl -s http://localhost:8501/_stcore/health` → `ok`.

### A2. Supabase mode (your real data; local use only)

Requires `.env` with `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, and data from
`data_provider_setup.md`.

```zsh
DASHBOARD_MODE=supabase python -m streamlit run app/dashboard/main.py
```

`DASHBOARD_MODE` must be in the **shell** (it is not read from `.env`). The dashboard has **no login**
and uses the service-role key: run it only on your own machine, never expose port 8501 publicly.

Journal page (5) writes only to the manual-journal tables and records a trade **only after** you tick
"I have ALREADY entered ... myself in Trader View". It never sends anything anywhere.

---

## B. REST API

### B1. Start it (development, dev-token mode)

```zsh
export DEV_BEARER_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
uvicorn app.api.main:app --reload --port 8000
```

`APP_ENV` must be `development` (the default). Dev-token mode uses the service role and bypasses
RLS, so it is for your own machine only. If you use `.env`, put the same token in `DEV_BEARER_TOKEN=`
instead of exporting it.

### B2. Health endpoint (no auth)

```zsh
curl -s http://127.0.0.1:8000/health
```

Expected: `{"status":"ok","service":"wsr-rival-cup-research","version":"0.1.0","environment":"development",...}`.
Interactive docs: <http://127.0.0.1:8000/docs>.

### B3. Protected routes (need Supabase)

```zsh
H="Authorization: Bearer $DEV_BEARER_TOKEN"
curl -s -H "$H" http://127.0.0.1:8000/controls/status       # mode PAUSED/RESEARCH_ONLY, signal_sending_enabled false
curl -s -H "$H" http://127.0.0.1:8000/research/status       # row counts, latest runs
curl -s -H "$H" http://127.0.0.1:8000/data-quality
curl -s -H "$H" http://127.0.0.1:8000/pairs/candidates      # scored pairs with exclusion reasons
curl -s -H "$H" -X POST http://127.0.0.1:8000/pairs/build-weekly-portfolio   # saves a RESEARCH_DRAFT; 409 unless RESEARCH_ONLY
curl -s -H "$H" http://127.0.0.1:8000/pairs/weekly-portfolio
```

A missing/wrong token gives `401`. An unreachable or wrong Supabase gives `500 Internal Server Error`
(check the uvicorn terminal for the connection error). Full route table: `README.md`.

---

## C. Testing checklist (in order)

**Supabase connection** (needs `.env`): see `supabase_setup.md` step 10; expect `securities rows: 30`.

**Provider ingestion:** `python -m scripts.refresh_weekly_research --jobs refresh_macro_context`
(and the others in `data_provider_setup.md`); expect `SUCCEEDED`.

**Pair inspection** (real data):

```zsh
python -m scripts.inspect_pair KO PEP                 # LONG ticker first, SHORT second
python -m scripts.inspect_pair KO PEP --size-usd 25000 --json
```

Expected: the `PAIR INSPECTION` report (offline version: add `--mock`).

**Weekly peer-pair portfolio build:**

```zsh
python -m scripts.build_weekly_portfolio                 # real data; add --mock for offline
python -m scripts.build_weekly_portfolio --max-pairs 4   # 1-6; hard cap is 6
python -m scripts.build_weekly_portfolio --leg-usd 10000 # intended manual leg size for liquidity checks
python -m scripts.build_weekly_portfolio --persist       # also saves the draft to Supabase (pair_rankings, weekly_portfolios only)
python -m scripts.build_weekly_portfolio --json
```

Expected: `WEEKLY PEER-PAIR PORTFOLIO ... [RESEARCH_DRAFT]` and `RESEARCH DRAFT ONLY. No order, trade
or position has been created or implied.`

**Local MCP test client (no Claude needed):**

```zsh
python -m scripts.mcp_test_client --mock                                   # in-process, mock data, 12 tools + 4 resources
python -m scripts.mcp_test_client --mock --tool get_liquidity_check --args '{"ticker":"KO","intended_notional":10000}'
```

Expected: `ok  research_only=True` per tool.

**Dashboard startup:** section A. **Health endpoint:** B2.

---

## D. OPTIONAL: local MCP server, then public remote MCP / Claude Web

Do this only after everything in C passes.

### D1. Run the MCP server locally against a running server

Terminal 1:

```zsh
export MCP_DEV_BEARER_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"   # DEVELOPMENT ONLY, 32+ chars
mkdir -p logs
uvicorn app.mcp.server:get_app --factory --port 8080 2>> logs/mcp_audit.log
```

(Audit lines are JSON on stderr; the redirect keeps them in a git-ignored `logs/` folder.
For real data also have `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` set in `.env`; the dev
token reads with the service role.)

Terminal 2:

```zsh
curl -s -i -X POST http://127.0.0.1:8080/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -3
```

Expected: `HTTP/1.1 401 Unauthorized` (no token; this proves auth is enforced).

```zsh
python -m scripts.mcp_test_client --url http://127.0.0.1:8080/mcp --token "$MCP_DEV_BEARER_TOKEN" --tool get_competition_rules_summary
```

Expected: `server: rival-research 1.0.0  protocol 2025-06-18` then `get_competition_rules_summary: ok
research_only=True ...`. Tools that read market/pair data need a reachable Supabase with data.

Show audit lines on dashboard page 8:

```zsh
MCP_AUDIT_LOG_FILE=logs/mcp_audit.log DASHBOARD_MODE=supabase python -m streamlit run app/dashboard/main.py
```

### D2. Public remote MCP and Claude Web (optional)

Claude Web cannot reach `127.0.0.1`, and it connects with **OAuth, not the dev token**. Follow, in order:

1. `docs/mcp_deployment.md`: container (`Dockerfile.mcp`, port 8080), production environment
   (`APP_ENV=production`, `MCP_PUBLIC_URL=https://.../mcp`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   `OWNER_USER_ID`; **do not** set the service-role key, dev tokens or provider keys on the public
   host), public HTTPS.
2. `docs/mcp_claude_web_setup.md`: Claude Web connector steps (callback
   `https://claude.ai/api/mcp/auth_callback`, PKCE).
3. `docs/mcp_security.md`: what the server can and cannot do.

Known gap: Supabase OAuth for a third-party client needs a consent page you host; **that page is
NOT IMPLEMENTED IN THIS REPOSITORY YET** (see `docs/mcp_deployment.md`, "Authentication" step 4). Until it exists,
treat Claude Web connection as not ready and use the local test client.

The MCP server exposes exactly 12 read-only tools and 4 read-only resources. It has no write tools,
no Supabase writes, no shell access, and cannot trade.

---

## Logs: where to look

| What | Where |
|---|---|
| API / MCP / dashboard output | the terminal where you started each process |
| MCP per-call audit (metadata only: request id, tool, argument hash, status, time) | stderr; with the redirect above: `logs/mcp_audit.log`; dashboard page 8 |
| Provider job history | SQL `provider_run_logs` (see `data_provider_setup.md`) |
| Data-quality issues | dashboard page 7, `GET /data-quality`, table `data_quality_issues` |
| Cached provider responses | `.cache/providers/` (safe to delete) |

Follow a log live: `tail -f logs/mcp_audit.log`.
