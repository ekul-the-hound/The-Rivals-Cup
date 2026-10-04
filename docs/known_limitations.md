# Known limitations

- Live data was not refreshed or verified in the audit environment (no route to SEC/Supabase). Only the
  mock world was exercised. Run the refresh on your machine before relying on anything.
- Yahoo is unofficial and can be wrong or rate-limited; bars are validated but not cross-checked against
  a second source. SEC versus news conflicts are handled by evidence ranking, not reconciliation.
- No holiday calendar in the freshness check; the 2026 NYSE holiday list in the score estimator is an
  assumption to confirm.
- No numeric stop/target logic exists; only reminder text.
- `liquidity_metrics` and a few tables lack explicit `source` / `fetched_at` columns.
- ADV-based liquidity cap is an estimate of Wall Street Rivals' rule, not authoritative.
- Ranking is descriptive of the past and is not a forecast or advice.
- Scores are estimates; the official leaderboard governs.
