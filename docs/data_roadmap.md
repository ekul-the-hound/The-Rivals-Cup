# Data roadmap: free sources still worth adding

Ordered by how much each would improve the leaders & laggards picks. All are free (some need a free
key). Nothing here is built yet unless it says so. None connect to WSR, Trader View or a broker.

| # | Source | What it adds | Notes |
|---|---|---|---|
| 1 | **SEC XBRL "frames" API** | Revenue, net income, equity, shares outstanding for *every* company in a handful of calls, so fundamentals cover the whole universe instead of 12 names; market cap = shares x price; short interest as % of shares | Keyless, same host as the company-facts API already used |
| 2 | **Finnhub profile2 / basic metric / earnings surprises** | Market cap, P/E, margins, beta, 52-week range, last four earnings beats/misses | Free tier; 60 calls/min, so finalists only |
| 3 | **Polygon.io free tier (dividends reference)** | Declared *future* ex-dividend dates, which replaces the projected date for shorts | Free key, 5 calls/min; verify the free tier still includes it |
| 4 | **SEC bulk data sets (Forms 3/4/5 insider trades, 13F holdings)** | Insider buying/selling and institutional ownership changes, universe-wide, without per-company calls | Quarterly zips from sec.gov |
| 5 | **ETF holdings files (SPDR sector ETFs, iShares)** | Index weights as a market-cap proxy; a second view of who competes in each sector | Daily files published by the issuers |
| 6 | **openFDA + ClinicalTrials.gov v2** | Drug approvals, trial status/readout dates: binary events for Health Care names | Keyless |
| 7 | **FDIC BankFind** | Net interest margin, nonperforming loans, ROA for banks (Financials drivers) | Keyless |
| 8 | **EIA API** | Electricity and gas prices, generation: Utilities drivers | Free key |
| 9 | **More FRED series** | High-yield spread, 30-year mortgage rate, housing starts, industrial production, WTI oil | Already have the key and client |
| 10 | **GDELT DOC 2.0** | Company news volume and tone as a sentiment/attention signal | Keyless |
| 11 | **Wikipedia pageviews API** | Retail attention as a crowding proxy for leaders and shorts | Keyless |
| 12 | **Stooq daily CSV** | Backup price history if Yahoo throttles or changes | Keyless; second source for cross-checks |
| 13 | **Finnhub company news** | Per-company headlines for the finalists | Free tier |
| 14 | **A market-calendar library** (no API) | Verified NYSE holidays, replacing my assumed 2026 list | Local library |

## No free source exists for

- **Borrow cost and share availability.** Short interest, days to cover and short-sale volume are the proxies.
- **EPS estimate revisions and price targets** (paid on Finnhub and most others).
- **Reliable options implied volatility.** Yahoo's endpoint is unofficial and untested here; Cboe prohibits automated extraction.
- **Whether Trader View lists or allows shorting a symbol.** Only you can check that, by hand.

## Already built

SEC (ticker map, submissions/SIC, company facts, RSS feed), Nasdaq Trader directories, FINRA short
interest and daily short-sale volume, Finnhub (recommendations, earnings calendar, **peers**),
Alpha Vantage (earnings calendar, transcripts), Yahoo chart (**one-year history with dividends and
splits**), FRED, Google News RSS, Wikipedia.
