"""SEC EDGAR provider (official, free). Declared user agent required; <=10 req/s (we use 8)."""

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from html.parser import HTMLParser

import httpx
from pydantic import BaseModel

from app.services.providers.base import FileCache, HttpClient, ProviderError, ProviderUnavailable

FORMS = ("8-K", "10-Q", "10-K", "4")
_UA_RE = re.compile(r"\S.*\s\S+@\S+\.\S+")


def valid_sec_user_agent(ua: str) -> bool:
    """SEC fair-access policy: identify yourself, e.g. 'Jane Doe jane@example.com'."""
    return bool(_UA_RE.search(ua or ""))


class CikInfo(BaseModel):
    cik: int
    title: str


class SicInfo(BaseModel):
    sic: int | None = None
    description: str | None = None


class FeedEntry(BaseModel):
    accession_number: str
    cik: str | None = None
    company_name: str | None = None
    form_type: str | None = None
    filed_at: datetime | None = None
    title: str
    link: str | None = None


class FilingMeta(BaseModel):
    accession_number: str
    form_type: str
    filed_at: date
    period_of_report: date | None = None
    primary_document: str
    items: list[str] = []
    description: str | None = None
    cik: int

    @property
    def url(self) -> str:
        acc = self.accession_number.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{acc}/{self.primary_document}"


class Form4Summary(BaseModel):
    owner: str | None = None
    title: str | None = None
    is_officer: bool = False
    is_director: bool = False
    transactions: list[dict] = []
    net_value_usd: float = 0.0  # + acquired, - disposed


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_excerpt(html: str, max_chars: int = 4000) -> str:
    p = _Text()
    p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    m = re.search(r"\bItem\s+\d\.\d{2}", text)  # skip cover-page boilerplate
    if m:
        text = text[m.start() :]
    return text[:max_chars]


def parse_form4(xml_text: str) -> Form4Summary | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    g = lambda node, path: (node.findtext(path) or "").strip()  # noqa: E731
    out = Form4Summary(
        owner=g(root, "reportingOwner/reportingOwnerId/rptOwnerName") or None,
        title=g(root, "reportingOwner/reportingOwnerRelationship/officerTitle") or None,
        is_officer=g(root, "reportingOwner/reportingOwnerRelationship/isOfficer") in {"1", "true"},
        is_director=g(root, "reportingOwner/reportingOwnerRelationship/isDirector")
        in {"1", "true"},
    )
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        try:
            shares = float(g(tx, "transactionAmounts/transactionShares/value") or 0)
            price = float(g(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        ad = g(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
        value = shares * price * (1 if ad == "A" else -1)
        out.transactions.append(
            {
                "date": g(tx, "transactionDate/value"),
                "code": g(tx, "transactionCoding/transactionCode"),
                "shares": shares,
                "price": price,
                "acquired_disposed": ad,
                "value_usd": round(value, 2),
            }
        )
        out.net_value_usd += value
    out.net_value_usd = round(out.net_value_usd, 2)
    return out


class SecProvider:
    name = "sec_edgar"

    def __init__(
        self,
        user_agent: str,
        *,
        cache_dir: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        sleep=None,
    ) -> None:
        if not valid_sec_user_agent(user_agent):
            raise ProviderUnavailable("SEC_USER_AGENT must look like 'Your Name you@example.com'")
        kw = {"sleep": sleep} if sleep else {}
        self.http = HttpClient(
            self.name,
            user_agent=user_agent,
            per_second=8.0,
            timeout=timeout,
            cache=FileCache(cache_dir),
            transport=transport,
            **kw,
        )

    async def healthcheck(self) -> bool:
        return bool(await self.ticker_cik_map())

    async def aclose(self) -> None:
        await self.http.aclose()

    async def ticker_cik_map(self) -> dict[str, CikInfo]:
        data = await self.http.get_json(
            "https://www.sec.gov/files/company_tickers.json", ttl=7 * 86400
        )
        return {
            v["ticker"].upper(): CikInfo(cik=int(v["cik_str"]), title=v["title"])
            for v in data.values()
        }

    async def sic_info(self, cik: int) -> SicInfo:
        """SEC Standard Industrial Classification code from the issuer's submissions record."""
        data = await self.http.get_json(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json", ttl=30 * 86400
        )
        raw = data.get("sic")
        return SicInfo(
            sic=int(raw) if str(raw or "").isdigit() else None,
            description=data.get("sicDescription") or None,
        )

    async def company_facts(self, cik: int) -> dict:
        """XBRL company facts (official, free). Large; callers should only request a shortlist."""
        data = await self.http.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", ttl=3 * 86400
        )
        if not isinstance(data, dict) or "facts" not in data:
            raise ProviderError(f"sec: no XBRL facts for CIK {cik}")
        return data

    async def current_filings_feed(self, form: str = "8-K", count: int = 100) -> list[FeedEntry]:
        """SEC "latest filings" Atom feed (EDGAR RSS). Newest first."""
        text = await self.http.get_text(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={
                "action": "getcurrent",
                "type": form,
                "owner": "include",
                "count": count,
                "output": "atom",
            },
            ttl=15 * 60,
        )
        return parse_atom_feed(text)

    async def recent_filings(
        self, cik: int, since: date, forms: tuple[str, ...] = FORMS
    ) -> list[FilingMeta]:
        data = await self.http.get_json(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json", ttl=6 * 3600
        )
        r = data.get("filings", {}).get("recent", {})
        out: list[FilingMeta] = []
        for i, form in enumerate(r.get("form", [])):
            if form not in forms:
                continue
            filed = date.fromisoformat(r["filingDate"][i])
            if filed < since:
                continue
            rep = r.get("reportDate", [""] * (i + 1))[i]
            out.append(
                FilingMeta(
                    accession_number=r["accessionNumber"][i],
                    form_type=form,
                    filed_at=filed,
                    period_of_report=date.fromisoformat(rep) if rep else None,
                    primary_document=r["primaryDocument"][i],
                    items=[x for x in (r.get("items", [""] * (i + 1))[i] or "").split(",") if x],
                    description=(r.get("primaryDocDescription", [""] * (i + 1))[i] or None),
                    cik=cik,
                )
            )
        return out

    async def filing_excerpt(self, f: FilingMeta, max_chars: int = 4000) -> str:
        """Bounded excerpt: reads at most ~300 KB and keeps max_chars of text."""
        html = await self.http.get_text(f.url, ttl=30 * 86400, max_bytes=300_000)
        return html_to_excerpt(html, max_chars)

    async def form4_summary(self, f: FilingMeta) -> Form4Summary | None:
        raw_name = f.primary_document.rsplit("/", 1)[-1]  # strip xslF345X0N/ rendering prefix
        acc = f.accession_number.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{f.cik}/{acc}/{raw_name}"
        try:
            return parse_form4(await self.http.get_text(url, ttl=30 * 86400, max_bytes=200_000))
        except ProviderError:
            return None


_ATOM = "{http://www.w3.org/2005/Atom}"
_ACC = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")
_TITLE = re.compile(r"^(?P<form>.+?)\s+-\s+(?P<name>.+?)\s+\((?P<cik>\d{6,10})\)")


def parse_atom_feed(xml_text: str) -> list[FeedEntry]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProviderError("sec: filing feed was not valid XML") from exc
    out: list[FeedEntry] = []
    for e in root.iter(f"{_ATOM}entry"):
        title = (e.findtext(f"{_ATOM}title") or "").strip()
        ident = e.findtext(f"{_ATOM}id") or ""
        acc = _ACC.search(ident)
        if not acc:
            continue
        m = _TITLE.match(title)
        link_el = e.find(f"{_ATOM}link")
        upd = e.findtext(f"{_ATOM}updated")
        try:
            filed = datetime.fromisoformat(upd.replace("Z", "+00:00")) if upd else None
        except ValueError:
            filed = None
        out.append(
            FeedEntry(
                accession_number=acc.group(1), title=title, filed_at=filed,
                link=link_el.get("href") if link_el is not None else None,
                form_type=m.group("form") if m else None, company_name=m.group("name") if m else None,
                cik=m.group("cik").zfill(10) if m else None,
            )
        )  # fmt: skip
    return out


FLOW_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}  # fmt: skip
STOCK_TAGS = {
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}  # fmt: skip
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}


