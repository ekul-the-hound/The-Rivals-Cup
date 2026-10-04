"""Export fundamentals + price/pair context for a shortlist of long/short pairs.

  python -m scripts.export_fundamentals [--universe-dir data/exports/universe]
                                        [--outdir data/exports/fundamentals] [--tickers A,B,...]

Reads SEC EDGAR XBRL company facts (official, free) for each ticker, computes the quality / balance
sheet / valuation ratios from the filings, and joins price-based items (returns, relative strength vs
SPY and the sector ETF, volatility, distance from 52-week high) plus pair items (60d correlation,
spread z-score). Writes fundamentals.csv, fundamentals.json and pairs.csv.

Numbers come from the most recent filings: flows are trailing-twelve-month when a newer 10-Q exists,
otherwise the last fiscal year. Foreign filers that report in a non-USD currency get ratios but no
USD valuation multiples. Read-only research: never places, queues or records a trade and does not
confirm that Trader View lists or allows shorting any symbol.
"""

import argparse
import asyncio
import csv
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings
from app.db.store import SupabaseStore, create_supabase_client
from app.services.leaders import BENCHMARK_TICKERS, MARKET_BENCHMARK, SECTOR_ETF
from app.services.leaders.book import fetch_for_tickers
from app.services.leaders.metrics import series_from_row
from app.services.providers.sec import SecProvider

# (long, short, sector) -- the ten picks first, then the alternates
PAIRS = [
    ("TXG", "QGEN", "HEALTH_CARE"), ("VCTR", "VRTS", "FINANCIALS"), ("SN", "WHR", "INDUSTRIALS"),
    ("LTC", "NHI", "REAL_ESTATE"), ("RNW", "CWEN", "UTILITIES"),
    ("RVTY", "BIO", "HEALTH_CARE"), ("VCTR", "AAMI", "FINANCIALS"), ("RNW", "BEPC", "UTILITIES"),
    ("LTC", "PEB", "REAL_ESTATE"), ("LTC", "SVC", "REAL_ESTATE"),
    ("TRGP", "AM", "UTILITIES"), ("TRGP", "KNTK", "UTILITIES"),
]  # fmt: skip
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}
PERIODIC_FORMS = {"10-Q", "10-Q/A", "6-K"}
TAXES = ("us-gaap", "ifrs-full")

REV = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue"]  # fmt: skip
TAGS = {
    "rev": REV,
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfSales"],
    "gp": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "ni": ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortisationExpense"],
    "int": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
            "InterestAndDebtExpense", "FinanceCosts"],
    "sbc": ["ShareBasedCompensation", "AdjustmentsForSharebasedPayments"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "div": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "DividendsPaidClassifiedAsFinancingActivities"],
    "dil_sh": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}  # fmt: skip
STOCK = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["LongTermDebt", "DebtAndCapitalLeaseObligations", "BorrowingsNoncurrent"],
    "debt_nc": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "debt_c": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "EquityAttributableToOwnersOfParent", "Equity"],
    "assets": ["Assets"],
    "ca": ["AssetsCurrent", "CurrentAssets"],
    "cl": ["LiabilitiesCurrent", "CurrentLiabilities"],
    "goodwill": ["Goodwill"],
    "intang": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "mat_1y": ["LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"],
    "shares": ["CommonStockSharesOutstanding"],
}  # fmt: skip


def _entries(facts: dict, tag: str) -> list[dict]:
    out = []
    for tax in TAXES:
        for unit, es in facts.get(tax, {}).get(tag, {}).get("units", {}).items():
            out += [{**e, "unit": unit} for e in es if "val" in e]
    return out


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _dur(e: dict) -> int:
    return (_d(e["end"]) - _d(e["start"])).days


def _pick_unit(es: list[dict]) -> list[dict]:
    usd = [e for e in es if e["unit"] == "USD"]
    if usd:
        return usd
    counts: dict[str, int] = {}
    for e in es:
        counts[e["unit"]] = counts.get(e["unit"], 0) + 1
    if not counts:
        return []
    best = max(counts, key=counts.get)
    return [e for e in es if e["unit"] == best]


