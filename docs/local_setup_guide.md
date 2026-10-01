# Local setup guide (macOS / zsh)

**What this project is.** A research-only, Supabase-backed system for a *manual* U.S. equity
pair-trading workflow. You enter every trade yourself in Trader View. **This system cannot place
or manage trades**: it contains no broker, Wall Street Rivals, or Trader View connection, no order
code, no browser automation, no scheduled-order feature and no Telegram trading. See
`docs/competition_compliance.md`.

How the guides fit together (each part is independent, do them in this order):

| Part | What it gives you | Needs accounts/keys? | Guide |
|---|---|---|---|
| **A. Local dashboard** | Dashboard, mock demo, tests, local MCP test client | **None** | this file |
| **B. Free data refresh** | Real prices, filings, news, macro in your Supabase | Supabase, SEC User-Agent, FRED key | `supabase_setup.md`, `data_provider_setup.md` |
| **C. Claude reasoning** | Not used by this code | **None. Not applicable** | see "Claude / Anthropic" below |
| **D. Public remote MCP / Claude Web** | Claude Web can read your research data | Public HTTPS host, Supabase OAuth | `dashboard_and_mcp_runbook.md` |
| **E. GitHub** | Version control and backup | GitHub login | `github_workflow.md` |

> **Windows users:** the repository README keeps a PowerShell section. Everything below is
> macOS/zsh. In PowerShell, activate with `.\.venv\Scripts\Activate.ps1` and set environment
> variables with `$env:NAME="value"`.

---

## A. Required for the local dashboard

### A1. Prerequisites

You need Homebrew, Git and Python **3.12 or newer** (`pyproject.toml` says `requires-python >= 3.12`).

```zsh
brew --version                      # prints "Homebrew 4.x" (install from https://brew.sh if missing)
brew install python@3.12 git
python3.12 --version                # expect: Python 3.12.x
git --version                       # expect: git version 2.x
```

### A2. Get the code and enter the folder

If the code is already on your Mac (for example from the Claude desktop app), go to that folder.
Otherwise, after the first push (see `github_workflow.md`):

```zsh
git clone https://github.com/ekul-the-hound/The-Rivals-Cup.git
cd The-Rivals-Cup
ls                                  # expect: README.md  app  docs  pyproject.toml  scripts  supabase ...
```

Always run every command in this guide **from the repository root** (the folder that contains
`pyproject.toml`). The code imports `app` and `scripts` relative to it, and `.env` is read relative to it.

### A3. Create and activate a virtual environment

```zsh
python3.12 -m venv .venv
source .venv/bin/activate
python --version                    # expect: Python 3.12.x
which python                        # expect: .../The-Rivals-Cup/.venv/bin/python
```

Your prompt now starts with `(.venv)`. Run `source .venv/bin/activate` in every new terminal.
Leave it with `deactivate`.

### A4. Install dependencies

This repository's only dependency file is `pyproject.toml` (there is **no** `requirements.txt`,
**no** `Makefile`, and **no** `package.json`). Install the app plus dev tools plus the dashboard:

```zsh
python -m pip install --upgrade pip
pip install -e ".[dev,dashboard]"
```

Verify:

```zsh
pip list | grep -iE "^(fastapi|uvicorn|pydantic|supabase|httpx|pandas|numpy|streamlit|pytest|ruff) "
```

Expect all of these names to be listed. Runtime dependencies: fastapi, uvicorn, pydantic,
pydantic-settings, supabase, httpx, pandas, numpy. Extra `dev`: pytest, ruff. Extra `dashboard`: streamlit.

### A5. Create your `.env` file (needed later; safe to create now)

```zsh
cp .env.example .env
```

`.env` is ignored by git (never commit it). Open it in your editor and keep the placeholders for now;
the mock demo needs **no** values. Every field in `.env.example` and what it is for:

| Variable | Needed for | Meaning / default |
|---|---|---|
| `APP_ENV` | all | `development` (default) or `production`. Dev-token modes are refused in `production`. |
| `APP_VERSION` | optional | shown by `/health` (default `0.1.0`). |
| `DISPLAY_TIMEZONE` | optional | default `America/Chicago`; used for display times. |
| `SUPABASE_URL` | B, dashboard "supabase" mode, API, MCP | `.env.example` default is the local stack `http://127.0.0.1:54321`; for a hosted project use `https://<ref>.supabase.co`. |
| `SUPABASE_ANON_KEY` | API owner-JWT mode, MCP production | public ("anon"/publishable) key. See `supabase_setup.md`. |
| `SUPABASE_SERVICE_ROLE_KEY` | B (refresh scripts), dashboard "supabase" mode, dev-token API/MCP | **server-side secret**. Never expose it. |
| `OWNER_USER_ID` | API/MCP when not using a dev token | UUID of your single Supabase Auth owner user. |
| `DEV_BEARER_TOKEN` | local API testing only | **development only**, 24+ characters; refused when `APP_ENV=production`. |
| `SEC_USER_AGENT` | B | `"Your Name your@email.com"` (SEC requires it). |
| `FRED_API_KEY` | B | free FRED key. |
| `WEB_USER_AGENT` | optional | UA for Yahoo / Google News / Wikipedia; defaults to `SEC_USER_AGENT`. |
| `PROVIDER_CACHE_DIR` | optional | default `.cache/providers` (git-ignored). |
| `WSR_EST_ADV_PCT_CAP` | optional | ESTIMATE only: assumed max leg size as a fraction of 20-day dollar volume (0.01). Not a verified WSR rule. |
| `DEFAULT_LEG_SIZE_USD` | optional | default 10000, used for liquidity checks. |
| `MCP_PUBLIC_URL`, `MCP_OAUTH_ISSUER`, `MCP_ALLOWED_ORIGINS`, `MCP_RATE_LIMIT_PER_MINUTE`, `MCP_MAX_RESPONSE_BYTES` | D | MCP server settings. |
| `MCP_DEV_BEARER_TOKEN` | local MCP testing | **development only**, 32+ characters. |

