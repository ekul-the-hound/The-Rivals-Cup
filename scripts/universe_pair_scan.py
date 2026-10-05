"""Whole-universe long/short pair scan + fundamentals ranking. RESEARCH ONLY - no orders, no broker, no Trader View.

Stage 1  scan  : every eligible long x every eligible short inside each sector (all ~1,460 tickers), keeping only pairs that
                 are real competitors (listed in peers.csv or same industry), with residual correlation >= 0.2 (0.3 when linked by industry only) and a
                 spread z-score inside (-2.5, 2). Writes data/exports/universe_scan/candidates.csv + candidates_pairs.txt
Stage 2  (you run) python -m scripts.export_fundamentals --pairs "<contents of candidates_pairs.txt>"
Stage 3  rank  : scores every name on its fundamentals vs its sector, tiers each pair (A/B/C), and picks the best N per sector
                 (one use per ticker). Writes ranked.csv + final_picks.csv

  python scripts/universe_pair_scan.py scan --top 40
  python scripts/universe_pair_scan.py rank --final 10
"""
import argparse
import os

import numpy as np
import pandas as pd

SECTORS = ["HEALTH_CARE", "INDUSTRIALS", "FINANCIALS", "UTILITIES", "REAL_ESTATE"]
ap = argparse.ArgumentParser()
ap.add_argument("stage", choices=["scan", "rank"])
ap.add_argument("--universe-dir", default="data/exports/universe")
ap.add_argument("--fund-dir", default="data/exports/fundamentals")
ap.add_argument("--out-dir", default="data/exports/universe_scan")
ap.add_argument("--top", type=int, default=40, help="scan: candidate pairs kept per sector")
ap.add_argument("--keep-all", action="store_true", help="scan: keep EVERY eligible linked pair (no per-sector cap, no ticker-reuse cap)")
ap.add_argument("--max-uses", type=int, default=2, help="scan: max times one ticker appears in the candidates")
ap.add_argument("--final", type=int, default=10, help="rank: final pairs per sector")
ap.add_argument("--candidates", default="", help="rank: candidates csv (default out-dir/candidates.csv)")
a = ap.parse_args()
os.makedirs(a.out_dir, exist_ok=True)
D = a.universe_dir
u = pd.read_csv(f"{D}/universe_table.csv").set_index("ticker"); u["sector"] = u["sector"].astype(str)
pe = pd.read_csv(f"{D}/peers.csv"); peers = {}
for x, y in zip(pe.ticker, pe.peer):
    peers.setdefault(x, set()).add(y); peers.setdefault(y, set()).add(x)

def link(l, s):
    if s in peers.get(l, ()): return "PEER"
    il, is_ = u.at[l, "industry"], u.at[s, "industry"]
    return "INDUSTRY" if isinstance(il, str) and il == is_ else ""