def _entries(facts: dict, tag: str) -> list[dict]:
    node = facts.get("us-gaap", {}).get(tag, {}).get("units", {})
    return [e for unit in node.values() for e in unit]


def _annual(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        if (
            e.get("form") not in ANNUAL_FORMS
            or e.get("fp") != "FY"
            or not (e.get("start") and e.get("end"))
        ):
            continue
        days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
        if 330 <= days <= 380:
            out.append(e)
    return sorted(out, key=lambda e: (e["end"], e.get("filed", "")))


def summarize_company_facts(data: dict) -> dict:
    """Latest annual flow metrics + latest balance sheet + shares outstanding, with provenance."""
    facts = data.get("facts", {})
    out: dict = {"cik": str(data.get("cik", "")).zfill(10), "metrics": {}}
    for field, tags in FLOW_TAGS.items():
        # companies switch XBRL tags over time (e.g. Revenues -> RevenueFromContract...), so take the
        # tag whose newest annual value is most recent; earlier-listed tags win ties.
        best: tuple[str, list[dict]] | None = None
        for tag in tags:
            ann = _annual(_entries(facts, tag))
            if ann and (best is None or ann[-1]["end"] > best[1][-1]["end"]):
                best = (tag, ann)
        if best:
            tag, ann = best
            last = ann[-1]
            out[field] = last["val"]
            out["metrics"][field] = {
                "tag": tag,
                "end": last["end"],
                "accn": last.get("accn"),
                "filed": last.get("filed"),
            }
            if field == "revenue":
                out["period_end"], out["fiscal_period"] = last["end"], "FY"
                prior = [e for e in ann if e["end"] < last["end"]]
                if prior and prior[-1]["val"]:
                    out["prior_year_revenue"] = prior[-1]["val"]
                    out["revenue_growth_pct"] = round((last["val"] / prior[-1]["val"] - 1) * 100, 2)
    for field, tags in STOCK_TAGS.items():
        best_e: tuple[str, dict] | None = None
        for tag in tags:
            es = sorted(
                (e for e in _entries(facts, tag) if e.get("end")),
                key=lambda e: (e["end"], e.get("filed", "")),
            )
            if es and (best_e is None or es[-1]["end"] > best_e[1]["end"]):
                best_e = (tag, es[-1])
        if best_e:
            tag, e = best_e
            out[field] = e["val"]
            out["metrics"][field] = {"tag": tag, "end": e["end"], "accn": e.get("accn")}
    shares = [
        e
        for unit in facts.get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .values()
        for e in unit
    ]
    if shares:
        s = sorted(shares, key=lambda e: (e.get("end", ""), e.get("filed", "")))[-1]
        out["shares_outstanding"] = s["val"]
        out["metrics"]["shares_outstanding"] = {
            "tag": "dei:EntityCommonStockSharesOutstanding",
            "end": s.get("end"),
        }
    return out


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)