def _best_tag(facts: dict, tags: list[str], keep) -> tuple[str | None, list[dict]]:
    """(tag, entries) whose newest kept entry is most recent (earlier-listed wins ties)."""
    best_t: str | None = None
    best: list[dict] = []
    for t in tags:
        es = _pick_unit([e for e in _entries(facts, t) if e.get("end") and keep(e)])
        if es and (not best or max(x["end"] for x in es) > max(x["end"] for x in best)):
            best_t, best = t, es
    return best_t, best


def _dedupe(es: list[dict]) -> list[dict]:
    d: dict[tuple, dict] = {}
    for e in sorted(es, key=lambda e: e.get("filed", "")):
        d[(e.get("start"), e["end"])] = e
    return sorted(d.values(), key=lambda e: e["end"])


def flow(facts: dict, key: str, tags: dict | None = None) -> dict[str, Any]:
    """Annual series + trailing-twelve-month value + YTD growth inputs for one flow concept."""
    tag_list = (tags or TAGS)[key]
    tag, rows = _best_tag(
        facts,
        tag_list,
        lambda e: e.get("form") in ANNUAL_FORMS and e.get("start") and 330 <= _dur(e) <= 380,
    )
    ann = _dedupe(rows)
    out: dict[str, Any] = {"fy": [(e["end"], e["val"]) for e in ann], "unit": None, "tag": tag}
    if not ann:
        return out
    last = ann[-1]
    out["unit"] = last["unit"]
    out["ttm"], out["ttm_end"] = last["val"], last["end"]
    same = [e for e in _entries(facts, tag) if e.get("start") and e["unit"] == last["unit"]]
    cands = [
        e
        for e in same
        if e.get("form") in PERIODIC_FORMS and e["end"] > last["end"] and 60 <= _dur(e) <= 300
    ]
    if cands:
        newest = max(e["end"] for e in cands)
        # the cumulative (longest) period ending at the newest quarter, latest filing wins
        cur = sorted(
            (e for e in cands if e["end"] == newest), key=lambda e: (_dur(e), e.get("filed", ""))
        )[-1]
        prior = [
            e
            for e in same
            if abs(_dur(e) - _dur(cur)) <= 10 and 340 <= (_d(cur["end"]) - _d(e["end"])).days <= 390
        ]
        if prior:
            p = sorted(prior, key=lambda e: e.get("filed", ""))[-1]
            out["ttm"] = last["val"] + cur["val"] - p["val"]
            out["ttm_end"] = cur["end"]
            out["ytd"], out["ytd_prior"] = cur["val"], p["val"]
    return out


def stock(facts: dict, key: str, back_days: int | None = None) -> tuple[float | None, str | None]:
    es = _dedupe(_best_tag(facts, STOCK[key], lambda e: True)[1])
    if not es:
        return None, None
    latest = es[-1]
    if back_days is None:
        return latest["val"], latest["end"]
    older = [e for e in es if 330 <= (_d(latest["end"]) - _d(e["end"])).days <= 400]
    return (older[-1]["val"], older[-1]["end"]) if older else (None, None)


def _div(a, b):
    return None if a is None or b in (None, 0) else a / b


def _r(v, n=3):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(v, n)


