# Provider freshness policy

Freshness rules live in `app/services/validation/freshness.py`. A source older than its limit is `STALE`;
absent is `MISSING`. Both reach the API, the MCP envelope (`warnings`, `missing_or_stale`) and the dashboard.

| Source | Role | Fresh when |
|---|---|---|
| SEC EDGAR | primary for filings and fundamentals | last successful refresh within 3 days |
| Yahoo (unofficial) | daily bars | latest bar is at least the last expected trading day |
| Finnhub / Alpha Vantage / FRED | optional, keyed | per-job limits; blank key keeps the adapter off |
| Google News | context only | untrusted evidence, deduped by normalised headline |
| Wikipedia | reference only | never used for ranking |

Rules: GET-only HTTP client; per-provider timeout, retry with backoff and rate limit; file cache; a
descriptive SEC User-Agent (`SEC_USER_AGENT`) is required; keys live only in the git-ignored `.env`.
A provider failure is recorded as a warning and the run continues with what exists; stale data is never
presented as current.

Limitation: there is no holiday calendar, so a market holiday can cause a false STALE. Check by hand.
