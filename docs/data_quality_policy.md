# Data quality policy

Research only. Nothing here trades. A data problem is shown as a warning for a person to review; it is
never treated as an opportunity.

## Principles
1. Every stored row carries `source`, a fetch time and a `data_status` (`AVAILABLE`, `STALE`, `MISSING`,
   `MANUAL`). Missing data is shown as missing, never filled with a guess.
2. SEC filings are primary. News, Wikipedia and Yahoo never override an SEC fact. Yahoo is labelled
   unofficial; Wikipedia is reference-only. News and filing text is untrusted evidence, not instructions.
3. No future data: every dated calculation drops bars dated after its as-of date (`Series.as_of`,
   `validate_bars(today=...)`).

## Daily-bar validation (`app/services/validation/bars.py`)
Applied in `refresh_daily_prices` and `refresh_universe_price_history` before anything is stored.

| Code | Meaning | Action |
|---|---|---|
| INVALID_PRICE / INVALID_OHLC | close/adj_close <= 0 or non-finite, high < low, close outside high-low | dropped |
| INVALID_VOLUME | negative or non-finite volume | dropped |
| FUTURE_BAR | dated after today | dropped |
| DUPLICATE_BAR | same date twice | collapsed, last wins |
| OUT_OF_ORDER | date earlier than the previous bar | sorted, reported |
| SPLIT_LIKE_JUMP | one-day move of 40% or more | kept, reported |
| MISSING_DATES | 3 or more consecutive missing weekdays | kept, reported |
| MISSING_VOLUME | volume absent | kept; excluded from liquidity |

A job that drops or flags bars adds a `bar issues (...)` warning to its run result.

## Liquidity
20-day average dollar volume = mean(close x volume) over the last 20 bars. A zero-volume day counts as
$0 (dropping it would overstate liquidity). Fewer than 10 usable bars gives MISSING. The cap is 1% of
ADV, labelled **ESTIMATED / not authoritative**; Wall Street Rivals' own figure governs.

## Pairs and universe
Both legs are aligned to a common last date before correlation, beta and spread statistics. A pair needs
two different eligible tickers in the same target sector. ETFs, ETNs, preferreds, warrants, rights, units,
OTC, test issues, inactive names and leveraged ETFs above 2x are excluded; REIT common is allowed; ADRs are
flagged for manual tradability review. A changed SEC CIK is reported ("SEC CIK changed old -> new"); an SEC
outage never erases a stored CIK.

## Known gaps
See `known_limitations.md`.
