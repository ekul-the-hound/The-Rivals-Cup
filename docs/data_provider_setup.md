# Free data provider setup (macOS / zsh)

Part B of the setup. Research data only: **nothing here connects to Wall Street Rivals, Trader View
or a broker.** For the provider design and rate limits see `docs/data_providers.md`; this guide is
the hands-on setup.

**Prerequisites:** finished `local_setup_guide.md` (venv + install) and `supabase_setup.md`
(project, migrations, `.env` with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, and step 9, the
`RESEARCH_ONLY` switch). Run everything from the repository root with the venv active
(`source .venv/bin/activate`).

## Providers implemented in this repository

Exactly five (files in `app/services/providers/`). There are **no other** providers; no
paid APIs, no earnings-calendar API, no Alpha Vantage/FMP/Marketaux, no Anthropic/OpenAI.

| Provider | Key needed? | `.env` variable | Rate limit in code | Used by job(s) |
|---|---|---|---|---|
| SEC EDGAR | No key. **Declared User-Agent required** | `SEC_USER_AGENT` | 8 req/s | `refresh_universe`, `refresh_sec_filings` |
| FRED | **Yes, free key** | `FRED_API_KEY` | 2 req/s | `refresh_macro_context` |
| Yahoo Finance (unofficial) | No | `WEB_USER_AGENT` (optional) | 1 req/s | `refresh_daily_prices`, `refresh_universe` |
| Google News RSS | No | `WEB_USER_AGENT` (optional) | 1 request / 2 s | `refresh_news` |
| Wikipedia MediaWiki API | No | `WEB_USER_AGENT` (optional) | 2 req/s | `refresh_universe` |

All providers retry 429/5xx with backoff, honor `Retry-After`, and cache responses under
`PROVIDER_CACHE_DIR` (`.cache/providers`, git-ignored). A provider with missing configuration is
**skipped with a message**, it does not crash the run.

### Priority order for accounts/keys

1. Supabase project (free) → `supabase_setup.md`
2. `SEC_USER_AGENT` (no account; just your name + email)
3. FRED API key (free account)
4. Nothing else is required. Yahoo, Google News RSS and Wikipedia need no signup.

---

## 1. SEC EDGAR

* **Key:** none. SEC fair-access policy requires you to identify yourself.
* **Set in `.env`:**

  ```
  SEC_USER_AGENT=Your Name your@email.com
  ```

  The code refuses to start the SEC provider unless the value looks like a name plus an email.
  (`.env` values with spaces need no quotes.) Optionally set `WEB_USER_AGENT` too; otherwise the
  SEC value is reused for Yahoo, Google News and Wikipedia.