def fundamentals(facts: dict, price: float | None) -> dict[str, Any]:
    f = {k: flow(facts, k) for k in TAGS}
    rev, ebit, ni, ocf = (f[k].get("ttm") for k in ("rev", "ebit", "ni", "ocf"))
    unit = f["rev"].get("unit")
    gp = f["gp"].get("ttm")
    if gp is None and rev is not None and f["cogs"].get("ttm") is not None:
        gp = rev - f["cogs"]["ttm"]
    capex = abs(f["capex"]["ttm"]) if f["capex"].get("ttm") is not None else None
    fcf = None if ocf is None else ocf - (capex or 0.0)
    ebitda = None if ebit is None else ebit + (f["da"].get("ttm") or 0.0)
    s = {k: stock(facts, k)[0] for k in STOCK if k != "shares"}
    debt = s["debt"]
    if debt is None and (s["debt_nc"] is not None or s["debt_c"] is not None):
        debt = (s["debt_nc"] or 0.0) + (s["debt_c"] or 0.0)
    cash = s["cash"]
    net_debt = None if debt is None or cash is None else debt - cash
    equity = s["equity"]
    fy_rev = f["rev"]["fy"]
    g = [_div(fy_rev[i][1], fy_rev[i - 1][1]) for i in range(1, len(fy_rev))]
    g = [None if x is None else (x - 1) * 100 for x in g]
    ytd_g = (
        None
        if f["rev"].get("ytd_prior") in (None, 0)
        else (f["rev"]["ytd"] / f["rev"]["ytd_prior"] - 1) * 100
    )
    ebit_fy = f["ebit"]["fy"]
    opm_fy = [
        _div(e[1], r[1]) for e, r in zip(ebit_fy[-2:], fy_rev[-2:], strict=False) if r[0] == e[0]
    ]
    opm_ttm = _div(ebit, rev)
    ic = None if equity is None else equity + (debt or 0.0) - (cash or 0.0)
    nopat = None if ebit is None else ebit * (1 - 0.21) if ebit > 0 else ebit
    sh_now, _ = stock(facts, "shares")
    sh_old, _ = stock(facts, "shares", back_days=365)
    dei = [
        e
        for u in facts.get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .values()
        for e in u
    ]
    shares = (
        sorted(dei, key=lambda e: (e.get("end", ""), e.get("filed", "")))[-1]["val"]
        if dei
        else sh_now
    )
    sh_growth = None
    if sh_now and sh_old:
        sh_growth = (sh_now / sh_old - 1) * 100
    elif len(f["dil_sh"]["fy"]) >= 2:
        a, b = f["dil_sh"]["fy"][-1][1], f["dil_sh"]["fy"][-2][1]
        sh_growth = (a / b - 1) * 100 if b else None
    usd = unit == "USD"
    mcap = price * shares if (price and shares and usd) else None
    ev = None if mcap is None or net_debt is None else mcap + net_debt
    tb = None if equity is None else equity - (s["goodwill"] or 0.0) - (s["intang"] or 0.0)
    div = abs(f["div"]["ttm"]) if f["div"].get("ttm") is not None else None
    bb = abs(f["buyback"]["ttm"]) if f["buyback"].get("ttm") is not None else None
    out = {
        "currency": unit,
        "ttm_end": f["rev"].get("ttm_end"),
        "revenue_ttm": rev,
        "revenue_growth_fy_pct": _r(g[-1] if g else None, 1),
        "revenue_growth_prior_fy_pct": _r(g[-2] if len(g) > 1 else None, 1),
        "revenue_growth_ytd_pct": _r(ytd_g, 1),
        "gross_margin_pct": _r(_pct(_div(gp, rev)), 1),
        "operating_margin_ttm_pct": _r(_pct(opm_ttm), 1),
        "operating_margin_fy_pct": _r(_pct(opm_fy[-1] if opm_fy else None), 1),
        "operating_margin_prior_fy_pct": _r(_pct(opm_fy[-2] if len(opm_fy) > 1 else None), 1),
        "operating_cash_flow": ocf,
        "free_cash_flow": fcf,
        "fcf_margin_pct": _r(_pct(_div(fcf, rev)), 1),
        "cash_conversion_ocf_to_ni": _r(_div(ocf, ni), 2),
        "roic_pct": _r(_pct(_div(nopat, ic)), 1),
        "roe_pct": _r(_pct(_div(ni, equity)), 1),
        "cash": cash,
        "debt": debt,
        "net_debt": net_debt,
        "net_debt_to_ebitda": _r(_div(net_debt, ebitda), 2),
        "interest_coverage": _r(
            _div(ebit, abs(f["int"]["ttm"]) if f["int"].get("ttm") else None), 1
        ),
        "debt_due_12m": s["mat_1y"],
        "debt_due_12m_pct_of_cash": _r(_pct(_div(s["mat_1y"], cash)), 0),
        "current_ratio": _r(_div(s["ca"], s["cl"]), 2),
        "share_count_growth_1y_pct": _r(sh_growth, 1),
        "sbc_pct_revenue": _r(_pct(_div(f["sbc"].get("ttm"), rev)), 1),
        "buyback_yield_pct": _r(_pct(_div(bb, mcap)), 2),
        "dividend_payout_pct_ni": _r(_pct(_div(div, ni)), 0),
        "dividend_payout_pct_ocf": _r(_pct(_div(div, ocf)), 0),
        "shares_outstanding": shares,
        "market_cap": mcap,
        "enterprise_value": ev,
        "ev_to_revenue": _r(_div(ev, rev), 2),
        "ev_to_ebitda": _r(_div(ev, ebitda), 1),
        "pe": _r(_div(mcap, ni) if ni and ni > 0 else None, 1),
        "price_to_fcf": _r(_div(mcap, fcf) if fcf and fcf > 0 else None, 1),
        "fcf_yield_pct": _r(_pct(_div(fcf, mcap)), 2),
        "price_to_book": _r(_div(mcap, equity) if equity and equity > 0 else None, 2),
        "price_to_tangible_book": _r(_div(mcap, tb) if tb and tb > 0 else None, 2),
    }
    if not usd and unit:
        out["note"] = f"reports in {unit}: ratios shown, USD market cap / EV multiples not computed"
    return out


