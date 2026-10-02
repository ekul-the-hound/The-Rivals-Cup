"""Target-sector universe jobs (Health Care, Industrials, Financials, Utilities, Real Estate).

All four jobs write only to security_master / security_master_view_state (via UniverseWriteGuard).
They read public listing and quote data. They never contact WSR, Trader View, a browser or a broker.
"""

from datetime import datetime
from typing import Any

from app.db.store import select_all
from app.models.enums import DataStatus
from app.models.universe import OTHER_SECTOR, TARGET_SECTORS, SecurityType
from app.services.ingestion.runner import JobContext, JobResult
from app.services.providers.base import ProviderError
from app.services.universe.master import (
    UniverseWriteGuard,
    inactive_rows,
    listing_rows,
    upsert_uniform,
)
from app.services.universe.repo import rebuild_view_state
from app.services.universe.sectors import CsvSectorMappingAdapter, normalize_sector
from app.services.universe.sic import BLANK_CHECK_SIC, REIT_SIC, sector_from_sic
from app.services.universe.views import UniverseParams

MIN_LISTINGS = 2000  # a directory this small is a bad download, not a market
KEEP_FRACTION = 0.7  # never deactivate when the new list is < 70% of the stored active list


def _guard(ctx: JobContext) -> UniverseWriteGuard:
    return UniverseWriteGuard(ctx.db)


def _age_key(v: Any) -> str:
    return "" if not v else str(v)  # never-checked sorts first


async def refresh_us_listed_symbol_universe(ctx: JobContext) -> JobResult:
    nasdaq = ctx.providers.require("nasdaq")
    listings, file_time = await nasdaq.all_listings()
    warnings: list[str] = []
    existing = {r["ticker"]: r for r in select_all(ctx.db, "security_master")}
    cik_map: dict[str, Any] = {}
    sec = ctx.providers.sec
    if sec:
        try:
            cik_map = await sec.ticker_cik_map()
        except ProviderError as exc:
            warnings.append(f"sec cik map unavailable: {exc}")
    asof = file_time or ctx.now
    rows, new = listing_rows(listings, existing, cik_map, asof)
    guard = _guard(ctx)
    written = upsert_uniform(guard, "security_master", rows, "ticker")
    current = {r["ticker"] for r in rows}
    active_before = sum(1 for r in existing.values() if r.get("is_active", True))
    gone = inactive_rows(existing, current, asof)
    if len(listings) < MIN_LISTINGS and active_before >= MIN_LISTINGS:
        warnings.append(f"only {len(listings)} listings returned; delistings not applied")
    elif active_before and len(current) < KEEP_FRACTION * active_before:
        warnings.append(
            f"new list ({len(current)}) is under {KEEP_FRACTION:.0%} of stored active ({active_before}); "
            "delistings not applied"
        )
    elif gone:
        written += upsert_uniform(guard, "security_master", gone, "ticker")
    return JobResult(
        job="refresh_us_listed_symbol_universe",
        rows_read=len(listings),
        rows_written=written,
        message=f"{len(listings)} listings ({new} new, {len(gone)} no longer listed)",
        warnings=warnings,
    )


async def classify_target_sectors(ctx: JobContext) -> JobResult:
    """Sector order: (1) the issuer's SEC SIC code (official, fast), (2) Yahoo's sector label for
    symbols without an SEC record, (3) your optional CSV (UNVERIFIED). Never inferred from a name."""
    s = ctx.settings
    sec, yahoo = ctx.providers.sec, ctx.providers.yahoo
    adapter = None
    warnings: list[str] = []
    if s.sector_mapping_adapter_enabled:
        if not s.sector_mapping_csv:
            warnings.append("SECTOR_MAPPING_ADAPTER_ENABLED but SECTOR_MAPPING_CSV is empty")
        else:
            try:
                adapter = CsvSectorMappingAdapter(s.sector_mapping_csv)
            except OSError as exc:
                warnings.append(f"sector mapping csv unreadable: {exc}")
    if sec is None and yahoo is None and adapter is None:
        return JobResult(
            job="classify_target_sectors", status="SKIPPED", message="no sector source enabled"
        )
    master = select_all(ctx.db, "security_master")
    cutoff = ctx.now.timestamp() - s.universe_reclassify_days * 86400
    todo = []
    for r in master:
        if not r.get("is_active", True) or r.get("is_test_issue"):
            continue
        if r.get("security_type") not in (SecurityType.COMMON_STOCK, SecurityType.ADR):
            continue
        if r.get("sector_source") == "MANUAL":
            continue
        checked = r.get("profile_checked_at")
        if r.get("sector") and checked and _ts(checked) > cutoff:
            continue
        todo.append(r)
    todo.sort(key=lambda r: _age_key(r.get("profile_checked_at")))
    batch = todo[: s.universe_classify_batch]
    rows: list[dict[str, Any]] = []
    state = {"yahoo_misses": 0}
    by_source: dict[str, int] = {}
    unresolved = 0
    for r in batch:
        row = await _classify_one(r, sec, yahoo, adapter, ctx.now, state)
        rows.append(row)
        src = row.get("sector_source") or "NONE"
        by_source[src] = by_source.get(src, 0) + 1
        if not row.get("sector"):
            unresolved += 1
    if state["yahoo_misses"] >= YAHOO_MISS_LIMIT:
        warnings.append("yahoo sector lookups kept failing; skipped for the rest of this run")
    if unresolved:
        warnings.append(f"{unresolved} symbols have no sector yet (see manual-review queue)")
    written = upsert_uniform(_guard(ctx), "security_master", rows, "ticker")
    return JobResult(
        job="classify_target_sectors",
        rows_read=len(batch),
        rows_written=written,
        message=(
            f"{len(batch)} checked ({by_source}), "
            f"{max(len(todo) - len(batch), 0)} still waiting for the next run"
        ),
        warnings=warnings[:20],
    )


