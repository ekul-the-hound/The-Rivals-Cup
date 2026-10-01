"""Deterministic offline mock of every provider endpoint (tests, `--mock` CLI mode, demos).

All data is SYNTHETIC. Nothing here resembles real prices, filings, or news.
"""

import hashlib
import random
import re
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import parse_qs, unquote, urlparse

import httpx

CIKS = {
    "KO": 21344, "PEP": 77476, "HD": 354950, "LOW": 60667, "V": 1403161, "MA": 1141391,
    "XOM": 34088, "CVX": 93410, "JPM": 19617, "BAC": 70858, "UPS": 1090727, "FDX": 1048911,
    "MRK": 310158, "PFE": 78003, "AMD": 2488, "INTC": 50863,
}  # fmt: skip
SECTOR_OF = {
    **dict.fromkeys(["KO", "PEP", "XLP"], "staples"), **dict.fromkeys(["HD", "LOW", "XLY"], "disc"),
    **dict.fromkeys(["V", "MA", "JPM", "BAC", "XLF"], "fin"), **dict.fromkeys(["XOM", "CVX", "XLE"], "energy"),
    **dict.fromkeys(["UPS", "FDX", "XLI"], "ind"), **dict.fromkeys(["MRK", "PFE", "XLV"], "health"),
    **dict.fromkeys(["AMD", "INTC", "XLK"], "tech"),
}  # fmt: skip

# ticker -> list[(form, days_ago, items, body)]
FILINGS = {
    "KO": [("8-K", 3, "1.01,5.02", "Item 1.01 Entry into a Material Definitive Agreement. The Company entered into a distribution agreement. Item 5.02 Departure of Directors or Certain Officers; the Chief Financial Officer will retire."), ("10-Q", 40, "", "")],
    "PEP": [("8-K", 20, "8.01", "Item 8.01 Other Events. Board authorized a share repurchase program of up to $10 billion.")],
    "HD": [("8-K", 5, "8.01", "Item 8.01 Other Events. The Company announced a new share repurchase authorization.")],
    "XOM": [("4", 2, "", "FORM4:S:120000:95.5")],
    "CVX": [("8-K", 6, "8.01", "Item 8.01 Other Events. The Company received a subpoena from the Department of Justice regarding an investigation.")],
    "AMD": [("8-K", 4, "2.02,7.01", "Item 2.02 Results of Operations. Quarterly results press release.")],
    "MRK": [("8-K", 10, "1.01,8.01", "Item 8.01 The Company agreed to acquire a biotech firm in a merger transaction.")],
}  # fmt: skip

NEWS = {
    "KO": [("Coca-Cola names new finance chief", "Reuters", "reuters.com", 1), ("Coca-Cola names new finance chief", "Yahoo Finance", "finance.yahoo.com", 1), ("Coca-Cola signs distribution deal", "Business Wire", "businesswire.com", 2)],
    "PEP": [("PepsiCo expands buyback", "CNBC", "cnbc.com", 3), ("Pepsi snack volumes in focus", "Some Blog", "someblog.example.com", 2)],
}  # fmt: skip


def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16))


