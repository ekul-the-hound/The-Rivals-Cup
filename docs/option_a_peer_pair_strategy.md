# Option A: low-maintenance Monday peer-pair portfolio

> **Superseded for weekly picking by `docs/leaders_laggards.md`** (strongest stock per sector as the
> long, its weakest competitor as the short). This builder is kept for reference and still works.

Research only. This system never places, queues or records an order, never connects to Wall Street
Rivals or Trader View, and never marks a pair as traded. You review on Monday, enter any trades yourself
in Trader View, and record them yourself afterward.

The sample pairs, mock data and thresholds below are illustrations and defaults, not recommendations.
The WSR liquidity rule (1% of 20-day dollar volume) and the portfolio size used for allocation are your
stated estimates and assumptions, not verified WSR rules. Check Trader View before every entry.

## Why 4 to 6 pairs

The goal is a book you can review in a short Monday session and then check only periodically. That
argues for few, high-quality pairs:

- Each pair needs real attention: event dates, filings, news, sizing, and a later review. Ten pairs means
  ten of everything, and the weakest pairs get the least scrutiny.
- Good peer pairs are scarce in any given week. After the hard filters (blackouts, liquidity, data quality,
  relationship strength) and the diversification rules, often only a handful remain. Filling a quota with
  weak pairs adds risk without adding edge.
- Diversification is about drivers, not counts. Six pairs that all depend on rates or oil are one bet. The
  builder prefers different sectors and macro drivers, and it stops early rather than reuse a driver.
- It will return fewer than four, or none, when quality is low. That is a valid outcome. The portfolio
  says so in its warnings. Nothing is forced, and no sector is required.

`max_pairs` is capped at 6 in the model, the API schema and the CLI.

## How a pair is scored (0 to 100)

Eight weighted components, each scored 0 to 1 and multiplied by its weight, then two penalties:

| Component | Weight |
|---|---|
| Relationship quality (your 1-5 mapping, same sector, driver recorded) | 15 |
| Relative-strength divergence (long minus short, 20d; sweet spot 6-15%, extreme moves are discounted) | 15 |
| 20/60-day trend consistency (both spreads point the same way) | 12 |
| Sector-relative divergence (long beats and short lags the sector ETF over 20d) | 12 |
| Liquidity (estimated leg cap versus intended notional) | 10 |
| Correlation and beta compatibility (60d correlation 60%, beta gap 40%) | 14 |
| Catalyst/evidence quality (only PRIMARY/SECONDARY sources count) | 7 |
| Data completeness (freshness, 60d history, liquidity, sector ETF, beta, verified blackout, context) | 15 |

Penalties: event risk (unverified blackout, an override in use, recent adverse 8-K categories; max 20)
and concentration (volatility mismatch, crowded factor cluster). Factor overlap between pairs is applied
at portfolio level, not inside the single-pair score.

Direction rule: the leg with the higher blended relative strength (0.6 x 20d + 0.4 x 60d return) is the
long candidate; the other is the short candidate. This is momentum continuation. Every packet includes
the opposite case (mean reversion) as the counter-thesis.

### Hard eligibility (a failed check excludes the pair; the reason is listed)

Relationship mapped and quality at least 3/5; both legs active U.S.-listed equities; no ETF leveraged
above 2x; no unresolved corporate action (flagged, or a one-day adjusted move above 35%); at least 21
valid daily bars; fresh price data; estimated cap (1% of 20-day dollar volume) at least your intended leg
size; 60-day correlation at least 0.50 over at least 40 observations; no earnings or major event in the
scoring week unless a logged override exists; and, for catalyst-based theses, PRIMARY/SECONDARY evidence.
A manual exclusion also removes a pair. Headlines from unverified sources are shown but never counted as
core evidence.

## Portfolio selection

1. Evaluate every pair in CANDIDATE or APPROVED_FOR_REVIEW status.
2. Pairs you pinned (approved for review this week) go first if they pass hard eligibility. Pinning cannot
   override a hard rule.
3. Repeatedly pick the best remaining pair by adjusted score = score minus factor-overlap penalty against
   what is already chosen. A pair must have score 60 or more, adjusted score 55 or more, room under 2 pairs
   per factor cluster, and a macro driver not already used.
4. Stop at 6 pairs or when nothing qualifies.
5. Factor clusters: technology, communications and discretionary are one cluster (growth beta); energy and
   materials are one (cyclical commodity); financials, REITs and utilities are one (rate sensitive).
   Overlap points: same driver 20, same cluster 12, same sector 6, capped at 30.
