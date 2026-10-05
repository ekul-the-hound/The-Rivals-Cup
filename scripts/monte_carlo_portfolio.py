"""Monte Carlo of the 6 A/B pairs as ONE long/short portfolio. RESEARCH ONLY - no orders, no broker.

Portfolio: 12 legs, --long-frac gross long (default 50%; 6 legs, equal) / the rest gross short (6 legs, equal). LTC is long in two pairs,
so it carries 2 legs of weight. Buy-and-hold legs (no rebalancing). Daily log returns ~ multivariate normal
(covariance from the chosen lookback window, pairwise-complete, eigenvalues clipped to PSD), zero drift by default.

Usage (PowerShell, from the repo root):
  python monte_carlo_portfolio.py --source local            # uses data/exports/universe/prices_adj.csv (about 1 year)
  python monte_carlo_portfolio.py --source yahoo            # needs: pip install yfinance  -> 20y / 5y / 1w windows
Options: --sims 300000 --horizon 252 --drift 0   (drift = annual mean return per leg to add; 0 = neutral)
"""
import argparse

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg"); import matplotlib.pyplot as plt

PAIRS = [("VCTR","VRTS"),("AAMI","CG"),("WT","MS"),("AMG","ARES"),("CRBG","LNC"),("UFPT","MDLN"),
         ("EXPD","CHRW"),("WAB","GBX"),("LRCX","ACMR"),("SUNB","EQPT"),("INSW","KEX"),("DE","AGCO"),("WTS","MWA"),("BE","FCEL"),
         ("LTC","NHI"),("HIW","DEI"),("CURB","UE"),("WELL","VTR"),("LNG","VG"),("OTTR","PNW"),("WTRG","AWK"),("UGI","ATO")]
ap = argparse.ArgumentParser()
ap.add_argument("--source", default="local"); ap.add_argument("--sims", type=int, default=300_000)
ap.add_argument("--horizon", type=int, default=252); ap.add_argument("--drift", type=float, default=0.0)
ap.add_argument("--csv", default="data/exports/universe/prices_adj.csv"); ap.add_argument("--out", default=".")
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--pairs-file", default=None)
ap.add_argument("--long-frac", type=float, default=0.5); ap.add_argument("--tag", default="")
a = ap.parse_args()
if a.pairs_file:
    import csv as _csv
    PAIRS = [(r["L"], r["S"]) for r in _csv.DictReader(open(a.pairs_file))]
LONGS = [p[0] for p in PAIRS]; SHORTS = [p[1] for p in PAIRS]; LEGS = LONGS + SHORTS; n = len(PAIRS)
W = np.array([a.long_frac/n]*n + [-(1-a.long_frac)/n]*n)

tick = sorted(set(LEGS))
if a.source == "yahoo":
    import yfinance as yf
    px = yf.download(tick, period="max", auto_adjust=True, progress=False)["Close"]
    windows = [("20 years", "20y"), ("5 years", "5y"), ("1 week", "1w")]
else:
    px = pd.read_csv(a.csv, index_col=0, parse_dates=True)[tick]
    windows = [("all local data (~1 year)", "all"), ("1 week", "1w")]
px = px.sort_index()
ret_all = np.log(px).diff().iloc[1:]
end = ret_all.index.max()

def slice_w(code):
    if code == "all": return ret_all
    if code == "1w": return ret_all.iloc[-5:]
    yrs = int(code[:-1]); return ret_all[ret_all.index > end - pd.DateOffset(years=yrs)]

def psd_cov(r):
    c = r.cov(min_periods=3).fillna(0.0).values
    c = (c + c.T) / 2; v, U = np.linalg.eigh(c); v = np.clip(v, 1e-12, None)
    return (U * v) @ U.T