if a.stage == "scan":
    px = pd.read_csv(f"{D}/prices_adj.csv", index_col=0); lr = np.log(px).diff()
    rows = []
    for sec in SECTORS:
        S = u[u.sector == sec]; cols = [c for c in S.index if c in lr.columns]
        R = lr[cols].iloc[-120:]; R = R.sub(R.mean(axis=1), axis=0)
        shorts = [t for t in cols if S.at[t, "adv_usd"] >= 15e6 and S.at[t, "event_blackout"] != 1 and S.at[t, "strength"] <= 70]
        found = []
        for min_ls in (80, 70, 65):                      # relax the long-strength bar only if a sector is thin
            longs = [t for t in cols if S.at[t, "strength"] >= min_ls and S.at[t, "adv_usd"] >= 15e6
                     and S.at[t, "event_blackout"] == 0 and S.at[t, "ret_5d"] < 12]
            found = []
            for lt in longs:
                for st in shorts:
                    if st == lt: continue
                    lk = link(lt, st)
                    if not lk: continue                   # competitors only - no momentum-only pairs
                    ra = R[[lt, st]].dropna()
                    if len(ra) < 100: continue
                    c = ra.corr().iloc[0, 1]
                    if c < (0.2 if lk == "PEER" else 0.3): continue   # industry-only links need a tighter co-movement
                    p = px[[lt, st]].dropna(); sp = np.log(p[lt]) - np.log(p[st]); w = sp.iloc[-60:]
                    z = (sp.iloc[-1] - w.mean()) / w.std(ddof=1)
                    if not (-2.5 < z < 2): continue
                    d = sp.diff().dropna().iloc[-130:]; blocks = d.values[: len(d) // 5 * 5].reshape(-1, 5).sum(axis=1)[-26:]
                    dd = np.sqrt(np.mean(np.minimum(blocks, 0) ** 2)); score = (blocks.mean() - 0.5 * dd) * 100
                    found.append(dict(sec=sec, L=lt, S=st, z=round(z, 2), resid_corr=round(c, 2), link=lk, score=round(score, 2),
                                      hit=round((blocks > 0).mean(), 2), worst=round(blocks.min() * 100, 1), min_long_strength=min_ls,
                                      Ls=S.at[lt, "strength"], Ss=S.at[st, "strength"], Ladv=round(S.at[lt, "adv_usd"] / 1e6),
                                      Sadv=round(S.at[st, "adv_usd"] / 1e6), Sfl=S.at[st, "short_flags"]))
            if len(found) >= a.top: break
        df = pd.DataFrame(found).sort_values("score", ascending=False) if found else pd.DataFrame()
        keep, uses = [], {}
        for _, r in df.iterrows():                        # cap how often one name repeats so the list is diverse
            if not a.keep_all and (uses.get(r.L, 0) >= a.max_uses or uses.get(r.S, 0) >= a.max_uses): continue
            keep.append(r); uses[r.L] = uses.get(r.L, 0) + 1; uses[r.S] = uses.get(r.S, 0) + 1
            if not a.keep_all and len(keep) >= a.top: break
        print(f"{sec}: {len(found)} eligible linked pairs -> kept {len(keep)} (long strength >= {found[0]['min_long_strength'] if found else '-'})")
        rows += keep
    out = pd.DataFrame(rows); out.to_csv(f"{a.out_dir}/candidates.csv", index=False)
    s = ",".join(f"{r.L}:{r.S}" for r in out.itertuples())
    open(f"{a.out_dir}/candidates_pairs.txt", "w").write(s)
    print(f"\n{len(out)} candidate pairs, {len(set(out.L)|set(out.S))} unique tickers -> {a.out_dir}\\candidates.csv")
    print("Next: python -m scripts.export_fundamentals --pairs (Get-Content data\\exports\\universe_scan\\candidates_pairs.txt -Raw)")

else:
    f = pd.read_csv(f"{a.fund_dir}/fundamentals.csv").set_index("ticker")
    cand = pd.read_csv(a.candidates or f"{a.out_dir}/candidates.csv")
    def pr(x, lo=None, hi=None): return x.clip(lo, hi).rank(pct=True)
    def score(g):
        sc = pd.DataFrame(index=g.index)
        sc["growth"] = pr(g.revenue_growth_ytd_pct, -30, 60); sc["opm"] = pr(g.operating_margin_ttm_pct, -50, 60)
        sc["fcfm"] = pr(g.fcf_margin_pct, -50, 60); sc["ret"] = pr(g.roic_pct.fillna(g.roe_pct), -30, 40)
        sc["lev"] = 1 - pr(g.net_debt_to_ebitda, -3, 12); sc["cov"] = pr(g.interest_coverage, -5, 30)
        sc["liq"] = pr(g.current_ratio, 0.3, 4); sc["dil"] = 1 - pr(g.share_count_growth_1y_pct, -5, 30)
        sc["val"] = pr(g.fcf_yield_pct, -10, 15)
        w = dict(growth=1.2, opm=1.2, fcfm=1.2, ret=1.2, lev=1, cov=.8, liq=.4, dil=.8, val=.6)
        return sum(sc[k].fillna(.5) * v for k, v in w.items()) / sum(w.values()) * 100, sc.notna().sum(axis=1)
    f["Q"] = np.nan; f["n_metrics"] = 0
    for sec, g in f.groupby("sector"):
        q, n = score(g); f.loc[g.index, "Q"] = q; f.loc[g.index, "n_metrics"] = n
    f.to_csv(f"{a.out_dir}/scored.csv")
    rows = []
    for r in cand.itertuples():
        if r.L not in f.index or r.S not in f.index: continue
        L, S = f.loc[r.L], f.loc[r.S]; fl = []
        lev = r.sec in ("HEALTH_CARE", "INDUSTRIALS") and (L.net_debt_to_ebitda > 5 or L.interest_coverage < 2)
        if L.Q < 40 or (L.operating_margin_ttm_pct < 0 and L.fcf_margin_pct < 0) or lev: fl.append("LONG_WEAK")
        if S.Q >= L.Q or S.Q >= 60: fl.append("SHORT_STRONG")
        if L.n_metrics <= 3 or S.n_metrics <= 3 or pd.isna(L.revenue_ttm) or pd.isna(S.revenue_ttm): fl.append("UNVERIFIED")
        if r.link == "INDUSTRY": fl.append("ADJACENT")            # same industry code but not a listed peer
        if "SQUEEZE" in str(getattr(r, "Sfl", "")): fl.append("SQUEEZE")
        if u.at[r.L, "type"] == "ADR" or u.at[r.S, "type"] == "ADR": fl.append("ADR")
        e = L.Q - S.Q; hard = [x for x in fl if x in ("LONG_WEAK", "SHORT_STRONG", "UNVERIFIED")]
        soft = [x for x in fl if x not in ("SQUEEZE",)]
        tier = "A" if e >= 15 and not hard and len(soft) <= 1 else ("B" if e >= 5 and len(hard) <= 1 and "UNVERIFIED" not in hard else "C")
        rows.append(dict(sec=r.sec, L=r.L, S=r.S, z=r.z, link=r.link, QL=round(L.Q, 1), QS=round(S.Q, 1), edge=round(e, 1), tier=tier,
                         flags="|".join(fl), ret60_L=L.get("ret_60d_pct"), ret60_S=S.get("ret_60d_pct")))
    d = pd.DataFrame(rows); d["tier_rank"] = d.tier.map({"A": 0, "B": 1, "C": 2})
    d = d.sort_values(["sec", "tier_rank", "edge"], ascending=[True, True, False]).drop(columns="tier_rank")
    d.to_csv(f"{a.out_dir}/ranked.csv", index=False)
    picks, used = [], set()
    for sec in SECTORS:
        n = 0
        for r in d[d.sec == sec].itertuples():
            if r.tier == "C" or r.L in used or r.S in used: continue
            picks.append(r._asdict()); used |= {r.L, r.S}; n += 1
            if n >= a.final: break
        print(f"{sec}: {n} pairs at tier A/B (target {a.final})")
    pk = pd.DataFrame(picks).drop(columns="Index", errors="ignore"); pk.to_csv(f"{a.out_dir}/final_picks.csv", index=False)
    pd.set_option("display.width", 250, "display.max_rows", 200)
    print(pk[["sec", "L", "S", "z", "QL", "QS", "edge", "tier", "link", "flags"]].to_string(index=False))
    print(d.tier.value_counts().to_dict())
