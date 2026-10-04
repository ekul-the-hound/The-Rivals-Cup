# Deep-dive research data

Purpose: give you (and Claude) enough company data to pick the five best long ideas and their five best competitors each. Everything is research input. Nothing here is a trade instruction, and none of it confirms that WSR/Trader View lets you trade a symbol.

## Two layers

1. **Market-wide (profile `deepmarket`)**, filtered to active target-sector names in `security_master`:
   `refresh_short_interest`, `refresh_short_sale_volume`, `refresh_sec_filing_feed`, `refresh_earnings_calendar`.
   The earnings calendar also writes the next date into `security_master.earnings_date_if_known`, so the pair-research view applies its earnings-week exclusion. A date you entered by hand is never overwritten.
2. **Per company (profile `deepdive`)**, only for the shortlist in `DEEP_DIVE_TICKERS`, because the free tiers are small:
   `refresh_analyst_ratings` (Finnhub), `refresh_earnings_transcripts` (Alpha Vantage), `refresh_company_fundamentals` (SEC XBRL), `refresh_options_iv` (Yahoo, only if `OPTIONS_IV_ENABLED=true`).

A job whose key, shortlist or flag is missing reports `SKIPPED` with the reason. It never fails the others.

## Setup (PowerShell, repo root, venv active)

1. Apply the new migration: paste `supabase/migrations/20261006000001_deep_dive_data.sql` into the Supabase SQL editor and Run it (same way as the universe migration).
2. Free keys (optional): finnhub.io and alphavantage.co. Put them in `.env`:
   `FINNHUB_API_KEY=...`, `ALPHA_VANTAGE_API_KEY=...`
3. Choose the shortlist in `.env`: `DEEP_DIVE_TICKERS=AAA,BBB,CCC` (your top candidates plus their competitors; start with 15 to 40 names).

```powershell
python -m scripts.refresh_weekly_research --profile deepmarket
python -m scripts.refresh_weekly_research --profile deepdive
python -m scripts.deep_dive_packet --tickers AAA,BBB --format md
```

Run `deepmarket` after the universe profile has finished. Run `deepdive` once per day at most (Alpha Vantage allows about 25 calls/day; `ALPHA_VANTAGE_DAILY_BUDGET=20` keeps a margin, and a rerun the next day continues where it stopped).

## The packet

`python -m scripts.deep_dive_packet --tickers ...` (or `GET /universe/{ticker}/research-packet`) returns one structure per company: security facts, short interest and trend, short-sale volume ratio, next earnings date, analyst score (-2 to +2) and its change, XBRL fundamentals with revenue growth, latest transcript excerpt, options IV, recent SEC filings, same-industry peer candidates from the pair-research view, and a `data_gaps` list naming anything missing. Paste the markdown into Claude to compare longs and competitors.

## Limits you should know

- Peer candidates group by SEC industry description, which is coarse.
- FINRA short interest is published about a week after each settlement date, so it lags.
- Transcripts depend on Alpha Vantage coverage and its daily cap; many companies will have none. The 8-K earnings press release link appears in `recent_sec_filings`.
- Options IV comes from an unofficial Yahoo endpoint that is often refused, so expect gaps.
- Free-tier terms can change. Check each provider's current terms.
