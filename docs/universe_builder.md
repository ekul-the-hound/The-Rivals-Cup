# U.S.-listed target-sector universe builder

> This is a research universe. It does not confirm that WSR/Trader View permits trading every listed symbol. Verify availability manually before any trade.

Goal: a refreshable, exportable list of U.S.-listed common stocks in **Health Care, Industrials, Financials, Utilities and Real Estate**, for later manual peer-pair research. It is not limited to the S&P 500 and contains no hardcoded tickers: the list comes from the Nasdaq Trader directories every time you refresh it.

"Buyable in America" is not the same as "available in the Rival Cup". Every row carries `competition_tradable_status` (`UNKNOWN` until you check Trader View yourself, then `MANUALLY_VERIFIED` or `MANUALLY_REJECTED`) and a `TRADABILITY_UNKNOWN` flag. Nothing in this repository can read or change real availability.

## Pipeline (four jobs)

1. `refresh_us_listed_symbol_universe`: downloads both Nasdaq Trader files, adds the SEC CIK, classifies the security type from the published name and flags, upserts `security_master`. Symbols that vanish from the directory become `is_active = false` (never deleted). A safety guard skips deactivation if the new list looks wrong (under 2,000 symbols or under 70% of what is stored).
2. `classify_target_sectors`: for common stocks and ADRs it first reads the issuer's SEC SIC code (official, from `data.sec.gov/submissions`, about 8 requests/second) and maps it to a sector with the rule table in `app/services/universe/sic.py` (`sector_source = SEC_SIC`, flag `SECTOR_FROM_SIC`). Symbols with no SEC record fall back to Yahoo's sector label, then to your optional CSV. Labels are normalised to `HEALTH_CARE | INDUSTRIALS | FINANCIALS | UTILITIES | REAL_ESTATE | OTHER`. Sector is never inferred from a name or ticker. A label that is not recognised becomes `UNVERIFIED` with no sector; a symbol Yahoo refuses stays `MISSING`. Both land in the manual-review queue.
3. `refresh_universe_market_data`: last price and 20-day average share/dollar volume for target-sector names (Yahoo daily bars).
4. `build_target_sector_universe_views`: recomputes which view each row belongs to, with exclusion reasons and flags.

Each run processes one batch (`UNIVERSE_CLASSIFY_BATCH`, default 4000; `UNIVERSE_MARKET_DATA_BATCH`, default 800 at Yahoo's 1 request/second), oldest-checked first, and the next run resumes where it stopped. Run the profile a few times until the job messages say nothing is still waiting. If Yahoo's sector endpoint keeps refusing, the job stops calling it for the rest of that run and relies on SEC SIC.

## Commands (PowerShell, from the repo root, venv active)

```powershell
supabase db push                                   # once: creates security_master and friends
python -m scripts.refresh_weekly_research --profile universe
# repeat the line above until "still waiting" reaches 0 in the job messages, or run only the pieces:
python -m scripts.refresh_weekly_research --jobs refresh_us_listed_symbol_universe,classify_target_sectors,refresh_universe_market_data,build_target_sector_universe_views
python -m scripts.export_target_sector_universe --format csv --output data/exports/us_target_sector_universe.csv
```

Offline demo with synthetic data: add `--mock` to either script. The dashboard page "10. Target-sector universe" shows the summaries and has an export button.

## Views

| View | Rule |
|---|---|
| `all_target_sector_listings` | active, not a test issue, not OTC, not ETF/ETN/fund/preferred/warrant/right/unit/debt/leveraged, not unknown type, sector verified (`AVAILABLE` or `MANUAL`) and one of the five targets. ADRs and thinly traded names stay in, flagged. |
| `pair_research_eligible_universe` | the above, plus price at least `UNIVERSE_MIN_PRICE`, 20-day dollar volume at least `UNIVERSE_MIN_ADV_USD`, market data no older than `UNIVERSE_PRICE_MAX_AGE_DAYS`, no known earnings/major event in the current scoring week, not a SPAC or shell, not `MANUALLY_REJECTED`. |
| `manual_review_universe` | plausible stocks with missing or unverified sector, uncertain security type, ADRs awaiting a tradability check, or a listing-compliance flag. |

Flags in exports: `ADR`, `REIT`, `SPAC`, `TRADABILITY_UNKNOWN`, `MANUALLY_VERIFIED`, `MANUALLY_REJECTED`, `MANUALLY_OVERRIDDEN`, `MANUAL_SECTOR`, `SECTOR_UNVERIFIED`, `NO_SEC_MATCH`, `LISTING_STATUS_x`, `LOW_LIQUIDITY`, `STALE_MARKET_DATA`, `MARKET_DATA_MISSING`, `EVENT_WITHIN_WEEK`.

## API (all under `/universe`, owner auth)

`GET /universe/target-sectors` (filters: view, sector, exchange, security_type, adr, tradable_status, min_adv, q; paging), `GET /universe/target-sectors/export?format=csv|json`, `GET /universe/target-sectors/summary`, `GET /universe/manual-review`, `POST /universe/{ticker}/manual-verify`, `POST /universe/{ticker}/manual-override`. The two POSTs only change research metadata, require a written note or reason, and write an audit row to `security_master_audit`. Refresh jobs never overwrite a manual sector or security type.

## Export

CSV or JSON with every flag, exclusion/review reasons, provenance (`listing_source`, `listing_verified_at`, `sector_source`, `sector_raw`, `sector_data_status`, `security_type_source`, `market_data_source`), a `generated_at` timestamp on each row, and the disclaimer above on each row.

## Known limitations

- Security type comes from name patterns. It is conservative (unmatched names become `UNKNOWN` and go to manual review) but not perfect: limited-partnership "Units", SPACs, closed-end funds without "Fund" in the name and "Trust" names can be misread. Fix with a manual override.
- SIC is not GICS. It is a filer-assigned industry code, so some companies land in a different sector than a GICS-based provider would give them (for example conglomerates or holding companies). SIC-based rows are flagged `SECTOR_FROM_SIC`; correct any you disagree with via a manual override.
- Yahoo's profile endpoint is unofficial and sometimes refuses requests. Those symbols stay `MISSING` until a later run, a manual override, or the optional CSV adapter (which marks rows `UNVERIFIED`, so they stay out of the views unless `UNIVERSE_ALLOW_UNVERIFIED_SECTOR=true`).
- Sector labels are Yahoo's, not GICS. Boundaries differ a little (for example some healthcare REIT-like names).
- Exchange comes from the Nasdaq Trader file only. OTC names are not in these files, so they never enter.
- Earnings and event dates come only from the manual fields and the existing blackout table; they are not fetched for the whole universe.
- Securities in `security_master` are not copied into the older `securities` table, so `scan_sectors` still reads `securities`.
- ADRs are kept but flagged because their availability in Trader View is the most likely to differ.