`HTTP_TIMEOUT_SECONDS` also exists in `app/config/settings.py` (default 20) but is not in `.env.example`.

Two more variables are read **from your shell, not from `.env`** (the dashboard reads them directly
from the process environment): `DASHBOARD_MODE` (`mock` default, or `supabase`) and
`MCP_AUDIT_LOG_FILE`. Set them on the command line, e.g. `DASHBOARD_MODE=supabase python -m streamlit ...`.

Generate a development token whenever you need one (never reuse it anywhere else):

```zsh
python -c "import secrets;print(secrets.token_urlsafe(32))"      # prints a 43-character random string
```

### A6. Run the full mock demo (no keys, no internet, no Supabase)

```zsh
python -m scripts.refresh_weekly_research --mock
```

Expected: nine lines starting with `SUCCEEDED` (refresh_universe ... refresh_data_quality) followed by
`Default Monday candidates for week <Monday>: 7 included, 1 excluded` and
`EXCLUDED HD / LOW: HD: earnings on ... falls in scoring week`.

```zsh
python -m scripts.inspect_pair KO PEP --mock
```

Expected: a report starting `PAIR INSPECTION  LONG KO / SHORT PEP` and
`RESEARCH ONLY - enter any trade manually in Trader View.`, with sections Relationship, Returns,
Relative strength, Correlation, Price / volume / liquidity.

```zsh
python -m scripts.build_weekly_portfolio --mock
```

Expected: `WEEKLY PEER-PAIR PORTFOLIO  week ... [RESEARCH_DRAFT]`,
`RESEARCH DRAFT ONLY. No order, trade or position has been created or implied.` and a ranked list
of pairs (`#1 LONG KO / SHORT PEP ...`). Numbers are synthetic.

```zsh
python -m scripts.mcp_test_client --mock
```

Expected: one JSON audit line plus one result line per tool, each `ok  research_only=True`,
for all 12 tools and the 4 resources (`rules://...`, `market://...`, `portfolio://...`, `quality://...`).

Start the dashboard in mock mode:

```zsh
python -m streamlit run app/dashboard/main.py
```

Expected terminal output includes `Local URL: http://localhost:8501`. Open that URL. Every one of
the nine pages shows the banner "Research only. Trades must be independently entered manually in
Trader View. This system cannot place or manage trades." and a "MOCK MODE" caption. Stop with `Ctrl+C`.
(If Streamlit asks for your email on first start, press Enter to skip.)

### A7. Run the tests, lint, format check and compliance guards

```zsh
pytest                                  # expect: all tests pass; no network and no Supabase needed
ruff check .                            # expect: All checks passed!
ruff format --check .                   # expect: N files already formatted
python scripts/check_no_execution.py    # expect: OK: no execution/automation code found
python -m scripts.compliance_audit      # expect: ten [PASS] lines then "RESULT: PASS - ..."
```

Type checking (mypy/pyright) is **NOT IMPLEMENTED IN THIS REPOSITORY YET**; add a `[tool.mypy]`
section and a dev dependency if you want it.

---

## C. Claude / Anthropic: not used by this code

The repository contains **no** Anthropic or OpenAI client, no `ANTHROPIC_API_KEY` field and no code
that calls an LLM. The dashboard, API, jobs and MCP server all start and work without one.
"Claude reasoning" happens only if *you* connect Claude Web to the optional MCP server (Part D);
that uses your Claude account, not an API key stored here. So there is nothing to create, no
tokens to burn, and nothing to test. If a future feature needs a key it must be added to
`.env.example` as a placeholder and documented here.

---

## What next?

1. Part B: `supabase_setup.md` then `data_provider_setup.md`.
2. Run servers and tests: `dashboard_and_mcp_runbook.md`.
3. Follow `first_day_checklist.md` line by line.
4. Push to GitHub: `github_workflow.md`.
5. Problems: `troubleshooting.md`.
