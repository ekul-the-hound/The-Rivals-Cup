# Windows / PowerShell guide

The other setup guides show macOS/zsh commands. This page gives the **Windows PowerShell**
equivalents, in the same order. The steps, expected output and compliance rules are identical:
this system is research-only and **cannot place or manage trades**.

Open **PowerShell** (Windows PowerShell 5.1 or PowerShell 7) and run everything from the repo root
(for example `D:\Ary Fund\mini_competition_ai`). Keep the folder path in quotes because of the space.

## 1. Prerequisites (local_setup_guide A1-A2)

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
```

Close and reopen PowerShell, then:

```powershell
py -3.12 --version          # expect: Python 3.12.x
git --version               # expect: git version 2.x
cd "D:\Ary Fund\mini_competition_ai"
dir                         # expect: README.md  app  docs  pyproject.toml  scripts  supabase
```

## 2. Virtual environment and install (A3-A4)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell says scripts are disabled, run once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

```powershell
python --version            # expect: Python 3.12.x
python -m pip install --upgrade pip
pip install -e ".[dev,dashboard]"
pip list | Select-String -Pattern "fastapi|streamlit|supabase|pytest|ruff"
```

## 3. `.env` file (A5)

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Generate a development token (never reuse it):

```powershell
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

`DASHBOARD_MODE` and `MCP_AUDIT_LOG_FILE` are read from the **shell**, not `.env`:

```powershell
$env:DASHBOARD_MODE = "supabase"          # only affects this PowerShell window
Remove-Item Env:DASHBOARD_MODE            # back to mock mode
```

## 4. Mock demo, tests, guards (A6-A7)

Commands are identical on every OS:

```powershell
python -m scripts.refresh_weekly_research --mock
python -m scripts.inspect_pair KO PEP --mock
python -m scripts.build_weekly_portfolio --mock
python -m scripts.mcp_test_client --mock
python -m streamlit run app/dashboard/main.py        # open http://localhost:8501

pytest
ruff check .
ruff format --check .
python scripts/check_no_execution.py
python -m scripts.compliance_audit
```

## 5. Supabase CLI (supabase_setup.md section 3-5)

The CLI installs through Scoop on Windows:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression      # skip if Scoop is installed
scoop bucket add supabase https://github.com/supabase/scoop-bucket.git
scoop install supabase
supabase --version
```

If this has changed, see <https://supabase.com/docs/guides/local-development/cli/getting-started>.
After that the commands are the same as in `supabase_setup.md`: `supabase login`, `supabase init`,
`supabase link --project-ref <ref>`, `supabase db push --dry-run`, `supabase db push`.
Local Docker stack (`supabase start`) needs Docker Desktop.

Verify `.env` is ignored:

```powershell
git check-ignore -v .env              # expect a line mentioning .gitignore
```

## 6. Servers and tests (dashboard_and_mcp_runbook.md)

Use **`curl.exe`** (plain `curl` in Windows PowerShell 5.1 is an alias for something else).

```powershell
# API
$env:DEV_BEARER_TOKEN = python -c "import secrets;print(secrets.token_urlsafe(32))"
uvicorn app.api.main:app --reload --port 8000

# in a second PowerShell window (activate the venv again; set the same token)
curl.exe -s http://127.0.0.1:8000/health
curl.exe -s -H "Authorization: Bearer $env:DEV_BEARER_TOKEN" http://127.0.0.1:8000/controls/status
```

```powershell
# MCP server (dev token, development only) - keep audit lines in a git-ignored logs\ folder
$env:MCP_DEV_BEARER_TOKEN = python -c "import secrets;print(secrets.token_urlsafe(32))"
New-Item -ItemType Directory -Force logs | Out-Null
uvicorn app.mcp.server:get_app --factory --port 8080 2>&1 | Tee-Object -FilePath logs\mcp_audit.log -Append

# second window: expect HTTP 401 without a token
curl.exe -s -i -X POST http://127.0.0.1:8080/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
python -m scripts.mcp_test_client --url http://127.0.0.1:8080/mcp --token $env:MCP_DEV_BEARER_TOKEN --tool get_competition_rules_summary

# dashboard reading the audit file
$env:MCP_AUDIT_LOG_FILE = "logs\mcp_audit.log"
python -m streamlit run app/dashboard/main.py
```

Handy equivalents:

| macOS/zsh | PowerShell |
|---|---|
| `tail -f logs/mcp_audit.log` | `Get-Content logs\mcp_audit.log -Wait` |
| `lsof -i :8000` | `netstat -ano \| findstr :8000` |
| `rm -rf .venv` | `Remove-Item -Recurse -Force .venv` |
| `rm -rf .cache/providers` | `Remove-Item -Recurse -Force .cache\providers` |
| `export NAME=value` | `$env:NAME = "value"` |

Free-data setup (`data_provider_setup.md`): put `SEC_USER_AGENT` and `FRED_API_KEY` in `.env`; the
`python -m scripts.refresh_weekly_research ...` commands are identical. The optional connectivity
checks use `curl.exe`, for example:

```powershell
curl.exe -s -A "Your Name your@email.com" https://data.sec.gov/submissions/CIK0000021344.json
```

## 7. GitHub from Windows (github_workflow.md)

Target: <https://github.com/ekul-the-hound/The-Rivals-Cup>

```powershell
cd "D:\Ary Fund\mini_competition_ai"
git status
git branch --show-current                # expect: main
git remote -v                            # expect: origin https://github.com/ekul-the-hound/The-Rivals-Cup.git
```

If git says `detected dubious ownership`, run once:

```powershell
git config --global --add safe.directory "D:/Ary Fund/mini_competition_ai"
```

Authenticate (nothing secret is typed in the terminal; you approve in the browser):

```powershell
winget install --id GitHub.cli -e        # then reopen PowerShell
gh auth login                            # GitHub.com -> HTTPS -> Login with a web browser
gh auth status                           # expect: Logged in to github.com account ekul-the-hound
gh auth setup-git
```

Pre-push safety checks, then push:

```powershell
git diff HEAD --stat
git ls-files | Select-String -Pattern "(^|/)\.env($|\.)" | Where-Object { $_ -notmatch "\.env\.example" }   # expect: no output
git grep -nE "eyJ[A-Za-z0-9_-]{15,}|sbp_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{16,}|sb_secret_[A-Za-z0-9]{8,}|sb_publishable_[A-Za-z0-9]{8,}"   # expect: no output
git push -u origin main
```

Expected: `branch 'main' set up to track 'origin/main'.` Never use `--force`. If the push is rejected
because GitHub already has commits, stop and follow the "If the push is rejected" section of
`github_workflow.md` (fetch and inspect; merge, never force).

SSH alternative: `ssh-keygen -t ed25519 -C "your-github-email"`, then `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | clip`
and paste that **public** key at GitHub > Settings > SSH and GPG keys; `ssh -T git@github.com` should greet you.
Never share the file without `.pub`.
