# Sector scan (Health Care, Industrials, Financials, Utilities, Real Estate)

Research only. Nothing here places, queues or manages a trade; you enter every trade yourself in
Trader View. The scan never writes to the database.

## 1. Add the stocks (one time, plus whenever the S&P 500 list changes)

```powershell
python -m scripts.load_sector_universe            # dry run: shows counts, writes nothing
python -m scripts.load_sector_universe --apply    # inserts missing stocks into `securities`
```

The list comes from Wikipedia's "List of S&P 500 companies" (about 250 names in these five
sectors). If that fetch fails, save a CSV with columns `ticker,name,sector,industry` and pass
`--csv file.csv`. Existing rows are never changed, and `wsr_eligibility` stays UNVERIFIED:
S&P 500 membership does not mean a stock is allowed in the Rival Cup. Check each name in Trader View.

## 2. Pull prices for them

```powershell
python -m scripts.refresh_weekly_research --jobs refresh_daily_prices
```

Yahoo is limited to 1 request per second, so about 260 names take roughly five minutes. Optional
extras: `refresh_universe` (company profiles) and `refresh_sec_filings`.

## 3. Scan

```powershell
python -m scripts.scan_sectors
python -m scripts.scan_sectors --top 10 --csv sector_pairs.csv
python -m scripts.scan_sectors --sectors "Utilities,Real Estate" --json
```

For each sector it reports 20-day performance versus the sector ETF (XLV, XLI, XLF, XLU, XLRE),
then screens every same-sector pair: 60-day return correlation of at least 0.60, both legs liquid
enough for the planned leg size (an estimated 1% of 20-day dollar volume, not a verified WSR
rule), and no earnings or event date inside the scoring week for names that have a manual
blackout row. Direction follows the engine's rule: long the stronger 20/60-day blend, short the
weaker. The screen score (0-100) weights correlation, the size of the 20-day spread relative to the
pair's own spread volatility, same GICS sub-industry, and liquidity headroom. It is a screen, not
the PeerPairEngine score.

## 4. What the scan cannot tell you

* Earnings dates for new names. Only names with a manual blackout row are checked; look up each
  shortlisted stock's earnings date yourself.
* Whether two companies are real business peers. Statistical correlation is not a relationship;
  record one in `peer_pairs` before the engine will consider a pair.
* Rival Cup eligibility, short availability and WSR limits.

To promote a shortlisted pair, create it as a `peer_pairs` candidate with a relationship rating and
explanation, then run `python -m scripts.build_weekly_portfolio`. The engine also requires an
`exchange` value (NYSE or NASDAQ) on each security, which the loader leaves empty.