def _pct(v):
    return None if v is None else v * 100


# ---------------- price / pair items ----------------
def _ret(a: np.ndarray, n: int):
    return None if len(a) <= n else float(a[-1] / a[-1 - n] - 1)


def price_items(adj: dict[str, tuple[list, np.ndarray]], tk: str, sector: str, tbl: dict) -> dict:
    a = adj[tk][1]
    spy, etf = adj[MARKET_BENCHMARK][1], adj[SECTOR_ETF[sector]][1]
    lr = np.diff(np.log(a[-21:]))
    row = tbl.get(tk, {})
    out = {"price": float(a[-1])}
    for n in (5, 20, 60):
        r, rs, re = _ret(a, n), _ret(spy, n), _ret(etf, n)
        out[f"ret_{n}d_pct"] = _r(None if r is None else r * 100, 1)
        out[f"rs_vs_spy_{n}d_pct"] = _r(None if r is None or rs is None else (r - rs) * 100, 1)
        out[f"rs_vs_etf_{n}d_pct"] = _r(None if r is None or re is None else (r - re) * 100, 1)
    out["adv_usd"] = row.get("adv_usd")
    out["vol_20d_ann_pct"] = _r(float(np.std(lr, ddof=1) * math.sqrt(252) * 100), 1)
    out["from_52w_high_pct"] = _r((a[-1] / float(np.max(a[-252:])) - 1) * 100, 1)
    out["earnings"] = row.get("earnings")
    out["event_blackout"] = row.get("event_blackout")
    return out


def pair_items(adj, long_tk: str, short_tk: str) -> dict:
    la, sa = adj[long_tk][1], adj[short_tk][1]
    n = min(len(la), len(sa))
    la, sa = la[-n:], sa[-n:]
    lr, sr = np.diff(np.log(la))[-60:], np.diff(np.log(sa))[-60:]
    corr = float(np.corrcoef(lr, sr)[0, 1])
    spread = np.log(la) - np.log(sa)
    w = spread[-60:]
    z = float((spread[-1] - w.mean()) / w.std(ddof=1)) if w.std(ddof=1) else None
    return {"corr_60d": _r(corr, 2), "spread_z_60d": _r(z, 2)}


