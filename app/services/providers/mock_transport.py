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
    "MHLT": 9000001, "MIND": 9000002, "MBNK": 9000003, "MUTL": 9000004, "MREI": 9000005,
}  # fmt: skip
SECTOR_OF = {
    **dict.fromkeys(["KO", "PEP", "XLP"], "staples"), **dict.fromkeys(["HD", "LOW", "XLY"], "disc"),
    **dict.fromkeys(["V", "MA", "JPM", "BAC", "XLF"], "fin"), **dict.fromkeys(["XOM", "CVX", "XLE"], "energy"),
    **dict.fromkeys(["UPS", "FDX", "XLI"], "ind"), **dict.fromkeys(["MRK", "PFE", "XLV"], "health"),
    **dict.fromkeys(["AMD", "INTC", "XLK"], "tech"),
}  # fmt: skip

# ---- synthetic Nasdaq Trader directories (every symbol is fictional) ----
NASDAQ_LISTED_MOCK = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
MHLT|Mockhealth Therapeutics, Inc. - Common Stock|Q|N|N|100|N|N
BADH|Mockbadquote Pharma, Inc. - Common Stock|Q|N|N|100|N|N
MDEF|Mockdeficient Bio Inc. - Common Stock|S|N|D|100|N|N
MIND|Mockindustrial Machines Corp. - Common Stock|Q|N|N|100|N|N
MLOW|Mocklowvolume Industries Inc. - Common Stock|S|N|N|100|N|N
MBNK|Mockbank Financial Corp. - Common Stock|G|N|N|100|N|N
MSPC|Mock Acquisition Corp. - Common Stock|G|N|N|100|N|N
MUNT|Mock Acquisition Corp. - Units|G|N|N|100|N|N
MWRT|Mockbank Financial Corp. - Warrants|G|N|N|100|N|N
MRGT|Mockhealth Therapeutics, Inc. - Rights|Q|N|N|100|N|N
MTEC|Mocktech Software Inc. - Common Stock|Q|N|N|100|N|N
MUNK|Mockmystery Holdings - Common Stock|Q|N|N|100|N|N
MNEW|Mockunknown Thing Holdings|Q|N|N|100|N|N
MTST|Mock Test Issue Inc. - Common Stock|Q|Y|N|100|N|N
MHET|Mock Healthcare Index ETF|G|N|N|100|Y|N
MLEV|Mock Daily 3x Bull Shares|G|N|N|100|Y|N
File Creation Time: 10012026 21:30|||||||
"""
OTHER_LISTED_MOCK = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
MUTL|Mockpower Utilities Inc. Common Stock|N|MUTL|N|100|N|MUTL
MREI|Mockproperties Realty Trust, Inc. Common Stock|N|MREI|N|100|N|MREI
MADR|Mockpharma plc American Depositary Shares|N|MADR|N|100|N|MADR
MFIN|Mockfinancial Group Common Stock|A|MFIN|N|100|N|MFIN
MBNK$A|Mockbank Financial Corp. Depositary Shares Series A Preferred|N|MBNK-A|N|100|N|MBNK$A
MFND|Mock Income Fund Inc.|N|MFND|N|100|N|MFND
MXLF|Mock Financial Select ETF|P|MXLF|Y|100|N|MXLF
File Creation Time: 10012026 21:30|||||||
"""
# mock Yahoo sector labels (symbols not listed here return "Mock Sector"); MUNK is refused outright
MOCK_PROFILES = {
    "MHLT": ("Healthcare", "Biotechnology"), "BADH": ("Healthcare", "Biotechnology"),
    "MDEF": ("Healthcare", "Biotechnology"), "MADR": ("Healthcare", "Drug Manufacturers - General"),
    "MIND": ("Industrials", "Specialty Industrial Machinery"), "MLOW": ("Industrials", "Metal Fabrication"),
    "MBNK": ("Financial Services", "Banks - Regional"), "MFIN": ("Financial Services", "Insurance - Diversified"),
    "MSPC": ("Financial Services", "Shell Companies"), "MTEC": ("Technology", "Software - Application"),
    "MUTL": ("Utilities", "Utilities - Regulated Electric"), "MREI": ("Real Estate", "REIT - Industrial"),
}  # fmt: skip
LOW_VOLUME = {"MLOW"}
MOCK_SIC = {9000001: (2834, "Pharmaceutical Preparations"), 9000002: (3560, "General Industrial Machinery"),
            9000003: (6022, "State Commercial Banks"), 9000004: (4911, "Electric Services"),
            9000005: (6798, "Real Estate Investment Trusts")}  # fmt: skip

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


