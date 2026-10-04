# Data sources

Everything here is free, public, read-only data. Nothing is fetched from Wall Street Rivals, Trader View, a brokerage or any execution system, and no source in this list can confirm that a symbol is tradable in the Rival Cup.

| Source | Adapter | Exact URL | Used for | Limit we respect |
|---|---|---|---|---|
| Nasdaq Trader (official) | `app/services/providers/nasdaq_trader.py` | https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt | Nasdaq-listed symbols, name, ETF flag, test-issue flag, financial status | 1 request/second, cached 6 h |
| Nasdaq Trader (official) | same | https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt | NYSE, NYSE American, NYSE Arca, Cboe BZX, IEX symbols | same |
| SEC EDGAR ticker map | `providers/sec.py` | https://www.sec.gov/files/company_tickers.json | ticker -> CIK, SEC company name (`cik`, `sec_company_name`; blank = `NO_SEC_MATCH` flag) | 8 req/s, needs `SEC_USER_AGENT` |
| SEC EDGAR submissions | `providers/sec.py` `sic_info` | https://data.sec.gov/submissions/CIK{cik10}.json | SIC code and description, mapped to a sector by `universe/sic.py` (primary sector source) | 8 req/s |
| Yahoo Finance (unofficial, replaceable) | `providers/yahoo.py` | https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=price,summaryProfile | fallback sector label for symbols without an SEC record, industry, market cap | 1 request/second; may refuse, then the symbol stays `MISSING` |
| Yahoo Finance (unofficial, replaceable) | same | https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d | last price, 20-day average share and dollar volume | 1 request/second |
| Your own CSV (optional, **disabled by default**) | `app/services/universe/sectors.py` `CsvSectorMappingAdapter` | local file `SECTOR_MAPPING_CSV` | fallback sector for symbols Yahoo cannot classify; always marked `UNVERIFIED` | none |
| Manual overrides | `app/services/universe/overrides.py`, `POST /universe/{ticker}/manual-override` | n/a | sector, industry, security type, event dates, notes; always audited | n/a |

Nasdaq Trader files are pipe-delimited with a header row and a `File Creation Time:` trailer. Parsing is header-driven, so a reordered column does not break it, and a non-file response (for example an HTML block page) raises an error instead of being parsed.

Other sources used by the rest of the system (FRED, Google News RSS, Wikipedia) are described in `docs/data_providers.md`.

## Settings (environment variables)

`UNIVERSE_CLASSIFY_BATCH`, `UNIVERSE_MARKET_DATA_BATCH`, `UNIVERSE_RECLASSIFY_DAYS`, `UNIVERSE_PRICE_MAX_AGE_DAYS`, `UNIVERSE_MIN_ADV_USD`, `UNIVERSE_MIN_PRICE`, `UNIVERSE_ALLOW_UNVERIFIED_SECTOR`, `SECTOR_MAPPING_ADAPTER_ENABLED`, `SECTOR_MAPPING_CSV`. Defaults are in `app/config/settings.py` and `.env.example`.

## Deep-dive sources (short interest, earnings, analysts, transcripts, fundamentals, options)

| Source | Key | Exact URL | Used for | Limit |
|---|---|---|---|---|
| FINRA equity short interest | none | https://cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv | short interest, days to cover (twice a month) | 1 req/s |
| FINRA daily short-sale volume | none | https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt | daily short volume ratio | 1 req/s |
| SEC XBRL company facts | none (SEC user agent) | https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json | revenue, growth, net income, EPS, assets, equity, shares | 8 req/s |
| SEC latest-filings RSS (Atom) | none (SEC user agent) | https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom | recent 8-K / 10-Q / 10-K filings for target-sector names | 8 req/s |
| Alpha Vantage (free key) | `ALPHA_VANTAGE_API_KEY` | https://www.alphavantage.co/query?function=EARNINGS_CALENDAR and `EARNINGS_CALL_TRANSCRIPT` | market-wide earnings calendar (1 call); transcripts | about 25 calls/day |
| Finnhub (free key) | `FINNHUB_API_KEY` | https://finnhub.io/api/v1/stock/recommendation and `/calendar/earnings` | analyst recommendation trends; calendar fallback | 60 calls/min |
| Yahoo options chain (unofficial, off by default) | none | https://query1.finance.yahoo.com/v7/finance/options/{symbol} | at-the-money implied volatility, put/call volume | 1 req/s; often refused |

Not used on purpose: Cboe delayed quotes (its page prohibits automated extraction), the Nasdaq screener endpoint (unofficial, duplicates the Nasdaq Trader files), and brokerage data APIs (the no-broker rule). Finnhub's price targets, estimates and transcripts are paid and are not called. Free-tier limits change, so check each provider's current terms.
