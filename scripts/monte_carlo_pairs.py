"""Monte Carlo for each A/B pair on its own: 1 long + 1 short, at 50/50 and 75/25. RESEARCH ONLY - no orders, no broker.

Per pair and window: daily log returns ~ bivariate normal (covariance from the lookback window), zero drift by default,
buy-and-hold legs, 300,000 paths x 252 days. The SAME random paths are used for both allocations so they compare cleanly.
Portfolio value = 1 + w_long*(long leg return) - w_short*(short leg return), w_long+w_short = 1.

PowerShell, repo root:   pip install yfinance matplotlib
  python scripts\monte_carlo_pairs.py --source yahoo --out data\exports\montecarlo
  (offline test on the local ~1y file: --source local)
"""
import argparse

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg"); import matplotlib.pyplot as plt

PAIRS = [  # (long, short, tier) - the 22 reviewed pairs from the whole-universe scan
    ("VCTR","VRTS","A"),("AAMI","CG","A"),("WT","MS","A"),("AMG","ARES","B"),("CRBG","LNC","B"),
    ("UFPT","MDLN","B"),
    ("EXPD","CHRW","A"),("WAB","GBX","A"),("LRCX","ACMR","A"),("SUNB","EQPT","A"),("INSW","KEX","A"),("DE","AGCO","B"),("WTS","MWA","B"),("BE","FCEL","A"),
    ("LTC","NHI","A"),("HIW","DEI","A"),("CURB","UE","B"),("WELL","VTR","B"),
    ("LNG","VG","A"),("OTTR","PNW","A"),("WTRG","AWK","B"),("UGI","ATO","B"),
]
ALLOCS = [0.50, 0.75]
ap = argparse.ArgumentParser()
ap.add_argument("--source", default="local"); ap.add_argument("--sims", type=int, default=300_000)
ap.add_argument("--horizon", type=int, default=252); ap.add_argument("--drift", type=float, default=0.0)
ap.add_argument("--csv", default="data/exports/universe/prices_adj.csv"); ap.add_argument("--out", default=".")
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--pairs", default="", help="override: LONG:SHORT,LONG:SHORT,...")
ap.add_argument("--pairs-file", default="", help="ranked.csv from universe_pair_scan.py (columns L,S,tier,sec)")
ap.add_argument("--tiers", default="AB", help="with --pairs-file: which tiers to simulate, e.g. AB or ABC")
ap.add_argument("--no-pair-charts", action="store_true", help="skip the per-pair PNGs (summary CSV + overview only)")
a = ap.parse_args()
import os; os.makedirs(a.out, exist_ok=True)
if a.pairs_file:
    _r = pd.read_csv(a.pairs_file); _r = _r[_r.tier.isin(list(a.tiers))]
    PAIRS = [(x.L, x.S, x.tier) for x in _r.itertuples()]
    print(f"{len(PAIRS)} pairs from {a.pairs_file} (tiers {a.tiers})", flush=True)
if a.pairs: PAIRS = [(x.split(":")[0], x.split(":")[1], "-") for x in a.pairs.split(",")]

tick = sorted({t for p in PAIRS for t in p[:2]})
if a.source == "yahoo":
    import yfinance as yf
    px = yf.download(tick, period="max", auto_adjust=True, progress=False)["Close"]
    windows = [("20 years","20y"),("5 years","5y"),("1 week","1w")]
else:
    px = pd.read_csv(a.csv, index_col=0, parse_dates=True)[tick]
    windows = [("all local data (~1 year)","all"),("1 week","1w")]
px = px.sort_index(); ret_all = np.log(px).diff().iloc[1:]; end = ret_all.index.max()

def win(code, cols):
    r = ret_all[cols]
    if code == "all": return r.dropna()
    if code == "1w": return r.dropna().iloc[-5:]
    return r[r.index > end - pd.DateOffset(years=int(code[:-1]))].dropna()   # common dates for the two names

def sim(r, W_list):
    cov = r.cov().values; cov = (cov + cov.T)/2
    v, U = np.linalg.eigh(cov); cov = (U*np.clip(v,1e-12,None))@U.T
    L = np.linalg.cholesky(cov + 1e-12*np.eye(2)); mu = a.drift/252.0
    rng = np.random.default_rng(a.seed); H = a.horizon; B = 20_000
    out = {w: dict(term=[], mdd=[], keep=None) for w in W_list}
    for s in range(0, a.sims, B):
        n = min(B, a.sims-s)
        z = rng.standard_normal((n,H,2)) @ L.T + mu
        cum = np.exp(np.cumsum(z, axis=1)) - 1.0
        for w in W_list:
            pv = 1.0 + cum @ np.array([w, -(1-w)])
            pv = np.concatenate([np.ones((n,1)), pv], axis=1)
            peak = np.maximum.accumulate(pv, axis=1)
            out[w]["mdd"].append(((peak-pv)/peak).max(axis=1)); out[w]["term"].append(pv[:,-1]-1.0)
            if out[w]["keep"] is None: out[w]["keep"] = pv[:3000]-1.0
    for w in W_list:
        out[w]["term"] = np.concatenate(out[w]["term"]); out[w]["mdd"] = np.concatenate(out[w]["mdd"])
    return out, np.sqrt(np.diag(cov)*252)

