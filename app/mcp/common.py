"""Context + helpers shared by tools and resources. Read-only."""

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.config import Settings
from app.mcp.envelope import clean_text
from app.models.enums import DataStatus
from app.services.features.metrics import (
    adv_dollar,
    adv_shares,
    close_series,
    estimate_liquidity_cap,
    trailing_returns,
)
from app.services.validation.freshness import price_status


class ToolError(Exception):
    """A user-safe error message (never contains internals)."""


@dataclass
class ToolContext:
    store: Any  # ReadOnlyStore / AsOfStore
    settings: Settings
    now: datetime  # as_of if supplied, else current UTC
    request_id: str = ""

    @property
    def today(self) -> date:
        return self.now.date()


def securities_by_ticker(ctx: ToolContext) -> dict[str, dict[str, Any]]:
    return {s["ticker"]: s for s in ctx.store.select("securities")}


def get_security(ctx: ToolContext, ticker: str) -> dict[str, Any]:
    rows = ctx.store.select("securities", eq={"ticker": ticker}, limit=1)
    if not rows:
        raise ToolError(f"{ticker} is not in the research universe (securities table)")
    return rows[0]


def bars_for(ctx: ToolContext, security_id: str, limit: int = 130) -> list[dict[str, Any]]:
    rows = ctx.store.select(
        "market_bars",
        eq={"security_id": security_id, "timeframe": "1D"},
        order="bar_date",
        desc=True,
        limit=limit,
    )
    return sorted(rows, key=lambda r: str(r["bar_date"]))


def price_freshness(
    ticker: str, bars: list[dict[str, Any]], ctx: ToolContext, missing: list[str]
) -> dict[str, Any]:
    last = date.fromisoformat(str(bars[-1]["bar_date"])[:10]) if bars else None
    st = price_status(last, ctx.today)
    if st != DataStatus.AVAILABLE:
        missing.append(
            f"{ticker}: price data {st.value}" + (f" (latest bar {last})" if last else "")
        )
    return {"latest_bar": last.isoformat() if last else None, "status": st.value}


def public_security(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": s["ticker"],
        "name": clean_text(s.get("name"), 120),
        "security_type": s.get("security_type"),
        "exchange": s.get("exchange"),
        "country": s.get("country"),
        "sector": s.get("sector"),
        "industry": clean_text(s.get("industry"), 80),
        "is_etf": bool(s.get("is_etf")),
        "leverage_factor": s.get("leverage_factor"),
        "is_active": s.get("is_active", True),
        "wsr_eligibility": s.get("wsr_eligibility"),
        "wsr_eligibility_note": "UNVERIFIED unless a human marked it; Trader View is authoritative",
    }


def returns_block(bars: list[dict[str, Any]]) -> dict[str, float | None]:
    ser = close_series(bars)
    r = trailing_returns(ser) if len(ser) else {}
    return {f"{n}d": r.get(n) for n in (1, 5, 20, 60)}


def liquidity_block(bars: list[dict[str, Any]], ctx: ToolContext) -> dict[str, Any]:
    adv = adv_dollar(bars)
    cap = estimate_liquidity_cap(adv, ctx.settings.wsr_est_adv_pct_cap)
    return {
        "adv_20d_usd": round(adv) if adv else None,
        "adv_20d_shares": round(adv_shares(bars) or 0) or None,
        "estimated_cap_pct_of_adv": ctx.settings.wsr_est_adv_pct_cap,
        "estimated_leg_cap_usd": cap,
        "basis": "ESTIMATE: 1% of trailing 20-day average dollar volume; WSR's actual limits control",
    }


def encode_cursor(offset: int, key: str) -> str:
    raw = json.dumps({"o": offset, "k": key}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def paginate(
    items: list[Any], limit: int, cursor: str | None, key_parts: Any
) -> tuple[list[Any], str | None]:
    key = hashlib.sha256(json.dumps(key_parts, sort_keys=True, default=str).encode()).hexdigest()[
        :12
    ]
    offset = 0
    if cursor:
        try:
            pad = "=" * (-len(cursor) % 4)
            d = json.loads(base64.urlsafe_b64decode(cursor + pad))
            offset, k = int(d["o"]), d["k"]
        except Exception as exc:
            raise ToolError("invalid cursor") from exc
        if k != key or offset < 0:
            raise ToolError("cursor does not match this query; start again without a cursor")
    page = items[offset : offset + limit]
    nxt = encode_cursor(offset + limit, key) if offset + limit < len(items) else None
    return page, nxt


def evidence_sources(
    ctx: ToolContext, security_ids: list[str], max_each: int = 3
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    since = ctx.today - timedelta(days=30)
    for sid in security_ids:
        for f in ctx.store.select(
            "filing_documents",
            eq={"security_id": sid},
            gte={"filed_at": since},
            order="filed_at",
            desc=True,
            limit=max_each,
        ):
            out.append({"type": "SEC_FILING", "id": f.get("accession_number"), "url": f.get("url"), "form_type": f.get("form_type")})  # fmt: skip
        for n in ctx.store.select(
            "news_items", eq={"security_id": sid}, order="published_at", desc=True, limit=max_each
        ):
            out.append({"type": "NEWS", "id": n.get("content_hash"), "url": n.get("url")})
    return out