def load_prices(udir: Path, need: set[str]) -> tuple[dict, dict]:
    import pandas as pd

    px = pd.read_csv(
        udir / "prices_adj.csv", index_col=0, usecols=lambda c: c == "date" or c in need
    )
    tbl = pd.read_csv(udir / "universe_table.csv").set_index("ticker")
    tbl = {t: r.dropna().to_dict() for t, r in tbl.loc[tbl.index.isin(need)].iterrows()}
    adj = {tk: (list(px.index), px[tk].dropna().to_numpy()) for tk in px.columns}
    return adj, tbl


def add_benchmarks(adj: dict) -> None:
    s = get_settings()
    store = SupabaseStore(
        create_supabase_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
    )
    for r in fetch_for_tickers(store, "universe_price_history", BENCHMARK_TICKERS):
        sr = series_from_row(r)
        if sr:
            adj[r["ticker"]] = (list(sr.dates), np.asarray(sr.adj, dtype=float))


async def sec_facts(tickers: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    s = get_settings()
    p = SecProvider(s.sec_user_agent, cache_dir=str(Path("data/cache/sec")))
    out: dict[str, dict] = {}
    names: dict[str, str] = {}
    try:
        cmap = await p.ticker_cik_map()
        for tk in tickers:
            info = cmap.get(tk.upper().replace(".", "-"))
            if not info:
                print(f"  {tk}: no SEC CIK found")
                continue
            try:
                data = await p.company_facts(info.cik)
                out[tk] = data["facts"]
                names[tk] = f"{info.title} | filer: {data.get('entityName')}"
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  {tk}: SEC facts unavailable ({exc})")
    finally:
        await p.aclose()
    return out, names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--universe-dir", default="data/exports/universe")
    ap.add_argument("--outdir", default="data/exports/fundamentals")
    ap.add_argument("--tickers", default="", help="comma list; default = every ticker in PAIRS")
    ap.add_argument(
        "--pairs", default="", help="LONG:SHORT,LONG:SHORT,... (replaces built-in PAIRS)"
    )
    a = ap.parse_args(argv)
    pairs = PAIRS
    if a.pairs:
        import pandas as pd

        sec = pd.read_csv(Path(a.universe_dir) / "universe_table.csv").set_index("ticker")["sector"]
        pairs = []
        for item in a.pairs.split(","):
            lt, st = (x.strip().upper() for x in item.split(":"))
            pairs.append((lt, st, sec[lt]))
    if a.tickers:
        want = {t.strip().upper() for t in a.tickers.split(",") if t.strip()}
        pairs = [p for p in PAIRS if p[0] in want or p[1] in want]
    sector_of = {t: sec for lt, st, sec in pairs for t in (lt, st)}
    tickers = sorted(sector_of)
    adj, tbl = load_prices(Path(a.universe_dir), set(tickers))
    add_benchmarks(adj)
    facts, names = asyncio.run(sec_facts(tickers))
    rows = []
    for tk in tickers:
        if tk not in adj:
            print(f"  {tk}: not in prices_adj.csv")
            continue
        row = {"ticker": tk, "sector": sector_of[tk], **price_items(adj, tk, sector_of[tk], tbl)}
        row.update(
            fundamentals(facts[tk], row["price"]) if tk in facts else {"note": "no SEC data"}
        )
        rows.append(row)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with (out / "fundamentals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    (out / "fundamentals.json").write_text(
        json.dumps(rows, indent=1, default=str), encoding="utf-8"
    )
    prs = []
    for lt, st, sec in pairs:
        if lt in adj and st in adj:
            prs.append({"long": lt, "short": st, "sector": sec, **pair_items(adj, lt, st)})
    with (out / "pairs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["long", "short", "sector", "corr_60d", "spread_z_60d"])
        w.writeheader()
        w.writerows(prs)
    print(f"Wrote {out}: {len(rows)} tickers, {len(prs)} pairs, SEC facts for {len(facts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