def last_weekday_before(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def synthetic_bars(
    symbol: str, today: date, n: int = 130
) -> list[tuple[date, float, float, float, float, int]]:
    days: list[date] = []
    d = last_weekday_before(today)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    base = 40 + _rng(symbol, "base").random() * 260
    mkt, grp, idio = _rng("mkt"), _rng(SECTOR_OF.get(symbol, symbol)), _rng(symbol, "idio")
    px, out = base, []
    for day in days:
        r = 0.5 * mkt.gauss(0.0003, 0.008) + 0.7 * grp.gauss(0, 0.007) + 0.45 * idio.gauss(0, 0.007)
        o = px
        px = max(1.0, px * (1 + r))
        vol = int(2_000_000 + idio.random() * 6_000_000)
        out.append((day, o, max(o, px) * 1.004, min(o, px) * 0.996, px, vol))
    return out


class MockWorld:
    def __init__(self, today: date | None = None) -> None:
        self.today = today or datetime.now(UTC).date()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ---- routing ----
    def handle(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        q = {k: v[0] for k, v in parse_qs(urlparse(str(req.url)).query).items()}
        if host == "www.sec.gov" and path == "/files/company_tickers.json":
            return self._json(
                {
                    str(i): {"cik_str": c, "ticker": t, "title": f"{t} MOCK CORP"}
                    for i, (t, c) in enumerate(CIKS.items())
                }
            )
        if host == "data.sec.gov":
            return self._json(self._submissions(int(re.search(r"CIK(\d+)", path).group(1))))
        if host == "www.sec.gov" and path.startswith("/Archives/"):
            return self._archive(path)
        if host == "api.stlouisfed.org":
            return self._fred(q["series_id"])
        if host == "query1.finance.yahoo.com":
            sym = unquote(path.rsplit("/", 1)[-1])
            if "quoteSummary" in path:
                return self._json(
                    {
                        "quoteSummary": {
                            "result": [
                                {
                                    "price": {"marketCap": {"raw": 2.5e11}},
                                    "summaryProfile": {
                                        "sector": "Mock Sector",
                                        "industry": "Mock Industry",
                                    },
                                }
                            ]
                        }
                    }
                )
            return self._yahoo(sym, q.get("range", "6mo"))
        if host == "news.google.com":
            return self._news(q.get("q", ""))
        if host == "en.wikipedia.org":
            return self._wiki(q.get("titles", ""))
        return httpx.Response(404, text="mock: unknown endpoint")

    @staticmethod
    def _json(obj) -> httpx.Response:
        return httpx.Response(200, json=obj)

    # ---- SEC ----
    def _filings_for(self, ticker: str):
        for i, (form, ago, items, body) in enumerate(FILINGS.get(ticker, [])):
            cik = CIKS[ticker]
            doc = "xslF345X05/form4.xml" if form == "4" else "d1.htm"
            yield {
                "acc": f"{cik:010d}-26-{i + 1:06d}",
                "form": form,
                "date": (self.today - timedelta(days=ago)).isoformat(),
                "items": items,
                "doc": doc,
                "body": body,
            }

    def _submissions(self, cik: int) -> dict:
        ticker = next((t for t, c in CIKS.items() if c == cik), None)
        fs = list(self._filings_for(ticker)) if ticker else []
        return {"cik": str(cik), "filings": {"recent": {
            "accessionNumber": [f["acc"] for f in fs], "filingDate": [f["date"] for f in fs],
            "reportDate": ["" for _ in fs], "form": [f["form"] for f in fs],
            "primaryDocument": [f["doc"] for f in fs], "items": [f["items"] for f in fs],
            "primaryDocDescription": [f["form"] for f in fs]}}}  # fmt: skip

    def _archive(self, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")  # Archives/edgar/data/{cik}/{acc}/{doc}
        cik, acc_nodash = int(parts[3]), parts[4]
        ticker = next((t for t, c in CIKS.items() if c == cik), "")
        for f in self._filings_for(ticker):
            if f["acc"].replace("-", "") == acc_nodash:
                if f["form"] == "4":
                    _, code, shares, price = f["body"].split(":")
                    ad = "A" if code == "P" else "D"
                    return httpx.Response(
                        200,
                        text=f"""<ownershipDocument><issuer><issuerTradingSymbol>{ticker}</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>Mock Insider</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>{f["date"]}</value></transactionDate><transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>{shares}</value></transactionShares><transactionPricePerShare><value>{price}</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>""",
                    )
                return httpx.Response(
                    200,
                    text=f"<html><body><p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION FORM 8-K cover page</p><p>{f['body']}</p></body></html>",
                )
        return httpx.Response(404, text="not found")

    # ---- FRED ----
    def _fred(self, sid: str) -> httpx.Response:
        vals = {"DGS2": 3.85, "DGS10": 4.20, "T10Y2Y": 0.35, "FEDFUNDS": 4.10, "VIXCLS": 17.8}
        if sid == "VIXCLS" and self.today.day == 31:  # nothing special; keeps hook for tests
            return self._json({"observations": []})
        d = last_weekday_before(self.today)
        obs = [
            {"date": d.isoformat(), "value": "."},
            {"date": (d - timedelta(days=1)).isoformat(), "value": str(vals[sid])},
        ]
        return self._json({"observations": obs})

    # ---- Yahoo ----
    def _yahoo(self, sym: str, rng: str) -> httpx.Response:
        if sym.startswith("BAD"):
            return self._json(
                {
                    "chart": {
                        "result": None,
                        "error": {"code": "Not Found", "description": "No data"},
                    }
                }
            )
        n = {"5d": 5, "1mo": 22, "6mo": 130}.get(rng, 130)
        bars = synthetic_bars(sym, self.today, n)
        ts = [
            int(datetime(b[0].year, b[0].month, b[0].day, 14, 30, tzinfo=UTC).timestamp())
            for b in bars
        ]
        last = bars[-1]
        return self._json({"chart": {"error": None, "result": [{
            "meta": {"symbol": sym, "gmtoffset": -14400, "regularMarketPrice": round(last[4], 2), "regularMarketTime": ts[-1], "chartPreviousClose": round(bars[-2][4], 2)},
            "timestamp": ts,
            "indicators": {"quote": [{"open": [round(b[1], 2) for b in bars], "high": [round(b[2], 2) for b in bars], "low": [round(b[3], 2) for b in bars], "close": [round(b[4], 2) for b in bars], "volume": [b[5] for b in bars]}], "adjclose": [{"adjclose": [round(b[4], 2) for b in bars]}]}}]}})  # fmt: skip

    # ---- Google News ----
    def _news(self, query: str) -> httpx.Response:
        items = []
        for tk, rows in NEWS.items():
            if tk.lower() in query.lower() or {"KO": "coca", "PEP": "pepsi"}[tk] in query.lower():
                for title, pub, dom, ago in rows:
                    when = datetime.now(UTC) - timedelta(days=ago)
                    items.append(
                        f'<item><title>{title} - {pub}</title><link>https://news.google.com/rss/articles/{abs(hash(title)) % 10**8}</link><pubDate>{format_datetime(when)}</pubDate><source url="https://www.{dom}">{pub}</source></item>'
                    )
        return httpx.Response(200, text=f"<rss><channel>{''.join(items)}</channel></rss>")

    # ---- Wikipedia ----
    def _wiki(self, title: str) -> httpx.Response:
        if title.startswith("Missing"):
            return self._json({"query": {"pages": [{"title": title, "missing": True}]}})
        text = f"{title} is a mock company. It competes with several large rivals in its market."
        wt = "{{Infobox company\n| name = X\n| industry = [[Beverage]]s, [[Snack food]]\n| products = Soft drinks, Juices\n}}"
        return self._json(
            {
                "query": {
                    "pages": [
                        {
                            "title": title,
                            "extract": text,
                            "revisions": [{"slots": {"main": {"content": wt}}}],
                        }
                    ]
                }
            }
        )
