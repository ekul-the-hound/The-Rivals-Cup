# Leaders & laggards: the weekly long/short candidate book

**Research only.** This never places, queues, records or manages a trade, and never connects to WSR,
Trader View or a broker. You review the book on Monday and enter anything yourself in Trader View.

## The strategy

In each of the five target sectors (Health Care, Industrials, Financials, Utilities, Real Estate):

1. Find the **strongest stock** in the sector. That is the **long candidate**.
2. Find that stock's **direct competitors** and pick the **weakest** one. That is the **short candidate**.

That is at most **5 longs and 5 shorts**, one of each per sector. A sector with no valid pair this
week is reported as empty rather than filled with a weak pair. (`docs/option_a_peer_pair_strategy.md`
describes the older one-to-one pair builder; this strategy replaces it for weekly picking.)

## How "strong" and "weak" are measured

Each tradable name gets a **strength score from 0 to 100**, ranked against the other tradable names in
its own sector (percentile ranks, so it always means "strong versus sector peers"):

| Input | Weight |
|---|---|
| 20-day return | 30% |
| 60-day return | 30% |
| 120-day return | 15% |
| Distance above the 50-day average | 10% |
| Position in the 52-week range | 5% |
| 60-day return per unit of downside deviation | 10% |

The last input uses the same downside measure the WSR score penalizes. Returns use dividend-adjusted
closes. Missing inputs are dropped and the rest re-weighted.

**Long score** = strength minus penalties for parabolic runs (20d above 50%), extreme volatility
(60d annualized above 80%) and one-day moves above 25%.

**Short score** = weakness (100 minus strength) minus penalties for:

- **squeeze risk**: days to cover 3 / 5 / 8 or more (small / medium / large penalty)
- **violent volatility** (above 90% annualized) and **recent bounces** (5d above +15% or 20d above +25%)
- **an estimated ex-dividend date inside the scoring week**: a short pays the dividend. The date is
  *projected* from past dividends (Yahoo's free feed has no future dates), so it is flagged as an estimate.

Small context nudges (a few points at most, and only where the data exists) come from analyst
consensus and revenue growth. Names without that data are neutral, not penalized.

## How competitors are found

1. **Finnhub peer lists** (free) for the strongest names in each sector, kept in `competitor_map`.
2. If fewer than three usable peers remain, **same-SEC-industry names of similar size** (0.1x to 10x
   dollar volume) fill in. The pair is flagged `COMPETITOR_SOURCE` when that happens.
3. Only same-sector competitors count. Peers in other sectors are noted and ignored.

The first leader (strongest first) with enough competitors **and** a strength gap of at least 15 points
over its weakest competitor wins the sector. If none meets the gap, the strongest leader is used and
flagged `WEAK_SPREAD`.

## Screens (a name that fails is not a candidate)

Pair-research eligible per the universe rules; **not in the earnings blackout week** (a blacked-out
name is listed as "blocked" if it would have ranked in the top 5); at least 120 daily bars; prices no
more than 7 days stale; 20-day dollar volume of at least $5M; price of at least $5; no stock split in
the last 30 days.

## Sizing estimates (not instructions)

Dollar-neutral legs: portfolio value ($1,000,000 by default) times the gross target (100% by default,
maximum 180%, since the WSR limit is 200%), split across the pairs found. Each leg is capped at 15% of
the portfolio and at 1% of the name's 20-day dollar volume (an *estimate* of the WSR liquidity rule).
A beta-neutral short size and an estimated net beta are shown for information. Trader View's real
limits govern.

## Commands

```powershell
# 1. universe + market-wide data (as before; repeat until nothing is waiting)
python -m scripts.refresh_weekly_research --profile universe
python -m scripts.refresh_weekly_research --profile deepmarket

# 2. price history, competitor lists, then the book
python -m scripts.refresh_weekly_research --profile leaders

# 3. fill in analyst/fundamental/transcript context for the finalists, then rebuild the book
python -m scripts.refresh_weekly_research --profile deepdive
python -m scripts.refresh_weekly_research --jobs build_leader_laggard_book

# read it
python -m scripts.leaders_laggards                      # latest stored book
python -m scripts.leaders_laggards --format json --output data/exports/book.json
python -m scripts.leaders_laggards --live --portfolio-usd 500000 --gross-pct 80
python -m scripts.leaders_laggards --mock               # offline demo
```

The price-history job downloads one year of bars per name (Yahoo, about one per second, 800 per run), so
the first fill takes two or three runs of the `leaders` profile. Later runs only refresh names older
than 18 hours. Apply `supabase/migrations/20261007000001_leaders_laggards.sql` first (paste it into the
Supabase SQL editor).

The `deepdive` per-company jobs now also run on the latest book's finalists (picks first, then
alternates), in addition to anything in `DEEP_DIVE_TICKERS`.

There is also a dashboard page (11. Leaders & laggards) and `GET /leaders-laggards` (+ `/markdown`).

## Known limits

- Strength is **price-based** (momentum, trend, risk). Fundamentals and analyst data only nudge the
  finalists. It is a ranking, not a forecast.
- Finnhub's peer lists are a vendor's industry grouping, not a verified competitor list. Read each
  pair's business overlap yourself.
- The ex-dividend date is projected, never known. Verify on the company's investor-relations page.
- No free source of **borrow cost or short availability** exists; days to cover and short-sale volume
  are proxies. Trader View decides what you can actually short.
- Yahoo's chart endpoint is unofficial and can change or throttle.
- `TRADABILITY_UNKNOWN` stays on every name until you check Trader View yourself.
