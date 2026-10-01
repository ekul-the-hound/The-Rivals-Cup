# Score methodology (ESTIMATED)

**All numbers are estimates. WSR / Trader View data and scoring are authoritative.** The estimator
revalues positions you *recorded after entering them yourself*. It does not simulate orders, fills,
stops, slippage or any WSR behaviour.

## Formulas

```
V(d) = cash + market value of long positions - market value of short liabilities
r(d) = (V(d) - V(d-1)) / V(d-1)
R    = (V(N) - 1,000,000) / 1,000,000 * 100          floored at -100
DD   = sqrt( sum( min(r(d), 0)^2 ) / N ) * 100        not annualized
PlayerScore = R - 0.5 * DD
```

* Each weekly round starts at **$1,000,000**; positions and cash reset every round. Only legs
  recorded with an entry inside the round count.
* **N = all scheduled trading days** in the round (Mon–Fri minus holidays), so the DD denominator
  includes days that have not happened yet. Unobserved days contribute r = 0 but still count in N.
* Cash accounting: a long reduces cash by qty × price; a short adds qty × price (the liability is
  the short market value). Recorded exits reverse that at the exit price.
* Marks use the **unadjusted daily close**. If a close is missing the last close is carried forward
  with a `STALE_MARK` warning; with no data the entry price is used (`NO_MARK_DATA`).
* Holiday calendar: an **assumed** 2026 NYSE list in `app/services/scoring/wsr.py`
  (`NYSE_HOLIDAYS_2026`); verify it against WSR's schedule and override if different.

Worked check (tested): V = 1,000,000 → 990,000 → 1,009,800 → 989,604 → 989,604 → 1,019,292.12,
N = 5 gives R = 1.929212, DD = sqrt((0.01² + 0.02²)/5)·100 = 1.000000, PlayerScore = 1.429212.

## Exposure

Gross = sum of absolute market values of longs and shorts. Two separate figures are shown:

* **Modeled gross exposure**: marked to market inside the estimate, as % of V(d). Warns at ≥180%
  (near) and ≥200% (at limit).
* **Manual recorded gross exposure**: cost basis (qty × entry price) of your open recorded legs, as
  a % of $1,000,000.

## Other checks

* **Liquidity**: ESTIMATED 1% of trailing 20-day dollar volume per name; labelled
  "WSR data authoritative". Over-cap or unknown → warning.
* **ETF leverage**: warns when a leverage factor above 2x is recorded (unknown → `LEVERAGE_UNKNOWN`).
* **Dividends / distributions**: applied from `dividend_events` (holder if entered before the ex-date
  and not exited before it; shorts pay). With no data: `DIVIDENDS_NOT_CHECKED`. A jump in the
  adjusted/unadjusted close ratio during the round raises `DIVIDEND_SUSPECTED`.

Saved snapshots (`score_snapshots`) are always `is_estimate = true`.
