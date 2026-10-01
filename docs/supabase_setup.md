# Supabase setup (macOS / zsh)

Part B of the setup. You need a free Supabase project only for **real data** (provider refresh,
dashboard in `supabase` mode, API/MCP against your data). The mock demo in
`local_setup_guide.md` needs none of this.

**Security rules for this guide:** never paste a key into chat, a commit, an issue or a screenshot;
the service-role (secret) key goes only into your local, git-ignored `.env`.

---

## 1. Create a free Supabase project

1. Go to <https://supabase.com> and sign in (GitHub login is fine).
2. **New project** → pick an organization → name it (for example `rivals-cup`) → choose the **Free** plan.
3. Set a **database password** and store it in a password manager. You can reset it later
   (Project Settings → Database). The Python code never uses it; only the Supabase CLI and `psql` do.
4. Pick the region closest to you and click **Create new project**. Wait until provisioning finishes
   (a couple of minutes). Use **one** project only.

Free-plan note: free projects can pause after a period of inactivity; if the dashboard shows
"paused", click **Restore** before running anything.

## 2. Find the values this code needs

Open your project. Values live in **Project Settings → API Keys** (also reachable via the **Connect**
button at the top of the dashboard) and **Project Settings → API / Data API** for the URL.

| `.env` variable | Where to find it | Used by |
|---|---|---|
| `SUPABASE_URL` | Project URL, `https://<project-ref>.supabase.co` | everything that talks to Supabase |
| `SUPABASE_ANON_KEY` | the **anon / publishable** key (public key) | API owner-JWT mode; MCP production mode |
| `SUPABASE_SERVICE_ROLE_KEY` | the **service_role / secret** key | refresh scripts, dashboard `supabase` mode, dev-token API/MCP |
| `OWNER_USER_ID` | UUID of your owner user (step 6) | API/MCP when not using a dev token |

**Key formats (current as of this writing).** Supabase's docs list two systems working side by
side: *legacy* JWT-style `anon` and `service_role` keys (long strings starting `eyJ`, described as
deprecated by end of 2026) and the new `sb_publishable_...` and `sb_secret_...` keys. Official docs:
<https://supabase.com/docs/guides/getting-started/api-keys>.

* Put the **secret** key in `SUPABASE_SERVICE_ROLE_KEY` and the **publishable** key in
  `SUPABASE_ANON_KEY`. The names are historical; this code just passes them to `supabase-py`.
* `supabase-py` 2.31 (what `pip install` gives you today) creates clients with both key formats
  (verified offline). Whether every owner-JWT flow works end to end with the *new* key format against
  a live project is **UNVERIFIED**. If something returns 401/"Invalid API key", try the legacy
  keys while your project still offers them.
* A secret key only works server-side. That matches this repo: it is used by local scripts only.

**Database connection details** (host, port, user, password) are **not** used by any Python code in
this repository. You need them only for the Supabase CLI (`supabase link` asks for the database
password) or if you choose to use `psql`. Find them via **Connect** → *Direct / Session pooler*.

### Put the values in `.env`

```zsh
cp -n .env.example .env             # -n: never overwrite an existing .env
open -e .env                        # or: nano .env
```

Fill in (leave the others as they are):

```
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_ANON_KEY=<publishable or anon key>
SUPABASE_SERVICE_ROLE_KEY=<secret or service_role key>
OWNER_USER_ID=<filled in step 6>
```

Verify the file is ignored and values are not tracked:

```zsh
git check-ignore -v .env            # expect a line mentioning .gitignore:2:.env   .env
```

(If the folder is not a git repo yet, do `github_workflow.md` step 1 first.)

---

## 3. Install and authenticate the Supabase CLI

The CLI is **required** to push the migrations the standard way (you can alternatively paste the SQL
files into the dashboard SQL editor, see 5b).

```zsh
brew install supabase/tap/supabase
supabase --version                  # expect a version number, e.g. 2.x.x
supabase login                      # opens a browser; approve. Do NOT paste tokens into chat or files
```

Expected: `You are now logged in. Happy coding!`.

## 4. Link this folder to your hosted project (safely)

The repository does **not** contain `supabase/config.toml` (only `migrations/` and `functions/`).
Create it once; this keeps your existing migrations:

```zsh
supabase init                       # creates supabase/config.toml; answer "N" to the VS Code/IDE prompts
ls supabase                         # expect: config.toml  functions  migrations
```

Find your project ref: it is the part before `.supabase.co` in your Project URL.

```zsh
supabase link --project-ref <your-project-ref>      # prompts for the database password
```

Expected: `Finished supabase link.` Linking writes local state under `supabase/.temp/`, which is git-ignored.
Check you linked the right project: `supabase projects list` shows a `LINKED` marker next to it.

## 5. Apply all migrations in the correct order

There are **10 migrations**, applied automatically in filename (timestamp) order:

```
20261001000001_enums_and_helpers.sql
20261001000002_core_tables.sql
20261001000003_market_research_tables.sql
20261001000004_manual_portfolio_tables.sql
20261001000005_ops_tables.sql
20261001000006_triggers_rls_grants.sql
20261001000007_seed_reference_data.sql
20261002000001_free_data_stack.sql
20261003000001_peer_pair_portfolio.sql
20261004000001_manual_journal.sql
```

