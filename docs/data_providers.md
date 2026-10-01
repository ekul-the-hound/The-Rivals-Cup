# Free data stack (Monday-entered peer-pair workflow)

Research data only. Nothing here touches Wall Street Rivals, Trader View, or a broker.

## Enabled providers

| Provider | Used for | Auth / limits | Authority |
|---|---|---|---|
| SEC EDGAR | ticker->CIK, 8-K/10-Q/10-K/Form 4 metadata, bounded 8-K excerpts, Form 4 parse, catalyst rules | `SEC_USER_AGENT="Name email"` required (provider refuses to start otherwise); client limited to 8 req/s | Primary for filings |
| FRED | DGS2, DGS10, T10Y2Y, FEDFUNDS, VIXCLS (code rejects any other series) | `FRED_API_KEY`; 2 req/s | Official macro |
| Yahoo Finance | daily OHLCV + adjusted close, last quote (from the same chart call), sector ETF/peer prices, market cap if available, 20d ADV estimate | none; **unofficial**, 1 req/s; JSON endpoints only, no page scraping | Prices only. Never for filings, corporate actions, or WSR limits |
| Google News RSS | recent news discovery per ticker/company, peer group, sector; syndication de-dupe; source class | 1 req / 2 s | Discovery. Links are not fetched or verified beyond domain + timestamp |
| Wikipedia API | static description, industry, products, competitor hint | 2 req/s | Context only; never a catalyst |

Every provider: async `httpx` client, rate limiter, exponential backoff + jitter (retries 429/5xx/timeouts, honors `Retry-After`, never retries other 4xx), TTL file cache (`.cache/providers`, secrets excluded from cache keys), per-job request/cache/retry counts written to `provider_run_logs`, and a mockable transport (`app/services/providers/mock_transport.py`).

Yahoo caveats: the `quoteSummary` profile endpoint often needs a crumb and may refuse; market cap is then simply missing and flagged as an INFO data-quality issue. The Yahoo adapter is isolated in one file so it can be swapped.

## Source classes (news)
PRIMARY: company wire releases / SEC domains (Business Wire, PR Newswire, GlobeNewswire, sec.gov). SECONDARY: a short allow-list of major outlets. Everything else UNVERIFIED. `link_confirmed` = a publisher domain and a parseable timestamp exist.

## Freshness rules (`app/services/validation/freshness.py`)
Daily bars must reach the last weekday before today (no holiday calendar: holidays can raise a false STALE). FRED daily series 5 days, FEDFUNDS 45 days. Last successful SEC refresh 3 days. News 7 days. Liquidity 5 days. Wikipedia profile 180 days. Manual blackout verification 7 days.

## Control-state gate
All jobs (CLI, admin endpoint) refuse to run unless `system_control_state.mode = 'RESEARCH_ONLY'` **and** `provider_ingestion_enabled = true`. Default is PAUSED / false, so enable deliberately (SQL editor):

```sql
update system_control_state set mode = 'RESEARCH_ONLY', provider_ingestion_enabled = true, reason = 'data refresh on';
```
`signal_sending_enabled` still cannot be true (CHECK constraint).

## Weekly event blackout (manual)
Table `security_event_blackouts`, one row per security per scoring week (`week_start` = Monday): `earnings_date_if_known`, `known_major_event_date`, `event_risk_notes`, `manually_verified_at`, `source_url`. There is **no earnings calendar API**; you fill these from sources you trust (Dashboard > Table editor, as the owner). `build_weekly_event_blackout_list` pre-creates empty rows for every pair leg so you only fill in dates.

A pair is excluded from the default Monday list if either leg's earnings or major-event date falls Mon-Fri of the scoring week. Override: insert a row in `pair_blackout_overrides` (`pair_id`, `week_start`, `reason` of 10+ characters). It is insert-only (a log), and the pair is then eligible with the reason shown. Results are stored in `pair_weekly_eligibility`.

## Running

```powershell
python -m scripts.refresh_weekly_research --profile sunday      # or daily | monday | full
python -m scripts.refresh_weekly_research --jobs refresh_news,refresh_data_quality
python -m scripts.refresh_weekly_research --mock                # offline, synthetic data
python -m scripts.inspect_pair KO PEP                           # LONG then SHORT ticker
python -m scripts.inspect_pair KO PEP --size-usd 25000 --json
python -m scripts.inspect_pair KO PEP --mock                    # full offline demo
```

## Cadence (no intraday scanning; nothing in this repo schedules itself)
Profiles live in `app/jobs/cadence.py`. Trigger them yourself, e.g. Windows Task Scheduler (America/Chicago local time):

```powershell
$act = { param($p) New-ScheduledTaskAction -Execute "D:\Ary Fund\mini_competition_ai\.venv\Scripts\python.exe" -Argument "-m scripts.refresh_weekly_research --profile $p" -WorkingDirectory "D:\Ary Fund\mini_competition_ai" }
Register-ScheduledTask -TaskName "WSR daily"  -Action (& $act daily)  -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 3:45PM)
Register-ScheduledTask -TaskName "WSR sunday" -Action (& $act sunday) -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6:00PM)
Register-ScheduledTask -TaskName "WSR monday" -Action (& $act monday) -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:30AM)
```
(Refreshing *data* on a timer is not trading automation; no job ever reads or writes a trade.) Manual refresh: `POST /admin/refresh/{job}` or `/admin/refresh-profile/{profile}` with the owner JWT (or the dev token in development). It returns 202 and runs in the background.

## Explicitly not implemented
Earnings calendar, consensus estimates, earnings actuals/surprises, transcripts, earnings strategies, options/implied moves, pre/after-market strategies, VWAP/opening-range, short interest, congressional trades, GDELT, Marketaux, FMP, Alpha Vantage, automated M&A or government-contract feeds, Telegram, any order/paper-order/execution feature. 8-K Item 2.02 (earnings releases) is deliberately ignored by catalyst detection.
