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