6. Alternates are ranked with a plain-language reason each was not chosen.
7. Warnings cover too few pairs, factor overlap, cluster share of exposure, crowded clusters, estimated net
   beta, unverified blackouts and overrides.

## Reviewing on Monday

1. Run `python -m scripts.build_weekly_portfolio` (or `POST /pairs/build-weekly-portfolio`) after the
   Sunday refresh. Read the warnings first.
2. Verify blackouts yourself. For each leg, check the company's investor-relations calendar for the week
   and enter earnings and major-event dates in the blackout table. Unverified blackouts are flagged and
   penalized. If you decide an event is not binary, record a logged override with a real reason.
3. For each pair, open the packet and work its checklist: availability and permissions in Trader View,
   latest 8-Ks on EDGAR, headlines since Friday, pending corporate actions, live prices versus the
   reference band, Trader View's real liquidity and position limits.
4. Use `manual-exclude` (with a reason) for pairs you reject and `manual-approve-for-review` to pin pairs
   you want to keep. Both are notes. Neither places or records anything.
5. Decide dollar sizes yourself. The suggested maximum gross is a ceiling, not an instruction.
6. Enter any trades manually in Trader View.

Entry zones are a sanity band around the last price or previous close (plus or minus half a typical daily
move) so you can notice a gap. They are not limit prices or instructions. Invalidation and target
concepts are likewise concepts to review against, not stop or limit orders.

## Why event blackouts exist

An earnings release or a binary event (merger vote, court ruling, product approval) can move one leg by
10% or more overnight. A pair strategy depends on relative drift over weeks; a single-name gap that hits
only one leg is not that. It is also exactly when your free price feed is most likely to be late, wrong or
adjusted after the fact. So pairs with a known event in the scoring week are excluded by default. This is
a manual field because the free data stack has no earnings calendar. An override must be logged with a
reason of at least 10 characters and is flagged on the packet.

## Why net-zero does not mean low risk

Equal dollars long and short cancel only the dollar exposure. They do not cancel:

- Beta: if the long leg has a higher beta than the short, the pair is net long the market.
- Factor exposure: both legs can lose together to the same driver (rates, oil, growth) when the long is
  the more sensitive leg.
- Volatility: equal dollars in unequal-volatility stocks carry unequal risk.
- Idiosyncratic risk: a long that falls on its own bad news while the short rises on its own good news
  loses on both legs at once. Shorts have unbounded upside risk.
- Crowding and squeezes: popular shorts can spike on thin liquidity.
- Stacking: several pairs on the same driver add up to a large directional bet.

The builder estimates net beta across the book and warns above 5% of portfolio value.

## How gross exposure works

Gross exposure is the sum of the absolute dollar value of every long and every short. A pair with $10,000
long and $10,000 short has $20,000 gross and about $0 net. The builder suggests a ceiling per pair as a
percentage of portfolio value (both legs combined):

- Total budget 60% of portfolio value, shared by adjusted score.
- No pair above 15%.
- No pair above what its less-liquid leg supports (2 x the smaller estimated leg cap, divided by portfolio
  value).

The portfolio value defaults to $100,000. Set `portfolio_value_usd` to your real figure. Ceilings are
not redistributed if a pair is capped, so the total can be below the budget. That is intentional.

## How to record actual manual entries after the fact

The system never records a trade for you, and approving a pair for review does not mean it was traded.
After you enter trades in Trader View, record them yourself in the manual tables (`manual_trades`,
`manual_positions`) from the Supabase dashboard or your own admin script, with `source = 'MANUAL'`:
the fill price and size you actually got, the date and time, and the pair. Afterward, set the pair's
status to `ACTIVE_MANUALLY` yourself (and `CLOSED_MANUALLY` when you exit). The pair endpoints in this
repository cannot write those tables or those statuses. `GET /portfolio/manual` then shows your recorded
positions, and you can compare actual exposure with the weekly draft.

## Commands

```powershell
python -m scripts.refresh_weekly_research          # Sunday data refresh
python -m scripts.build_weekly_portfolio           # print the draft
python -m scripts.build_weekly_portfolio --persist # also save pair_rankings + weekly_portfolios
python -m scripts.build_weekly_portfolio --mock    # offline demo with synthetic data
pytest app/tests/test_option_a.py
```

## Recording what you actually traded (Turn 5)

The builder produces a research draft only. After you enter trades yourself in Trader View, record
them in dashboard page 5 (manual journal): each leg separately, linked by a manual pair record, with
your stop/target concepts and notes. A pair that was recommended, reviewed or approved is never
assumed to be traded. Page 6 then estimates the weekly score (see `docs/score_methodology.md`).