MOCK_DEEP = ["MHLT", "MIND", "MBNK", "MUTL", "MREI"]
MOCK_PEER_GROUPS = [["MHLT", "MADR", "MDEF"], ["MIND", "MLOW"], ["MBNK", "MFIN", "MSPC"]]


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
        if host == "www.nasdaqtrader.com" and path.endswith("/nasdaqlisted.txt"):
            return httpx.Response(200, text=NASDAQ_LISTED_MOCK)
        if host == "www.nasdaqtrader.com" and path.endswith("/otherlisted.txt"):
            return httpx.Response(200, text=OTHER_LISTED_MOCK)
        if host == "cdn.finra.org":
            return self._finra(path)
        if host == "finnhub.io":
            return self._finnhub(path, q)
        if host == "www.alphavantage.co":
            return self._alpha(q)
        if host == "www.sec.gov" and path == "/cgi-bin/browse-edgar":
            return httpx.Response(200, text=self._atom())
        if host == "data.sec.gov" and "/api/xbrl/companyfacts/" in path:
            return self._json(self._facts(int(re.search(r"CIK(\d+)", path).group(1))))
        if host == "data.sec.gov":
            return self._json(self._submissions(int(re.search(r"CIK(\d+)", path).group(1))))
        if host == "www.sec.gov" and path.startswith("/Archives/"):
            return self._archive(path)
        if host == "api.stlouisfed.org":
            return self._fred(q["series_id"])
        if host == "query1.finance.yahoo.com":
            sym = unquote(path.rsplit("/", 1)[-1])
            if "quoteSummary" in path:
                if sym == "MUNK":
                    return httpx.Response(404, text="mock: no profile")
                sector, industry = MOCK_PROFILES.get(sym, ("Mock Sector", "Mock Industry"))
                return self._json(
                    {
                        "quoteSummary": {
                            "result": [
                                {
                                    "price": {"marketCap": {"raw": 2.5e11}},
                                    "summaryProfile": {
                                        "sector": sector,
                                        "industry": industry,
                                    },
                                }
                            ]
                        }
                    }
                )
            if "/v7/finance/options/" in path:
                return self._options(sym)
            return self._yahoo(sym, q.get("range", "6mo"), bool(q.get("events")))
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
        sic, desc = MOCK_SIC.get(cik, (None, None))
        return {"cik": str(cik), "sic": str(sic) if sic else "", "sicDescription": desc or "", "filings": {"recent": {
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
    def _yahoo(self, sym: str, rng: str, events: bool = False) -> httpx.Response:
        if sym.startswith("BAD"):
            return self._json(
                {
                    "chart": {
                        "result": None,
                        "error": {"code": "Not Found", "description": "No data"},
                    }
                }
            )
        n = {"5d": 5, "1mo": 22, "6mo": 130, "1y": 252}.get(rng, 130)
        bars = synthetic_bars(sym, self.today, n)
        ts = [
            int(datetime(b[0].year, b[0].month, b[0].day, 14, 30, tzinfo=UTC).timestamp())
            for b in bars
        ]
        last = bars[-1]
        ev: dict = {}
        if events and _rng(sym, "div").random() < 0.5:  # synthetic quarterly dividend on some names
            ev = {"dividends": {
                str(ts[i]): {"amount": 0.5, "date": ts[i]} for i in range(len(ts) - 1, 0, -63)
            }}  # fmt: skip
        return self._json({"chart": {"error": None, "result": [{
            **({"events": ev} if ev else {}),
            "meta": {"symbol": sym, "gmtoffset": -14400, "regularMarketPrice": round(last[4], 2), "regularMarketTime": ts[-1], "chartPreviousClose": round(bars[-2][4], 2)},
            "timestamp": ts,
            "indicators": {"quote": [{"open": [round(b[1], 2) for b in bars], "high": [round(b[2], 2) for b in bars], "low": [round(b[3], 2) for b in bars], "close": [round(b[4], 2) for b in bars], "volume": [b[5] // 1000 if sym in LOW_VOLUME else b[5] for b in bars]}], "adjclose": [{"adjclose": [round(b[4], 2) for b in bars]}]}}]}})  # fmt: skip

    # ---- deep-dive mocks (all SYNTHETIC) ----
    def _finra(self, path: str) -> httpx.Response:
        d = re.search(r"(\d{8})", path).group(1)
        if "shrt" in path:
            rows = [
                "accountingYearMonthNumber,symbolCode,issueName,currentShortPositionQuantity,previousShortPositionQuantity,changePercent,averageDailyVolumeQuantity,daysToCoverQuantity,settlementDate"
            ]
            for i, t in enumerate(MOCK_DEEP):
                rows.append(
                    f"202609,{t},Mock {t},{1_000_000 * (i + 1)},{900_000 * (i + 1)},11.1,{500_000 * (i + 1)},{2.0 + i},{d[:4]}-{d[4:6]}-{d[6:]}"
                )
            return httpx.Response(200, text="\n".join(rows))
        if "CNMSshvol" in path:
            rows = ["Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"]
            for i, t in enumerate(MOCK_DEEP):
                rows.append(f"{d}|{t}|{400_000 + i * 1000}|1000|{1_000_000 + i * 5000}|Q,N")
            rows.append("1234 records")
            return httpx.Response(200, text="\n".join(rows))
        return httpx.Response(404, text="mock")

    def _finnhub(self, path: str, q: dict) -> httpx.Response:
        if path.endswith("/stock/peers"):
            sym = q.get("symbol", "")
            group = next((g for g in MOCK_PEER_GROUPS if sym in g), [sym])
            return self._json(list(group))
        if "recommendation" in path:
            p = self.today.replace(day=1)
            return self._json(
                [
                    {
                        "symbol": q.get("symbol"),
                        "period": p.isoformat(),
                        "strongBuy": 5,
                        "buy": 12,
                        "hold": 8,
                        "sell": 1,
                        "strongSell": 0,
                    },
                    {
                        "symbol": q.get("symbol"),
                        "period": (p - timedelta(days=31)).replace(day=1).isoformat(),
                        "strongBuy": 4,
                        "buy": 11,
                        "hold": 9,
                        "sell": 2,
                        "strongSell": 0,
                    },
                ]
            )
        d = self.today + timedelta(days=3)
        return self._json(
            {
                "earningsCalendar": [
                    {
                        "symbol": t,
                        "date": d.isoformat(),
                        "hour": "amc",
                        "epsEstimate": 1.5,
                        "revenueEstimate": 1e9,
                    }
                    for t in MOCK_DEEP[:1]
                ]
            }
        )

    def _alpha(self, q: dict) -> httpx.Response:
        if q.get("function") == "EARNINGS_CALENDAR":
            d1 = self.today + timedelta(days=1)
            d2 = self.today + timedelta(days=30)
            body = (
                "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
                + f"MHLT,Mockhealth,{d1},2026-09-30,1.2,USD\nMIND,Mockindustrial,{d2},2026-09-30,0.8,USD\nNOTLISTED,Other,{d2},2026-09-30,0.1,USD\n"
            )
            return httpx.Response(200, text=body)
        if q.get("function") == "EARNINGS_CALL_TRANSCRIPT":
            return self._json(
                {
                    "symbol": q.get("symbol"),
                    "quarter": q.get("quarter"),
                    "transcript": [
                        {
                            "speaker": "Mock CEO",
                            "title": "CEO",
                            "content": "Synthetic prepared remarks about demand and margins.",
                        },
                        {
                            "speaker": "Mock Analyst",
                            "content": "Synthetic question about guidance.",
                        },
                    ],
                }
            )
        return self._json({"Information": "mock: unknown function"})

    def _atom(self) -> str:
        def e(acc, form, name, cik):
            return f'<entry><title>{form} - {name} ({cik}) (Filer)</title><link href="https://www.sec.gov/Archives/edgar/data/{int(cik)}/x.htm"/><updated>{self.today}T14:00:00-04:00</updated><id>urn:tag:sec.gov,2008:accession-number={acc}</id></entry>'

        return (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            + e("0009000001-26-000001", "8-K", "MOCKHEALTH THERAPEUTICS INC", "0009000001")
            + e("0000000999-26-000002", "8-K", "UNKNOWN CO", "0000000999")
            + "</feed>"
        )

    def _facts(self, cik: int) -> dict:
        def fy(end, val, start, fy_):
            return {
                "start": start,
                "end": end,
                "val": val,
                "accn": "0000000000-26-000001",
                "fy": fy_,
                "fp": "FY",
                "form": "10-K",
                "filed": end[:4] + "-12-31",
            }

        return {
            "cik": cik,
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                fy("2025-12-31", 1_000_000_000, "2025-01-01", 2025),
                                fy("2024-12-31", 800_000_000, "2024-01-01", 2024),
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {"USD": [fy("2025-12-31", 120_000_000, "2025-01-01", 2025)]}
                    },
                    "Assets": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2025-12-31",
                                    "val": 2_000_000_000,
                                    "form": "10-K",
                                    "filed": "2026-02-01",
                                }
                            ]
                        }
                    },
                    "EarningsPerShareDiluted": {
                        "units": {"USD/shares": [fy("2025-12-31", 2.4, "2025-01-01", 2025)]}
                    },
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {"end": "2026-02-01", "val": 50_000_000, "filed": "2026-02-05"}
                            ]
                        }
                    }
                },
            },
        }

    def _options(self, sym: str) -> httpx.Response:
        if sym.startswith("BAD"):
            return httpx.Response(401, text="Unauthorized")
        exp = int(
            datetime.combine(
                self.today + timedelta(days=21), datetime.min.time(), tzinfo=UTC
            ).timestamp()
        )
        ch = lambda k, iv, v: {"strike": k, "impliedVolatility": iv, "volume": v}  # noqa: E731
        return self._json(
            {
                "optionChain": {
                    "result": [
                        {
                            "quote": {"regularMarketPrice": 100.0},
                            "options": [
                                {
                                    "expirationDate": exp,
                                    "calls": [ch(95, 0.4, 100), ch(100, 0.35, 300)],
                                    "puts": [ch(100, 0.37, 200), ch(105, 0.45, 50)],
                                }
                            ],
                        }
                    ]
                }
            }
        )

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
