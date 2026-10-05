# Earnings module

Research only. It ranks the companies reporting **this week and next** by the estimated chance the
stock rises (or falls) after the report, with a Monte Carlo of the first move. **Longs are the main
goal**; shorts are listed but secondary. It never places, queues, or records a trade: you decide and
you enter anything yourself in Trader View.

> The probabilities come from a hand-weighted model that has **not** been backtested. Use them to
> decide what to research, not as a forecast. `python -m scripts.earnings_scan --score` grades the
> model on your own logged predictions once reports have happened.

## Run it

```powershell
# one-time: fill .env (asks for the keys with hidden input; Enter skips)
python -m scripts.setup_earnings_env --name "Your Name" --email you@example.com
python -m scripts.earnings_scan --live-check     # which sources work right now
python -m scripts.earnings_scan                  # live scan, saves the dashboard snapshot
python -m scripts.earnings_scan --mock           # offline demo, no keys, no network
python -m scripts.earnings_scan --tickers KO,PEP --week next
python -m scripts.earnings_scan --score          # grade past predictions
python -m streamlit run app/dashboard/main.py    # page 12 has the visuals
uvicorn app.api.main:app                         # GET /earnings/week, /earnings/{ticker}
```

"This week" is Monday to Friday of the current week; on Saturday and Sunday it is the coming week.

## Data sources (all free)

| Signal | Source | Needs |
|---|---|---|
| Report calendar, timing (before open / after close), EPS and revenue estimates | Finnhub (primary), Alpha Vantage (backup) | `FINNHUB_API_KEY`, optional `ALPHA_VANTAGE_API_KEY` |
| EPS beat history, past earnings-day reactions | Finnhub report history + Yahoo prices | Finnhub key |
| Analyst buy/hold/sell level and 3-month trend | Finnhub recommendation trends | Finnhub key |
| News tone (7 days) | Google News RSS, keyword lexicon | nothing |
| Insider buying and selling (officers, directors, 10% owners) | SEC EDGAR Form 4 | `SEC_USER_AGENT` |
| BlackRock, Vanguard, State Street, Berkshire holdings change | SEC EDGAR 13F-HR | `SEC_USER_AGENT` |
| Politician trades | JSON feed you choose, or Finnhub (paid) | `POLITICIAN_TRADES_URL` |
| Momentum vs SPY, liquidity, move size | Yahoo prices | nothing |
| Options-implied move and put/call skew | Yahoo options (unofficial) | `OPTIONS_IV_ENABLED=true` |
| Peer read-through | Finnhub peers + Yahoo prices | Finnhub key |

Every source is optional. A missing or failing one is shown on the dashboard (and in
`--live-check`), its signal is marked unavailable, and confidence drops accordingly.

Politician trades: no reliable free keyless API is built in. Point `POLITICIAN_TRADES_URL` at a JSON
feed of House/Senate-style records (`ticker`, `type` purchase/sale, `amount`, `transaction_date`,
`representative` or `senator`). Disclosures can be up to 45 days late, so the signal is weighted low.
13F data is quarterly and also up to 45 days stale.

## How the probability works

`log-odds = logit(prior) + sum(weight x signal)`, with every signal scaled to -1..+1. The prior is
`EARNINGS_BASE_P_UP` (0.52, an assumption). The result is clamped to 25%..75% because no earnings
setup is ever more certain than that.

| Signal | Weight | Notes |
|---|---|---|
| EPS beat history | 0.60 | centred on the typical 74% beat rate, so a 75% beater adds nothing |
| Past earnings-day reactions | 0.35 | how often the stock rose, shrunk toward 50% for small samples |
| Analyst consensus | 0.45 | centred on analysts' usual bullish lean |
| Analyst trend | 0.25 | proxy for estimate revisions |
| News tone | 0.30 | needs a handful of headlines to count fully |
| Insiders | 0.40 | open-market buys count a lot; sales are capped (often pre-planned) |
| Politicians | 0.20 | net distinct buyers minus sellers |
| 13F holders | 0.15 | average share change quarter over quarter |
| Momentum | 0.10 | 20-day return vs SPY; a big run-up raises a "priced in" flag |
| Peers | 0.25 | average move of peers that reported in the last 14 days (top names only) |
| Options skew | 0.10 | put/call volume ratio |

**Confidence** = 60% data coverage + 40% history depth. A long candidate needs P(up) >= 58%,
confidence >= 45%, at least 4 signals, positive Monte Carlo expected move, and at least $5M average
daily dollar volume (all configurable in `.env`).

## Monte Carlo

10,000 paths per report. Direction uses the model's P(up) with its uncertainty spread by a Beta
draw (less confidence, wider spread). Move size is Student-t (4 degrees of freedom) scaled to the
expected absolute move, taken from options when available, else the average of the last reports,
else 2.5 x daily volatility. Upside and downside sizes follow the stock's own history. It turns the
probability into tails and expected value; it is **not** independent evidence about direction.

## Improving it

Coded in: calibration log and Brier scoring, peer read-through, options-implied move, analyst trend,
insider-cluster weighting, priced-in flag. Still open: real estimate revisions and whisper numbers
(paid), wiring FINRA short interest in, transcript tone, and refitting the weights once 30+ reports
have been scored. The dashboard lists these too.

## Files

`app/earnings/` (sources, model, montecarlo, service, calibration, suggestions, mock),
`app/api/earnings.py`, `app/dashboard/earnings_view.py` + `screens/p12_earnings.py`,
`scripts/earnings_scan.py`, `scripts/setup_earnings_env.py`, `app/tests/test_earnings.py`.
Scans save to `data/generated/earnings/` (git-ignored): `latest.json` and `predictions.jsonl`.