### 5a. With the CLI (recommended)

```zsh
supabase migration list             # Local column lists 10 files; Remote column is empty on a new project
supabase db push --dry-run          # shows what WOULD be applied; changes nothing
supabase db push                    # asks to confirm, then applies all 10
supabase migration list             # now Local and Remote both show all 10
```

Expected last line of the push: `Finished supabase db push.`

### 5b. Without the CLI

Dashboard → **SQL Editor** → New query. Open each file in the order above, paste, **Run**. Run each
exactly once; stop and read the error if one fails. Do not run them out of order.

### Local alternative (Docker)

Instead of a hosted project you can run Supabase locally (Docker Desktop required):

```zsh
supabase start                      # prints API URL, anon key, service_role key (local-only keys)
supabase db reset                   # applies all migrations to the LOCAL database (wipes it first)
```

The `.env.example` default `SUPABASE_URL=http://127.0.0.1:54321` matches this local stack.

## 6. Create the single owner user

1. Dashboard → **Authentication → Users → Add user → Create new user**: enter your email and a
   password, tick auto-confirm if offered. Do this **before anyone else exists**: a database trigger
   (`handle_new_user`) makes the *first* `auth.users` row the owner (`profiles.is_owner = true`);
   later accounts are never owners and RLS shows them nothing.
2. Copy that user's **UUID** into `.env` as `OWNER_USER_ID`.
3. Dashboard → **Authentication → Sign In / Providers** (or Settings): turn **off** the option that
   allows new users to sign up. (Exact label changes between dashboard versions.)

## 7. Confirm tables, RLS and seed data

Dashboard → **SQL Editor**, run:

```sql
-- every public table, with RLS enabled AND forced
select count(*)                                  as tables,
       count(*) filter (where relrowsecurity)    as rls_enabled,
       count(*) filter (where relforcerowsecurity) as rls_forced
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r';
```

Expected: `33 | 33 | 33`.

```sql
select count(*) as policies from pg_policies where schemaname = 'public';       -- expected: 104
select has_table_privilege('anon', 'public.securities', 'select') as anon_can_read;   -- expected: false
select rolname from pg_roles where rolname = 'mcp_readonly';                    -- expected: 1 row
select email, is_owner from profiles;                                           -- expected: exactly your owner row, true
select mode, signal_sending_enabled, provider_ingestion_enabled
from system_control_state;                                                      -- expected: PAUSED | false | false
```

(Expected counts come from applying these exact migrations to a fresh Postgres; a project with extra
objects you created yourself may differ.)

## 8. Seed data (already included)

The seed lives **inside migration 7** (plus metadata updates in migration 9); there is no separate
seed script. After step 5:

```sql
select count(*) from securities;              -- expected: 30  (3 benchmarks SPY/QQQ/IWM, 11 sector ETFs, 16 sample stocks)
select count(*) from sector_etf_mappings;     -- expected: 11
select count(*) from peer_pairs;              -- expected: 8   (KO/PEP, HD/LOW, V/MA, XOM/CVX, JPM/BAC, UPS/FDX, MRK/PFE, AMD/INTC)
select count(*) from peer_pair_members;       -- expected: 16
```

These pairs are **samples, not recommendations**; `wsr_eligibility` is `UNVERIFIED` until you check
it manually in Trader View. You can edit `securities`, `peer_pairs`, `peer_pair_members`,
`sector_etf_mappings` as the owner.

Market data, company info, filings, news and macro rows are filled by the refresh jobs
(`data_provider_setup.md`), including `refresh_universe`, which enriches the securities/companies.

## 9. Turn data refresh on (deliberately)

All refresh jobs refuse to run while the system is `PAUSED`. In the SQL editor:

```sql
update system_control_state
set mode = 'RESEARCH_ONLY', provider_ingestion_enabled = true, reason = 'data refresh on'
where id = 1;
```

`signal_sending_enabled` cannot be set to true (a CHECK constraint makes it impossible); there is no
feature behind it.

## 10. Test the connection from Python

```zsh
source .venv/bin/activate
python - <<'PY'
from app.config import get_settings
from app.db.store import SupabaseStore, create_supabase_client
s = get_settings()
c = create_supabase_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
print("securities rows:", SupabaseStore(c).count("securities"))
PY
```

Expected: `securities rows: 30`. A connection error means the URL or key is wrong, or the project is
paused (see `troubleshooting.md`).

## Notes

* Ingestion tables (bars, quotes, packets, rankings, audit logs) are written only with the
  service-role key; the owner can edit only the mapping and manual-entry tables (enforced by RLS policies in migrations 6, 9 and 10).
* For the optional public MCP, `mcp_readonly` is a NOLOGIN role reserved for it; production MCP uses
  the anon key plus your owner JWT (see `dashboard_and_mcp_runbook.md`, Part D).
* Rerunning/resetting: see `troubleshooting.md` ("Reset data and rerun migrations safely").