* **Limits:** client limited to 8 req/s (SEC's ceiling is 10). Do not remove the limiter.
* **Test (through the repo, writes to your Supabase):**

  ```zsh
  python -m scripts.refresh_weekly_research --jobs refresh_universe
  ```

  Expected: `SUCCEEDED refresh_universe  read=16  wrote=16` (16 sample stocks). A line like
  `! XYZ: not found in SEC ticker map` is a warning for a ticker SEC does not list.
  Then:

  ```zsh
  python -m scripts.refresh_weekly_research --jobs refresh_sec_filings
  ```

  Expected: `SUCCEEDED refresh_sec_filings`, or `PARTIAL` with warnings.
* **Connectivity check without the repo** (optional; Coca-Cola's CIK):

  ```zsh
  curl -s -A "Your Name your@email.com" https://data.sec.gov/submissions/CIK0000021344.json | head -c 200
  ```

  Expected: JSON starting `{"cik":"0000021344",...`. An HTML "Request Rate Threshold" or 403 page
  means your User-Agent is missing/invalid or you were throttled.
* **Limitations:** filing metadata, bounded 8-K excerpts, Form 4 parsing, rule-based catalysts. 8-K Item
  2.02 (earnings releases) is deliberately ignored. No transcripts, no estimates.

## 2. FRED (macro)

* **Get a key (free):** create an account at <https://fredaccount.stlouisfed.org>, then
  **My Account → API Keys → Request API Key**. The key is a 32-character string.
* **Set in `.env`:**

  ```
  FRED_API_KEY=<your 32-character key>
  ```
* **Limits:** 2 req/s in code. Only five series are allowed (the code rejects any other):
  `DGS2`, `DGS10`, `T10Y2Y`, `FEDFUNDS`, `VIXCLS`.
* **Test:**

  ```zsh
  python -m scripts.refresh_weekly_research --jobs refresh_macro_context
  ```

  Expected: `SUCCEEDED refresh_macro_context  read=5  wrote=1`. Without a key you get
  `SKIPPED ... FRED_API_KEY not set`.
* **Limitations:** FRED publishes with delays and some series skip weekends/holidays (freshness rule: daily series 5 days,
  `FEDFUNDS` 45 days).

## 3. Yahoo Finance adapter

* **Key:** none. It is an **unofficial** JSON endpoint (`query1.finance.yahoo.com/v8/finance/chart`).
* **Limits:** 1 req/s; JSON endpoints only, no page scraping.
* **Test:**

  ```zsh
  python -m scripts.refresh_weekly_research --jobs refresh_daily_prices
  ```

  Expected: `SUCCEEDED refresh_daily_prices` with `read=` about 30 securities × ~130 bars, then in
  the SQL editor: `select count(*) from market_bars;` returns several thousand rows.
* **Limitations (important):** prices only. It is never used for filings, corporate actions or WSR
  limits. It can rate-limit or change format without notice. The company-profile endpoint
  (`quoteSummary`) often needs a crumb and may refuse, so market cap may be missing (flagged as an
  INFO data-quality issue). The first run after a long pause may take a couple of minutes.
  Holidays may produce a false STALE flag (no holiday calendar in the freshness check).

## 4. Google News RSS

* **Key:** none. Endpoint `news.google.com/rss/search`.
* **Limits:** 1 request / 2 s in code.
* **Test:**

  ```zsh
  python -m scripts.refresh_weekly_research --jobs refresh_news
  ```

  Expected: `SUCCEEDED refresh_news`. Verify with `select count(*) from news_items;`.
* **Limitations:** discovery only. Links are not fetched or verified beyond publisher domain and
  timestamp; news text shown in the dashboard/MCP is treated as **untrusted data**.
  Sources outside a small allow-list are marked UNVERIFIED.

## 5. Wikipedia MediaWiki API

* **Key:** none. Endpoint `en.wikipedia.org/w/api.php`. Set a real contact in `WEB_USER_AGENT` or
  `SEC_USER_AGENT` (Wikimedia asks for an identifying User-Agent).
* **Limits:** 2 req/s.
* **Test:** runs inside `refresh_universe` (section 1). Verify with
  `select count(*) from companies where wiki_fetched_at is not null;`.
* **Limitations:** static company context only; never treated as a catalyst or a source of truth for numbers.

## Derived jobs (no outside API)

`refresh_market_context`, `refresh_corporate_catalysts`, `refresh_data_quality` and
`build_weekly_event_blackout_list` compute from data already in Supabase.

## Run a whole refresh

```zsh
python -m scripts.refresh_weekly_research --profile sunday        # profiles: daily | sunday | monday | full
```

Expected: nine `SUCCEEDED`/`PARTIAL` lines, then
`Default Monday candidates for week <Monday>: N included, M excluded`. `PARTIAL` is normal (warnings,
e.g. one ticker missing). `FAILED` or `SKIPPED` lines are explained in the message and in
`provider_run_logs`:

```sql
select job_name, status, rows_read, rows_written, error_message, started_at
from provider_run_logs order by started_at desc limit 15;
```

Profiles and cadence live in `app/jobs/cadence.py`:

* `daily` after the cash close (Mon–Fri),
* `sunday` before the week (includes `refresh_universe` and the blackout list),
* `monday` pre-market freshen-up.

**Scheduler/worker: NOT IMPLEMENTED IN THIS REPOSITORY.** Nothing runs by itself; you run these
commands manually. (You could trigger *data* refreshes with your own OS scheduler, but that is
outside this repo and must never be used for anything trade-related.)

## Weekly event blackout (manual, no API)

There is no earnings-calendar provider. Each week, fill `security_event_blackouts`
(`earnings_date_if_known`, `known_major_event_date`, `event_risk_notes`, `manually_verified_at`,
`source_url`) in Dashboard → **Table editor** from sources you trust; `build_weekly_event_blackout_list`
pre-creates the empty rows. Pairs with a leg in blackout are excluded from the Monday list unless you
log an override in `pair_blackout_overrides` (10+ character reason).

## Free-plan expectations

Yahoo may throttle, Google News may return fewer items, FRED lags, and SEC rate limits are strict.
If a job returns `PARTIAL`, rerun just that job later:
`python -m scripts.refresh_weekly_research --jobs <job_name>`.
