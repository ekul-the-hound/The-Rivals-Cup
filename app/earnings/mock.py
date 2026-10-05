"""Offline SYNTHETIC world for the earnings scan (`--mock`, tests and the demo dashboard).

Extends the repo's MockWorld with an earnings calendar, earnings history, insiders, 13F tables, a
politician feed and news. Nothing here resembles real companies, prices or filings.
"""

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.providers.mock_transport import MockWorld, synthetic_bars

TICKERS = [
    "KO", "PEP", "HD", "LOW", "V", "MA", "XOM", "CVX", "JPM", "BAC", "UPS", "FDX", "MRK", "PFE",
    "AMD", "INTC",
]  # fmt: skip
PEERS = [["KO", "PEP", "HD", "LOW"], ["V", "MA", "JPM", "BAC"], ["XOM", "CVX", "UPS", "FDX"],
         ["MRK", "PFE", "AMD", "INTC"]]  # fmt: skip
HOLDER_CIKS = {1364742: "BLK", 102909: "VAN", 93751: "SST", 1067983: "BRK"}
POLITICIAN_URL = "https://mock-politicians.example/trades.json"


def _h(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:10], 16)


class EarningsMockWorld(MockWorld):
    def handle(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        q = {k: v[0] for k, v in parse_qs(urlparse(str(req.url)).query).items()}
        if host == "mock-politicians.example":
            return self._politicians()
        if host == "api.nasdaq.com":
            return self._nasdaq(q)
        if host == "cdn.finra.org" and "shrt" in path:
            return self._short_interest(path)
        if host == "finnhub.io":
            if path.endswith("/company-news"):
                return self._company_news(q.get("symbol", ""))
            if path.endswith("/calendar/earnings"):
                return self._calendar(q)
            if "recommendation" in path:
                return self._recs(q.get("symbol", ""))
            if path.endswith("/stock/peers"):
                sym = q.get("symbol", "")
                return self._json(next((g for g in PEERS if sym in g), [sym]))
        if host == "data.sec.gov":
            m = re.search(r"CIK(\d+)", path)
            if m and int(m.group(1)) in HOLDER_CIKS:
                return self._holder_submissions(int(m.group(1)))
        if host == "www.sec.gov" and path.startswith("/Archives/edgar/data/"):
            parts = path.strip("/").split("/")
            if int(parts[3]) in HOLDER_CIKS:
                return self._holder_archive(int(parts[3]), parts[4], parts[-1])
        if host == "news.google.com":
            return self._headlines(q.get("q", ""))
        if host == "query1.finance.yahoo.com" and "chart" in path and q.get("range") == "2y":
            return self._long_history(path.rsplit("/", 1)[-1])
        return super().handle(req)

    # ---- extra sources ----
    def _nasdaq(self, q: dict) -> httpx.Response:
        day = date.fromisoformat(q["date"])
        rows = []
        for t in TICKERS:
            if self._report_date(t) == day:
                eps = 1.0 + (_h(t, "e") % 100) / 100
                skew = 1.0 if _h(t, "nas") % 3 else 1.25  # some names disagree with Finnhub
                rows.append({"symbol": t, "time": ("time-pre-market", "time-after-hours")[_h(t, "h") % 2],
                             "epsForecast": f"${eps * skew:.2f}", "noOfEsts": str(3 + _h(t, "n") % 9),
                             "lastYearEPS": f"${eps * 0.9:.2f}"})  # fmt: skip
        return self._json({"data": {"rows": rows}})

    def _short_interest(self, path: str) -> httpx.Response:
        d = re.search(r"(\d{8})", path).group(1)
        rows = ["accountingYearMonthNumber,symbolCode,issueName,currentShortPositionQuantity,"
                "previousShortPositionQuantity,changePercent,averageDailyVolumeQuantity,"
                "daysToCoverQuantity,settlementDate"]  # fmt: skip
        for t in TICKERS:
            dtc = 1.5 + (_h(t, "dtc") % 80) / 10
            rows.append(
                f"202609,{t},Mock {t},{2_000_000 + _h(t, 's') % 5_000_000},1800000,"
                f"{(_h(t, 'c') % 40) - 15},{800_000},{dtc:.2f},{d[:4]}-{d[4:6]}-{d[6:]}"
            )
        return httpx.Response(200, text="\n".join(rows))

    def _company_news(self, sym: str) -> httpx.Response:
        n = 1 + _h(sym, "cn") % 3
        base = int(datetime.now(UTC).timestamp())
        words = (
            "raises guidance",
            "faces probe",
            "launches product",
            "downgraded",
            "wins contract",
        )
        return self._json([
            {"headline": f"{sym} {words[(_h(sym, 'w', str(i)) % len(words))]} (mock {i})",
             "source": "MockWire", "datetime": base - 3600 * (i + 1), "url": f"https://mock.example/{sym}/{i}"}
            for i in range(n)
        ])  # fmt: skip

    # ---- calendar & history ----
    def _monday(self) -> date:
        m = self.today - timedelta(days=self.today.weekday())
        return m + timedelta(days=7) if self.today.weekday() >= 5 else m

    def _report_date(self, t: str) -> date:
        slot = _h(t, "slot") % 10  # 0-9 weekdays across this and next week
        return self._monday() + timedelta(days=slot // 5 * 7 + slot % 5)

    def _calendar(self, q: dict) -> httpx.Response:
        sym = q.get("symbol")
        start, end = date.fromisoformat(q["from"]), date.fromisoformat(q["to"])
        rows = []
        if end >= self.today and not sym:
            for t in TICKERS:
                d = self._report_date(t)
                if start <= d <= end:
                    rows.append({"symbol": t, "date": d.isoformat(), "hour": ("bmo", "amc")[_h(t, "h") % 2],
                                 "epsEstimate": 1.0 + (_h(t, "e") % 100) / 100, "revenueEstimate": 1e9 * (1 + _h(t, "r") % 40)})  # fmt: skip
        elif sym:
            bars = synthetic_bars(sym, self.today, 520)
            dates = [b[0] for b in bars]
            beater = _h(sym, "beat") % 100 < 70
            for i, idx in enumerate(range(len(dates) - 4, 30, -63)[:8]):
                d = dates[idx]
                if idx == len(dates) - 4 and _h(sym, "peer") % 2:
                    pass  # a very recent report: lets peers feed the read-through
                elif i == 0:
                    continue
                est = 1.0 + (_h(sym, str(i)) % 50) / 100
                good = beater if _h(sym, "q", str(i)) % 10 < 8 else not beater
                act = est * (1.06 if good else 0.94)
                if start <= d <= end:
                    rows.append({"symbol": sym, "date": d.isoformat(), "hour": "amc", "epsActual": round(act, 3),
                                 "epsEstimate": round(est, 3), "revenueActual": 1e9, "revenueEstimate": 1e9})  # fmt: skip
        return self._json({"earningsCalendar": rows})

    def _recs(self, sym: str) -> httpx.Response:
        b = _h(sym, "rec") % 8
        p = self.today.replace(day=1)
        rows = [
            {"symbol": sym, "period": (p - timedelta(days=31 * k)).replace(day=1).isoformat(),
             "strongBuy": 4 + b - k, "buy": 10, "hold": 8 - b // 2, "sell": 1 + k, "strongSell": 0}
            for k in range(3)
        ]  # fmt: skip
        return self._json(rows)

    def _long_history(self, sym: str) -> httpx.Response:
        bars = synthetic_bars(sym, self.today, 520)
        ts = [
            int(datetime(b[0].year, b[0].month, b[0].day, 14, 30, tzinfo=UTC).timestamp())
            for b in bars
        ]
        return self._json({"chart": {"error": None, "result": [{
            "meta": {"symbol": sym, "gmtoffset": -14400, "regularMarketPrice": round(bars[-1][4], 2),
                     "regularMarketTime": ts[-1], "chartPreviousClose": round(bars[-2][4], 2)},
            "timestamp": ts,
            "indicators": {"quote": [{"open": [round(b[1], 2) for b in bars], "high": [round(b[2], 2) for b in bars],
                                      "low": [round(b[3], 2) for b in bars], "close": [round(b[4], 2) for b in bars],
                                      "volume": [b[5] for b in bars]}],
                           "adjclose": [{"adjclose": [round(b[4], 2) for b in bars]}]}}]}})  # fmt: skip

    # ---- insiders ----
    def _filings_for(self, ticker: str):
        yield from super()._filings_for(ticker)
        extra = {"KO": [("P", 4, "5000", "58.0"), ("P", 9, "3000", "57.5")], "HD": [("P", 6, "1500", "330.0")],
                 "JPM": [("S", 5, "40000", "210.0")]}  # fmt: skip
        from app.services.providers.mock_transport import CIKS

        base = len(list(super()._filings_for(ticker)))
        for i, (code, ago, shares, price) in enumerate(extra.get(ticker, [])):
            cik = CIKS[ticker]
            yield {"acc": f"{cik:010d}-26-{900 + base + i:06d}", "form": "4",
                   "date": (self.today - timedelta(days=ago)).isoformat(), "items": "",
                   "doc": "xslF345X05/form4.xml", "body": f"FORM4:{code}:{shares}:{price}"}  # fmt: skip

    # ---- politicians ----
    def _politicians(self) -> httpx.Response:
        d = lambda n: (self.today - timedelta(days=n)).isoformat()  # noqa: E731
        return self._json([
            {"ticker": "KO", "representative": "Rep. Mock One", "type": "purchase", "amount": "$15,001 - $50,000", "transaction_date": d(20), "disclosure_date": d(5)},
            {"ticker": "KO", "senator": "Sen. Mock Two", "type": "purchase", "amount": "$1,001 - $15,000", "transaction_date": d(30), "disclosure_date": d(8)},
            {"ticker": "XOM", "representative": "Rep. Mock Three", "type": "sale_full", "amount": "$50,001 - $100,000", "transaction_date": d(15), "disclosure_date": d(3)},
            {"ticker": "--", "representative": "Rep. Mock Four", "type": "purchase", "amount": "$1,001 - $15,000", "transaction_date": d(15)},
        ])  # fmt: skip

    # ---- 13F ----
    def _holder_submissions(self, cik: int) -> httpx.Response:
        accs = [(f"{cik:010d}-26-000002", (self.today - timedelta(days=40)).isoformat(), "2026-06-30"),
                (f"{cik:010d}-26-000001", (self.today - timedelta(days=130)).isoformat(), "2026-03-31")]  # fmt: skip
        return self._json({"cik": str(cik), "filings": {"recent": {
            "accessionNumber": [a[0] for a in accs], "filingDate": [a[1] for a in accs],
            "reportDate": [a[2] for a in accs], "form": ["13F-HR", "13F-HR"],
            "primaryDocument": ["xslForm13F_X02/primary_doc.xml"] * 2, "items": ["", ""],
            "primaryDocDescription": ["13F-HR", "13F-HR"]}}})  # fmt: skip

    def _holder_archive(self, cik: int, acc: str, doc: str) -> httpx.Response:
        if doc == "index.json":
            return self._json({"directory": {"item": [
                {"name": "primary_doc.xml", "size": "2000"}, {"name": "infotable.xml", "size": "90000"}]}})  # fmt: skip
        prior = acc.endswith("000001")
        rows = ""
        for t in TICKERS:
            base = 1_000_000 * (1 + _h(t, str(cik)) % 20)
            shares = base if prior else int(base * (1 + ((_h(t, "chg", str(cik)) % 21) - 8) / 100))
            rows += (f"<infoTable><nameOfIssuer>{t} MOCK CORP</nameOfIssuer><titleOfClass>COM</titleOfClass>"
                     f"<cusip>000000000</cusip><value>{shares * 50}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt>"
                     f"<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>")  # fmt: skip
        return httpx.Response(
            200,
            text=f'<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">{rows}</informationTable>',
        )

    # ---- news ----
    def _headlines(self, query: str) -> httpx.Response:
        items = []
        for t in TICKERS:
            if re.search(rf"\b{t}\b", query.upper()):
                tone = _h(t, "news") % 3
                titles = [
                    (f"{t} Mock beats estimates and raises outlook", "Reuters", "reuters.com"),
                    (f"{t} Mock shares slump after analyst downgrade", "CNBC", "cnbc.com"),
                    (f"{t} Mock announces new product line", "Business Wire", "businesswire.com"),
                    (f"{t} Mock strong demand lifts record sales", "Reuters", "reuters.com"),
                ]
                pick = (
                    titles
                    if tone == 0
                    else titles[1:3]
                    if tone == 1
                    else [titles[0], titles[2], titles[3]]
                )
                for i, (title, pub, dom) in enumerate(pick):
                    when = datetime.now(UTC) - timedelta(days=1 + i)
                    items.append(f'<item><title>{title} - {pub}</title><link>https://news.google.com/rss/articles/{_h(title) % 10**8}</link>'
                                 f'<pubDate>{format_datetime(when)}</pubDate><source url="https://www.{dom}">{pub}</source></item>')  # fmt: skip
        return httpx.Response(200, text=f"<rss><channel>{''.join(items)}</channel></rss>")