def _ts(v: Any) -> float:
    if isinstance(v, datetime):
        return v.timestamp()
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


YAHOO_MISS_LIMIT = 8  # stop calling Yahoo for sector after this many consecutive empty answers


async def _classify_one(r, sec, yahoo, adapter, now: datetime, state: dict) -> dict[str, Any]:
    tk = r["ticker"]
    row: dict[str, Any] = {"ticker": tk, "profile_checked_at": now}
    # 1) SEC SIC code
    if sec is not None and r.get("cik"):
        try:
            info = await sec.sic_info(int(r["cik"]))
        except (ProviderError, ValueError):
            info = None
        if info is not None and info.sic:
            code = sector_from_sic(info.sic)
            row.update(
                sector=code, sector_raw=f"SIC {info.sic} {info.description or ''}".strip(),
                industry=info.description, sector_source="SEC_SIC", sector_classified_at=now,
                sector_data_status=DataStatus.AVAILABLE.value,
            )  # fmt: skip
            if info.sic == REIT_SIC:
                row["is_reit"] = True
            if info.sic == BLANK_CHECK_SIC:
                row["is_spac"] = True
            return row
    # 2) Yahoo label
    prof = None
    if yahoo is not None and state["yahoo_misses"] < YAHOO_MISS_LIMIT:
        try:
            prof = await yahoo.profile(tk)
        except ProviderError:
            prof = None
        state["yahoo_misses"] = 0 if (prof and prof.sector) else state["yahoo_misses"] + 1
    if prof is not None and prof.sector:
        code = normalize_sector(prof.sector)
        row.update(sector_raw=prof.sector, industry=prof.industry, sector_source="YAHOO_FINANCE",
                   sector_classified_at=now)  # fmt: skip
        if code is None:  # unrecognised label: never guess
            row.update(sector=None, sector_data_status=DataStatus.UNVERIFIED.value)
        else:
            row.update(sector=code, sector_data_status=DataStatus.AVAILABLE.value)
            if code == "REAL_ESTATE" and "reit" in (prof.industry or "").lower():
                row["is_reit"] = True
        if prof.market_cap:
            row["market_cap"] = prof.market_cap
        return row
    # 3) optional CSV (always UNVERIFIED)
    guess = adapter.lookup(tk) if adapter else None
    if guess is not None:
        code = normalize_sector(guess.sector_raw)
        row.update(
            sector=code if code in (*TARGET_SECTORS, OTHER_SECTOR) else None,
            sector_raw=guess.sector_raw, industry=guess.industry, sector_source=guess.source,
            sector_classified_at=now, sector_data_status=DataStatus.UNVERIFIED.value,
        )  # fmt: skip
        return row
    if not r.get("sector"):
        row["sector_data_status"] = DataStatus.MISSING.value
    return row


async def refresh_universe_market_data(ctx: JobContext) -> JobResult:
    yahoo = ctx.providers.require("yahoo")
    master = select_all(ctx.db, "security_master")
    todo = [
        r for r in master
        if r.get("is_active", True)
        and r.get("sector") in TARGET_SECTORS
        and r.get("security_type") in (SecurityType.COMMON_STOCK, SecurityType.ADR)
        and not r.get("is_test_issue")
    ]  # fmt: skip
    todo.sort(key=lambda r: str(r.get("liquidity_data_as_of") or ""))
    batch = todo[: ctx.settings.universe_market_data_batch]
    rows, warnings = [], []
    for r in batch:
        tk = r["ticker"]
        try:
            h = await yahoo.daily_history(tk, "1mo")
        except ProviderError as exc:
            warnings.append(f"{tk}: {exc}")
            continue
        bars = [b for b in h.bars if b.close and b.volume][-20:]
        if not bars:
            warnings.append(f"{tk}: no usable bars")
            continue
        adv_sh = sum(b.volume for b in bars) / len(bars)
        adv_usd = sum(b.close * b.volume for b in bars) / len(bars)
        rows.append(
            {
                "ticker": tk,
                "average_daily_volume_20d": round(adv_sh, 2),
                "average_dollar_volume_20d": round(adv_usd, 2),
                "last_price": h.last_price or bars[-1].close,
                "liquidity_data_as_of": bars[-1].bar_date,
                "market_data_source": "yahoo_finance",
            }
        )
    written = upsert_uniform(_guard(ctx), "security_master", rows, "ticker")
    return JobResult(
        job="refresh_universe_market_data",
        rows_read=len(batch),
        rows_written=written,
        message=f"{len(rows)} quoted, {max(len(todo) - len(batch), 0)} waiting for the next run",
        warnings=warnings[:20],
    )


async def build_target_sector_universe_views(ctx: JobContext) -> JobResult:
    p = UniverseParams.from_settings(ctx.settings)
    counts = rebuild_view_state(_guard(ctx), p, ctx.today, ctx.now)
    return JobResult(
        job="build_target_sector_universe_views",
        rows_read=counts["rows"],
        rows_written=counts["rows"],
        message=(
            f"all_target_sector_listings={counts['all']} "
            f"pair_research_eligible_universe={counts['pair_research']} "
            f"manual_review_universe={counts['manual_review']}"
        ),
    )
