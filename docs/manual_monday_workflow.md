# Manual Sunday / Monday workflow

Everything here is research and bookkeeping. **You** place every trade by hand in Trader View. This
system never connects to Wall Street Rivals, Trader View or a broker, and it cannot place or manage
trades.

## Sunday (research, ~30 min)

1. Refresh free data: `python -m scripts.refresh_weekly_research` (add `--mock` to rehearse offline).
2. Enter the weekly event blackout list (earnings / major events for next Mon–Fri) for the names you
   care about; mark rows as manually verified. Unverified rows are flagged.
3. Build the draft: `python -m scripts.build_weekly_portfolio` (or dashboard page 1).
4. Dashboard pages 2–3: read each pair's reasons, counter-thesis, event blackout, liquidity and the
   manual checklist. Exclude anything you do not like (`manual-exclude` needs a reason).
5. Page 7 (Data quality): confirm bars are fresh and read the open issues.
6. Optionally ask Claude Web (read-only MCP) to review the candidates and packets.

## Monday (you trade, then you record)

1. Re-check the checklist and the blackout list against the live calendar. WSR / Trader View data
   is authoritative for prices, liquidity limits and rules.
2. Enter the trades **yourself in Trader View**, both legs, with sizes you choose.
3. Only **after** the trades exist in Trader View: dashboard page 5 → "Record a pair". Tick
   "I have ALREADY entered both legs myself in Trader View." The form refuses without it.
   Record ticker, long/short, quantity, entry price, date/time, notes, and your stop/target
   *concepts* (free text; not orders). Each leg is stored separately and linked to one manual pair
   record. Reviewing or approving a pair never creates a record.
4. Later exits: page 5 → "Record a manual exit" (exit price, time, reason), again only after you
   exited in Trader View.
5. Page 6: see the ESTIMATED score and exposure. Compare against Trader View; if they disagree,
   Trader View wins.

## During the week

Each day after the close: refresh data (`--profile daily` style jobs), re-open page 6, read the
warnings (gross near 200%, liquidity, leverage, dividends, stale marks).

## Next Sunday

Positions and cash reset for each weekly round. Open legs recorded in an earlier round are excluded
from the new round's estimate (with a warning); record your exits for them if you closed them.