def simulate(r):
    rr = r.reindex(columns=tick)
    cov = psd_cov(rr)
    idx = [tick.index(t) for t in LEGS]
    cov = cov[np.ix_(idx, idx)]                        # LTC repeated -> perfectly correlated twin legs
    L = np.linalg.cholesky(cov + 1e-12*np.eye(len(LEGS)))
    mu = np.full(len(LEGS), a.drift/252.0)
    rng = np.random.default_rng(a.seed); H = a.horizon
    term = np.empty(a.sims); mdd = np.empty(a.sims); keep = []; B = 2_000
    for s in range(0, a.sims, B):
        n = min(B, a.sims - s)
        z = rng.standard_normal((n, H, len(LEGS))) @ L.T + mu
        cum = np.exp(np.cumsum(z, axis=1)) - 1.0       # per-leg buy-and-hold return
        pv = 1.0 + cum @ W                             # portfolio value, start 1.0 (cash + net exposure P&L)
        pv = np.concatenate([np.ones((n, 1)), pv], axis=1)
        peak = np.maximum.accumulate(pv, axis=1)
        mdd[s:s+n] = ((peak - pv) / peak).max(axis=1)
        term[s:s+n] = pv[:, -1] - 1.0
        if len(keep) * B < 20_000: keep.append(pv[:2000])
    return term, mdd, np.vstack(keep) - 1.0, rr

fig, axes = plt.subplots(len(windows), 3, figsize=(17, 4.6*len(windows)))
axes = np.atleast_2d(axes); rows = []
for i, (label, code) in enumerate(windows):
    r = slice_w(code); term, mdd, paths, rr = simulate(r)
    score = term - 0.5 * mdd                            # WSR PlayerScore = R - 0.5*DD (per path)
    q = np.percentile(term, [1,5,25,50,75,95,99])
    vol = np.sqrt(np.diag(psd_cov(r.reindex(columns=tick)))*252)
    rows.append(dict(window=label, obs=len(r), mean=term.mean(), p5=q[1], p50=q[3], p95=q[5], prob_loss=(term<0).mean(),
                     p_loss_gt10=(term<-0.10).mean(), med_maxdd=np.median(mdd), p95_maxdd=np.percentile(mdd,95),
                     med_score=np.median(score), avg_leg_vol=vol.mean()))
    lo,hi=np.percentile(term*100,[0.2,99.8]); ax = axes[i,0]; ax.hist(term*100, bins=160, range=(lo,hi), color="#3b6ea5", alpha=.85)
    for k, c in [(q[1],"#c0392b"),(q[3],"k"),(q[5],"#27ae60")]: ax.axvline(k*100, color=c, ls="--", lw=1)
    ax.set_title(f"{label}: {a.horizon}-day portfolio return ({a.sims:,} sims)"); ax.set_xlabel("% (extreme 0.2% tails cut from view)"); ax.set_ylabel("paths")
    ax = axes[i,1]; t = np.arange(paths.shape[1]); ps = np.percentile(paths, [5,25,50,75,95], axis=0)*100
    ax.plot(t, paths[:150].T*100, color="#999", lw=.3, alpha=.5)
    ax.fill_between(t, ps[0], ps[4], color="#3b6ea5", alpha=.18); ax.fill_between(t, ps[1], ps[3], color="#3b6ea5", alpha=.3)
    ax.plot(t, ps[2], color="k", lw=1.6); ax.set_title("Fan chart (5/25/50/75/95th pct)"); ax.set_xlabel("trading days"); ax.set_ylabel("%")
    ax = axes[i,2]; ax.hist(mdd*100, bins=140, range=(0,np.percentile(mdd*100,99.8)), color="#8e44ad", alpha=.85); ax.axvline(np.median(mdd)*100, color="k", ls="--", lw=1)
    ax.set_title("Max drawdown per path (extreme 0.2% tail cut from view)"); ax.set_xlabel("%")
fig.suptitle(f"{n} pairs as one portfolio: {a.long_frac:.0%} long / {1-a.long_frac:.0%} short, equal-weighted, zero drift (research only - not a forecast)", y=1.0)
fig.tight_layout(); fig.savefig(f"{a.out}/monte_carlo_portfolio{a.tag}.png", dpi=110)
pd.DataFrame(rows).round(4).to_csv(f"{a.out}/monte_carlo_summary{a.tag}.csv", index=False)
print(pd.DataFrame(rows).round(3).to_string(index=False))