rows = []
for L_, S_, tier in PAIRS:
    pn = None if a.no_pair_charts else 1
    fig, axes = plt.subplots(len(windows), 3, figsize=(17, 4.4*len(windows))); axes = np.atleast_2d(axes)
    for i, (label, code) in enumerate(windows):
        r = win(code, [L_, S_])
        res, vol = sim(r, ALLOCS)
        corr = r.corr().iloc[0,1] if len(r) > 2 else np.nan
        ax = axes[i,0]; allt = np.concatenate([res[w]["term"] for w in ALLOCS])*100
        lo, hi = np.percentile(allt, [0.2, 99.8]); cols = {0.50:"#3b6ea5", 0.75:"#e67e22"}
        for w in ALLOCS: ax.hist(res[w]["term"]*100, bins=150, range=(lo,hi), alpha=.55, color=cols[w], label=f"{w:.0%} long / {1-w:.0%} short")
        ax.set_title(f"{L_} long / {S_} short ({tier}) - {label}: 252-day return"); ax.set_xlabel("% (extreme 0.2% tails cut)"); ax.legend(fontsize=8)
        for j, w in enumerate(ALLOCS):
            ax = axes[i,1+j]; k = res[w]["keep"]; t = np.arange(k.shape[1]); ps = np.percentile(k,[5,25,50,75,95],axis=0)*100
            ax.plot(t, k[:100].T*100, color="#999", lw=.3, alpha=.5)
            ax.fill_between(t, ps[0], ps[4], color=cols[w], alpha=.18); ax.fill_between(t, ps[1], ps[3], color=cols[w], alpha=.32)
            ax.plot(t, ps[2], color="k", lw=1.5); ax.set_title(f"Fan chart {w:.0%}/{1-w:.0%} (5/25/50/75/95th)"); ax.set_xlabel("trading days"); ax.set_ylabel("%")
        for w in ALLOCS:
            term, mdd = res[w]["term"], res[w]["mdd"]; q = np.percentile(term,[5,50,95])
            rows.append(dict(pair=f"{L_}/{S_}", tier=tier, window=label, long_pct=int(w*100), short_pct=int((1-w)*100), obs=len(r),
                mean=term.mean(), p5=q[0], p50=q[1], p95=q[2], prob_loss=(term<0).mean(), med_maxdd=np.median(mdd),
                p95_maxdd=np.percentile(mdd,95), med_score=np.median(term-0.5*mdd), long_vol=vol[0], short_vol=vol[1], corr=corr))
    if pn:
        fig.suptitle(f"{L_} long / {S_} short - {a.sims:,} sims, zero drift (research only, not a forecast)", y=1.0)
        fig.tight_layout(); fig.savefig(f"{a.out}/mc_pair_{L_}_{S_}.png", dpi=100)
    plt.close(fig)
    print("done", L_, S_, flush=True)
df = pd.DataFrame(rows).round(4); df.to_csv(f"{a.out}/mc_pairs_summary.csv", index=False)
print(df.drop(columns=["tier","mean","long_vol","short_vol"]).to_string(index=False))
# overview chart: 5th-95th percentile range and median max drawdown per pair, first window only
w0 = windows[0][0]; ov = df[df.window == w0]; names = [f"{p[0]}/{p[1]}" for p in PAIRS]; dpi_ov = 100 if len(PAIRS) <= 60 else 60
fig, ax = plt.subplots(1, 2, figsize=(15, 0.42*len(names)+2.5), sharey=True); y = np.arange(len(names))
for k, (w, c) in enumerate([(50, "#3b6ea5"), (75, "#e67e22")]):
    d = ov[ov.long_pct == w].set_index("pair").reindex(names); off = (k-0.5)*0.35
    lo = d.p5.clip(-1, 1.5)*100; hi = d.p95.clip(-1, 1.5)*100
    ax[0].hlines(y+off, lo, hi, color=c, lw=4, alpha=.7, label=f"{w}/{100-w}"); ax[0].plot(d.p50*100, y+off, "k|", ms=9)
    ax[1].barh(y+off, d.med_maxdd.clip(0, 1.5)*100, height=0.32, color=c, alpha=.8, label=f"{w}/{100-w}")
ax[0].set_yticks(y); ax[0].set_yticklabels(names); ax[0].invert_yaxis(); ax[0].axvline(0, color="k", lw=.6)
ax[0].set_title(f"{w0}: 5th-95th pct 1-year return (black tick = median; clipped at -100/+150%)"); ax[0].set_xlabel("%"); ax[0].legend()
ax[1].set_title("Median max drawdown (%)"); ax[1].legend(); fig.tight_layout(); fig.savefig(f"{a.out}/mc_pairs_overview.png", dpi=dpi_ov)
