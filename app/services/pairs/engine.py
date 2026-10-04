"""PeerPairEngine: deterministic hard-eligibility checks + 0-100 pair-quality score.

Pure computation over PairInputs. No I/O, no randomness, no clock reads (uses inputs.now).
Direction rule (momentum continuation): the leg with the higher blended relative strength
(0.6 x 20d + 0.4 x 60d return) is the LONG candidate; the other is the SHORT candidate.
The counter-thesis in every packet argues mean reversion.
"""

import math
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.models.enums import DataStatus
from app.services.features.metrics import (
    adv_dollar,
    align_to_common_end,
    beta,
    close_series,
    correlation,
    daily_vol,
    estimate_liquidity_cap,
    max_abs_daily_move,
    relative,
    spread_vol,
    trailing_returns,
)  # fmt: skip
from app.services.pairs.blackout import evaluate_leg, evaluate_pair
from app.services.pairs.factors import CROWDED_CLUSTERS, factor_profile
from app.services.pairs.models import (
    EntryZone,
    LegInputs,
    PairEvaluation,
    PairInputs,
    PairPacket,
    Reason,
)  # fmt: skip
from app.services.pairs.params import WEIGHTS, EngineParams
from app.services.validation.freshness import price_status

DIRECTION_RULE = (
    "LONG = leg with higher blended relative strength (0.6*20d + 0.4*60d); SHORT = the other leg."
)
ADVERSE_CATALYST_PENALTY = {
    "M_AND_A": 6, "REGULATORY": 4, "OFFERING": 3, "MANAGEMENT_CHANGE": 3,
    "MATERIAL_AGREEMENT": 2, "BUYBACK": 1, "INSIDER": 1, "OTHER": 1,
}  # fmt: skip
GOOD_EVIDENCE = {"PRIMARY", "SECONDARY"}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _f(v) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1%}"


def rs_divergence_score(spread20: float | None) -> float:
    if spread20 is None or spread20 <= 0:
        return 0.0
    if spread20 <= 0.06:
        return spread20 / 0.06
    if spread20 <= 0.15:
        return 1.0
    if spread20 <= 0.30:
        return 1.0 - (spread20 - 0.15) / 0.15 * 0.6
    return 0.4  # extreme divergence often means an event, not a drift


class PeerPairEngine:
    def __init__(self, params: EngineParams | None = None) -> None:
        self.p = params or EngineParams()

    # ------------------------------------------------------------------ public
    def evaluate_all(self, inputs: list[PairInputs]) -> list[PairEvaluation]:
        evals = [self.evaluate(i) for i in inputs]
        return sorted(evals, key=lambda e: (not e.eligible, -e.score, e.name))

    def evaluate(self, inp: PairInputs) -> PairEvaluation:
        p = self.p
        reasons: list[Reason] = []
        flags: list[str] = []
        missing: list[str] = []
        pair = inp.pair
        name = pair.get("name", "?")

        # ---- relationship mapping (hard) ----
        q = pair.get("relationship_quality")
        if len(inp.legs) != 2:
            reasons.append(
                Reason(
                    code="RELATIONSHIP_NOT_MAPPED", message="pair must have exactly two mapped legs"
                )
            )
        if not (pair.get("relationship_explanation") or "").strip() or q is None:
            reasons.append(
                Reason(
                    code="RELATIONSHIP_NOT_MAPPED",
                    message="no recorded peer relationship/explanation",
                )
            )
        elif q < p.min_relationship_quality:
            reasons.append(
                Reason(
                    code="RELATIONSHIP_WEAK",
                    message=f"relationship quality {q}/5 below minimum {p.min_relationship_quality}",
                )
            )
        if len(inp.legs) != 2:
            return self._ineligible_stub(inp, reasons)

        a, b = inp.legs
        series = {id(leg): close_series(leg.bars) for leg in inp.legs}
        series[id(a)], series[id(b)] = align_to_common_end(series[id(a)], series[id(b)])
        r_a, r_b = trailing_returns(series[id(a)]) if len(series[id(a)]) else dict.fromkeys((1, 5, 20, 60)), trailing_returns(series[id(b)]) if len(series[id(b)]) else dict.fromkeys((1, 5, 20, 60))  # fmt: skip

        def blend(r):
            if r.get(20) is None:
                return None
            return 0.6 * r[20] + 0.4 * r[60] if r.get(60) is not None else r[20]

        ba, bb = blend(r_a), blend(r_b)
        if ba is not None and bb is not None and bb > ba:
            long_leg, short_leg = b, a
        else:
            long_leg, short_leg = a, b
        L, S = long_leg.security, short_leg.security
        cl, cs = series[id(long_leg)], series[id(short_leg)]
        rl, rs = (r_a, r_b) if long_leg is a else (r_b, r_a)
        spread = relative(rl, rs)

        # ---- security-level hard checks ----
        for leg in (long_leg, short_leg):
            s = leg.security
            tk = s["ticker"]
            if (s.get("security_type") or "EQUITY") != "EQUITY" or s.get("is_etf") or not s.get("is_active", True) \
                    or (s.get("country") or "US") != "US" or (s.get("exchange") or "") not in p.us_exchanges:  # fmt: skip
                reasons.append(
                    Reason(
                        code="NOT_US_LISTED_EQUITY",
                        message=f"{tk}: not an active U.S.-listed equity (type={s.get('security_type')}, exchange={s.get('exchange')}, country={s.get('country')})",
                    )
                )
            if s.get("is_etf") and float(s.get("leverage_factor") or 1) > p.max_leverage:
                reasons.append(
                    Reason(
                        code="LEVERAGED_ETF",
                        message=f"{tk}: ETF leverage {s.get('leverage_factor')}x exceeds {p.max_leverage}x",
                    )
                )
            if s.get("unresolved_corporate_action"):
                reasons.append(
                    Reason(
                        code="CORPORATE_ACTION",
                        message=f"{tk}: unresolved corporate action flagged ({s.get('corporate_action_notes') or 'no notes'})",
                    )
                )
            if (
                s.get("wsr_eligibility") != DataStatus.AVAILABLE.value
                and (s.get("wsr_eligibility") or "UNVERIFIED") != "AVAILABLE"
            ):
                flags.append(f"WSR_ELIGIBILITY_UNVERIFIED_{tk}")

        # ---- price history / staleness / corporate-action heuristic (hard) ----
        for leg, ser in ((long_leg, cl), (short_leg, cs)):
            tk = leg.security["ticker"]
            if len(ser) < p.min_bars or (ser <= 0).any():
                reasons.append(
                    Reason(
                        code="INSUFFICIENT_HISTORY",
                        message=f"{tk}: {len(ser)} valid daily bars, need {p.min_bars}",
                    )
                )
                continue
            last = date.fromisoformat(str(ser.index[-1])[:10])
            st = price_status(last, inp.today)
            if st != DataStatus.AVAILABLE:
                reasons.append(
                    Reason(code="STALE_PRICES", message=f"{tk}: latest bar {last} ({st.value})")
                )
            mv = max_abs_daily_move(ser)
            if mv is not None and mv > p.max_single_day_move:
                reasons.append(
                    Reason(
                        code="CORPORATE_ACTION",
                        message=f"{tk}: {mv:.0%} one-day move in adjusted prices; possible unresolved corporate action/data error",
                    )
                )

        # ---- liquidity (hard) ----
        caps: dict[str, float | None] = {}
        advs: dict[str, float | None] = {}
        for leg in (long_leg, short_leg):
            tk = leg.security["ticker"]
            advs[tk] = adv_dollar(leg.bars)
            caps[tk] = estimate_liquidity_cap(advs[tk], p.adv_cap_pct)
            if caps[tk] is None:
                reasons.append(
                    Reason(
                        code="LIQUIDITY_UNKNOWN", message=f"{tk}: cannot estimate 20d dollar volume"
                    )
                )
            elif caps[tk] < p.intended_leg_notional_usd:
                reasons.append(
                    Reason(
                        code="INSUFFICIENT_LIQUIDITY",
                        message=f"{tk}: est. cap ${caps[tk]:,.0f} (1% of 20d ADV) below intended ${p.intended_leg_notional_usd:,.0f}",
                    )
                )

        # ---- correlation (hard) ----
        corr, n_corr = correlation(cl, cs, 60)
        if corr is None or n_corr < p.min_corr_obs:
            reasons.append(
                Reason(
                    code="CORRELATION_UNKNOWN",
                    message=f"only {n_corr} overlapping returns; need {p.min_corr_obs}",
                )
            )
        elif corr < p.min_correlation:
            reasons.append(
                Reason(
                    code="LOW_CORRELATION",
                    message=f"60d correlation {corr:.2f} below {p.min_correlation:.2f}",
                )
            )

        # ---- event blackout (hard unless logged override) ----
        legs_bo = [evaluate_leg(leg.security["ticker"], inp.blackout_rows.get(leg.security["id"]), inp.week_start, inp.week_end, inp.now) for leg in (long_leg, short_leg)]  # fmt: skip
        elig = evaluate_pair(legs_bo, inp.override)
        if not elig.eligible:
            reasons.append(Reason(code="EVENT_BLACKOUT", message="; ".join(elig.blocked_reasons)))
        unverified = [lb.ticker for lb in legs_bo if any("never manually verified" in w or "older than" in w or "no blackout row" in w for w in lb.warnings)]  # fmt: skip
        if unverified and p.require_verified_blackout:
            reasons.append(
                Reason(
                    code="BLACKOUT_UNVERIFIED",
                    message=f"blackout not verified for {', '.join(unverified)}",
                )
            )
        for t in unverified:
            flags.append(f"UNVERIFIED_BLACKOUT_{t}")
        if elig.override_reason:
            flags.append("BLACKOUT_OVERRIDDEN")

        # ---- catalyst evidence (hard only when thesis_basis == CATALYST) ----
        cats = {
            leg.security["id"]: inp.catalysts.get(leg.security["id"], [])
            for leg in (long_leg, short_leg)
        }
        all_cats = [c for v in cats.values() for c in v]
        good_cats = [c for c in all_cats if c.get("evidence_quality") in GOOD_EVIDENCE]
        weak_cats = [c for c in all_cats if c.get("evidence_quality") not in GOOD_EVIDENCE]
        news = {
            leg.security["id"]: inp.news.get(leg.security["id"], [])
            for leg in (long_leg, short_leg)
        }
        good_news = [
            n for v in news.values() for n in v if n.get("evidence_quality") in GOOD_EVIDENCE
        ]
        weak_news = [
            n for v in news.values() for n in v if n.get("evidence_quality") not in GOOD_EVIDENCE
        ]
        if pair.get("thesis_basis") == "CATALYST" and not good_cats:
            reasons.append(
                Reason(
                    code="NO_QUALITY_CATALYST_EVIDENCE",
                    message="catalyst-based thesis lacks PRIMARY/SECONDARY evidence",
                )
            )
        if weak_cats or weak_news:
            flags.append("LOW_QUALITY_EVIDENCE_IGNORED")

        # ---- manual exclusion ----
        if inp.manual_decision == "EXCLUDE":
            reasons.append(
                Reason(code="MANUALLY_EXCLUDED", message="excluded by owner for this week")
            )

        # ================= metrics =================
        etf = close_series(inp.sector_etf_bars)
        spy = close_series(inp.spy_bars)
        r_etf = trailing_returns(etf) if len(etf) else {}
        ex_l = (
            (rl.get(20) - r_etf[20])
            if rl.get(20) is not None and r_etf.get(20) is not None
            else None
        )
        ex_s = (
            (rs.get(20) - r_etf[20])
            if rs.get(20) is not None and r_etf.get(20) is not None
            else None
        )
        vol_l, vol_s = daily_vol(cl), daily_vol(cs)
        beta_l = beta(cl, spy) if len(spy) else None
        beta_s = beta(cs, spy) if len(spy) else None
        beta_gap = abs(beta_l - beta_s) if beta_l is not None and beta_s is not None else None
        sv = spread_vol(cl, cs)
        sv10 = sv * math.sqrt(10) if sv else None
        etf_beta_l = beta(cl, etf) if len(etf) else None
        etf_beta_s = beta(cs, etf) if len(etf) else None

        # ---- data quality score (0-100) ----
        dq, dq_items = 0.0, []

        def award(pts: float, ok: bool, label: str):
            nonlocal dq
            if ok:
                dq += pts
            else:
                dq_items.append(label)

        fresh = all(len(s) and price_status(date.fromisoformat(str(s.index[-1])[:10]), inp.today) == DataStatus.AVAILABLE for s in (cl, cs))  # fmt: skip
        award(25, fresh, "price data missing/stale for a leg")
        award(15, len(cl) >= 61 and len(cs) >= 61, "fewer than 61 bars (60d metrics unavailable)")
        award(15, all(v is not None for v in advs.values()), "liquidity estimate missing for a leg")
        etf_fresh = (
            len(etf) >= 21
            and price_status(date.fromisoformat(str(etf.index[-1])[:10]), inp.today)
            == DataStatus.AVAILABLE
        )
        award(10, etf_fresh, f"sector ETF {inp.sector_etf or '(none mapped)'} prices missing/stale")
        award(10, beta_gap is not None, "beta vs SPY unavailable")
        award(15, not unverified and not any("no blackout row" in w for lb in legs_bo for w in lb.warnings), "event blackout not manually verified")  # fmt: skip
        award(
            5,
            all(leg.company.get("market_cap_usd") for leg in (long_leg, short_leg)),
            "market cap unavailable",
        )
        award(
            5,
            all(leg.company.get("wiki_summary") for leg in (long_leg, short_leg)),
            "company context unavailable",
        )
        missing += dq_items

        # ================= components (raw 0..1) =================
        same_sector = bool(L.get("sector")) and L.get("sector") == S.get("sector")
        raw: dict[str, float] = {}
        raw["relationship_quality"] = clamp((((q or 1) - 1) / 4) * 0.7 + 0.15 * same_sector + 0.15 * bool((pair.get("business_driver") or "").strip()))  # fmt: skip
        raw["relative_strength_divergence"] = rs_divergence_score(spread.get(20))
        s20, s60, s5 = spread.get(20), spread.get(60), spread.get(5)
        if s20 is None or s60 is None:
            trend = 0.5
            flags.append("MISSING_60D_TREND")
        elif s20 > 0 and s60 > 0:
            trend = 1.0
        elif s20 > 0 or s60 > 0:
            trend = 0.25
        else:
            trend = 0.0
        if s5 is not None and s5 < -0.02 and trend > 0:
            trend *= 0.7
        raw["trend_consistency"] = trend
        if ex_l is None or ex_s is None:
            raw["sector_relative_divergence"] = 0.5
        else:
            raw["sector_relative_divergence"] = (
                clamp(ex_l / 0.04, -1, 1) - clamp(ex_s / 0.04, -1, 1) + 2
            ) / 4
        known_caps = [c for c in caps.values() if c is not None]
        min_cap = min(known_caps) if len(known_caps) == 2 else None
        raw["liquidity"] = (
            clamp(((min_cap / p.intended_leg_notional_usd) - 1) / 4) if min_cap else 0.0
        )
        corr_score = clamp(((corr or 0) - p.min_correlation) / 0.35) if corr is not None else 0.0
        beta_score = 0.5 if beta_gap is None else clamp(1 - (beta_gap - 0.2) / 0.6)
        raw["correlation_beta_compatibility"] = 0.6 * corr_score + 0.4 * beta_score
        if len(good_cats) + len(good_news) >= 3:
            ev = 1.0
        elif good_cats or good_news:
            ev = 0.8
        elif weak_cats or weak_news:
            ev = 0.3
        else:
            ev = 0.5
        raw["catalyst_evidence_quality"] = ev
        raw["data_completeness"] = dq / 100

        components = {k: {"weight": WEIGHTS[k], "raw": round(v, 4), "points": round(v * WEIGHTS[k], 2)} for k, v in raw.items()}  # fmt: skip

        # ---- penalties ----
        event_pen = 0.0
        event_pen += 4 * len(unverified) if len(unverified) else 0
        event_pen = min(event_pen, 8.0)
        if elig.override_reason:
            event_pen += 8
        recent_adverse = []
        cutoff = (inp.today - timedelta(days=14)).isoformat()
        for c in good_cats:
            if str(c.get("event_date") or "")[:10] >= cutoff:
                recent_adverse.append(c["catalyst_type"])
        event_pen += min(sum(ADVERSE_CATALYST_PENALTY.get(t, 1) for t in set(recent_adverse)), 8)
        event_pen = min(event_pen, 20.0)
        for t in sorted(set(recent_adverse)):
            flags.append(f"RECENT_{t}_DISCLOSURE")
        fp = factor_profile(
            L.get("sector") or S.get("sector") or pair.get("sector"), pair.get("macro_driver")
        )
        conc_pen = 0.0
        vr = (
            (max(vol_l, vol_s) / min(vol_l, vol_s))
            if vol_l and vol_s and min(vol_l, vol_s) > 0
            else None
        )
        if vr is not None and vr > 1.3:
            conc_pen += clamp((vr - 1.3) / 1.0) * 6
            flags.append("VOL_MISMATCH")
        if fp.cluster in CROWDED_CLUSTERS:
            conc_pen += 2
        for lbl, v in (("HIGH_VOL", vol_l), ("HIGH_VOL", vol_s)):
            if v and v > 0.03:
                flags.append(lbl)
        if beta_gap is not None and beta_gap > 0.3:
            flags.append("BETA_GAP")
        penalties = {
            "event_risk_penalty": round(event_pen, 2),
            "concentration_factor_penalty": round(conc_pen, 2),
        }
        score = round(
            clamp(sum(c["points"] for c in components.values()) - event_pen - conc_pen, 0, 100), 1
        )

        # ================= packet =================
        tl, ts_ = L["ticker"], S["ticker"]
        liq = {"est_cap_usd": caps, "adv_20d_usd": {k: round(v) if v else None for k, v in advs.items()}, "intended_leg_notional_usd": p.intended_leg_notional_usd, "assumption": f"cap = {p.adv_cap_pct:.0%} of 20d dollar volume; WSR's actual limits unverified"}  # fmt: skip
        metrics = {
            "returns": {tl: rl, ts_: rs}, "spread_long_minus_short": spread, "sector_excess_20d": {tl: ex_l, ts_: ex_s},
            "daily_vol_20d": {tl: vol_l, ts_: vol_s}, "correlation_60d": corr, "correlation_obs": n_corr,
            "beta_vs_spy_60d": {tl: beta_l, ts_: beta_s}, "beta_gap": beta_gap,
            "beta_vs_sector_etf_60d": {tl: etf_beta_l, ts_: etf_beta_s}, "spread_vol_10d": sv10, "liquidity": liq,
        }  # fmt: skip
        reasons_txt = self._top_reasons(components, locals())
        counter = self._counter_thesis(tl, ts_, spread, beta_gap, recent_adverse, fp, vr)
        zones = [self._zone(long_leg, "LONG", vol_l), self._zone(short_leg, "SHORT", vol_s)]
        inv_pct = clamp(2 * sv10, 0.04, 0.15) if sv10 else 0.06
        tgt_pct = round(inv_pct * 1.5, 4)
        invalidation = (
            f"Concept: reassess if the {tl}-minus-{ts_} relative return falls ~{inv_pct:.1%} against the entry spread "
            f"(~2 sigma of 10-day spread vol), 60d correlation drops below {p.min_correlation:.2f}, or either leg posts a new "
            "earnings/major-event date or adverse 8-K. Not a stop order."
        )
        target = f"Concept: review a favorable spread move of ~{tgt_pct:.1%} (1.5x the invalidation distance), or 4 weeks, whichever first. Not a limit order."  # fmt: skip
        cadence = ("Every 2-3 trading days (volatile spread or fresh disclosure); immediate review on any new filing or event date."
                   if (sv10 and sv10 > 0.07) or recent_adverse else
                   "Weekly light check (Friday close or Sunday); immediate review on any new 8-K, news, or event-date change.")  # fmt: skip
        e_status = (
            "CLEAR"
            if elig.eligible and not elig.blocked_reasons
            else ("OVERRIDDEN" if elig.eligible else "BLACKOUT")
        )
        blackout = {"week": f"{inp.week_start} to {inp.week_end}", "status": e_status, "blocked_reasons": elig.blocked_reasons, "override_reason": elig.override_reason, "warnings": elig.warnings}  # fmt: skip
        cat_ctx = self._catalyst_ctx(inp, long_leg, short_leg, cats)
        news_ctx = self._news_ctx(news, long_leg, short_leg)
        macro_ctx = {"macro": {k: (inp.macro or {}).get(k) for k in ("as_of_date", "dgs2", "dgs10", "yield_curve_10y2y", "fed_funds", "vix", "regime")} if inp.macro else None, "market": {"spy_1d": (inp.market_ctx or {}).get("spy_return_1d"), "breadth": (inp.market_ctx or {}).get("breadth")} if inp.market_ctx else None, "sector_etf": inp.sector_etf, "sector_etf_returns": r_etf or None, "factor_cluster": fp.cluster, "macro_driver": fp.driver}  # fmt: skip
        if not inp.macro:
            missing.append("macro context missing")
        checklist = self._checklist(tl, ts_, unverified, flags, elig.override_reason, inp)
        packet = PairPacket(
            pair_id=pair.get("id"), name=name, long_ticker=tl, short_ticker=ts_, direction_rule=DIRECTION_RULE,
            relationship={"explanation": pair.get("relationship_explanation"), "quality_1_to_5": q, "business_driver": pair.get("business_driver"), "macro_driver": fp.driver, "sector": fp.sector, "same_sector": same_sector, "sector_etf": inp.sector_etf, "thesis_basis": pair.get("thesis_basis") or "RELATIVE_VALUE"},  # fmt: skip
            pair_quality_score=score, top_reasons=reasons_txt, counter_thesis=counter, event_blackout=blackout,
            entry_zones=zones, invalidation_concept=invalidation, target_concept=target, review_cadence=cadence,
            manual_checklist=checklist, risk_flags=sorted(set(flags)), metrics=metrics, catalyst_context=cat_ctx,
            news_summary=news_ctx, macro_sector_context=macro_ctx, data_quality_score=round(dq, 1),
            missing_or_stale=list(dict.fromkeys(missing)), manual_decision=inp.manual_decision,
        )  # fmt: skip
        return PairEvaluation(
            pair_id=pair.get("id"), name=name, long_ticker=tl, short_ticker=ts_, long_security_id=L.get("id"),
            short_security_id=S.get("id"), eligible=not reasons, ineligible_reasons=reasons, score=score,
            components=components, penalties=penalties, data_quality_score=round(dq, 1),
            factor={"sector": fp.sector, "cluster": fp.cluster, "driver": fp.driver}, packet=packet,
        )  # fmt: skip

    # ------------------------------------------------------------------ helpers
    def _ineligible_stub(self, inp: PairInputs, reasons: list[Reason]) -> PairEvaluation:
        pair = inp.pair
        fp = factor_profile(pair.get("sector"), pair.get("macro_driver"))
        pk = PairPacket(
            pair_id=pair.get("id"), name=pair.get("name", "?"), long_ticker=None, short_ticker=None,
            direction_rule=DIRECTION_RULE, relationship={"explanation": pair.get("relationship_explanation")},
            pair_quality_score=0, top_reasons=[], counter_thesis=[], event_blackout={}, entry_zones=[],
            invalidation_concept="n/a", target_concept="n/a", review_cadence="n/a", manual_checklist=[],
            risk_flags=["INELIGIBLE"], metrics={}, catalyst_context={}, news_summary={}, macro_sector_context={},
            data_quality_score=0, missing_or_stale=["pair mapping incomplete"],
        )  # fmt: skip
        return PairEvaluation(
            pair_id=pair.get("id"), name=pair.get("name", "?"), long_ticker=None, short_ticker=None, eligible=False,
            ineligible_reasons=reasons, score=0, components={}, penalties={}, data_quality_score=0,
            factor={"sector": fp.sector, "cluster": fp.cluster, "driver": fp.driver}, packet=pk,
        )  # fmt: skip

    def _top_reasons(self, comps: dict, loc: dict) -> list[str]:
        spread, corr, caps = loc["spread"], loc["corr"], loc["min_cap"]
        bgap = "n/a" if loc["beta_gap"] is None else format(loc["beta_gap"], ".2f")
        tl, ts_, q = loc["tl"], loc["ts_"], loc["q"]
        text = {
            "relationship_quality": f"Direct peer relationship rated {q}/5 ({loc['pair'].get('business_driver') or 'driver not set'})",
            "relative_strength_divergence": f"{tl} leads {ts_} by {_pct(spread.get(20))} over 20d ({_pct(spread.get(60))} over 60d)",
            "trend_consistency": f"20d and 60d spreads point the same way (5d spread {_pct(spread.get(5))})",
            "sector_relative_divergence": f"vs {loc['inp'].sector_etf or 'sector ETF'} (20d): {tl} {_pct(loc['ex_l'])}, {ts_} {_pct(loc['ex_s'])}",
            "liquidity": f"Estimated leg cap ${caps:,.0f} covers intended ${self.p.intended_leg_notional_usd:,.0f}" if caps else "Liquidity estimate unavailable",
            "correlation_beta_compatibility": f"60d return correlation {corr:.2f}; beta gap {bgap}" if corr is not None else "Correlation unavailable",
            "catalyst_evidence_quality": "Primary/secondary evidence available for recent company events",
            "data_completeness": f"Data quality score {loc['dq']:.0f}/100",
        }  # fmt: skip
        order = [k for k in comps if k not in ("data_completeness", "catalyst_evidence_quality")]
        ranked = sorted(order, key=lambda k: (-comps[k]["raw"], -comps[k]["weight"], k))
        picks = [k for k in ranked if comps[k]["raw"] >= 0.5][:3]
        if len(picks) < 3 and comps["catalyst_evidence_quality"]["raw"] >= 0.8:
            picks.append("catalyst_evidence_quality")
        if len(picks) < 3 and comps["data_completeness"]["raw"] >= 0.8:
            picks.append("data_completeness")
        return [text[k] for k in picks]

    def _counter_thesis(self, tl, ts_, spread, beta_gap, adverse, fp, vr) -> list[str]:
        out = [f"Momentum continuation is not guaranteed: {tl} already outperformed {ts_} by {_pct(spread.get(20))} over 20d, and the spread can mean-revert."]  # fmt: skip
        if (
            spread.get(20) is not None
            and spread.get(60) is not None
            and (spread[20] > 0) != (spread[60] > 0)
        ):
            out.append(
                "20d and 60d spreads disagree; the recent move may be noise rather than trend."
            )
        out.append(f"Both legs share the '{fp.driver}' driver; a shock there, or company-specific legal/regulatory news, can hurt the long and help the short at the same time.")  # fmt: skip
        if beta_gap is not None and beta_gap > 0.3:
            out.append(
                f"Not market-neutral: beta gap {beta_gap:.2f} means a broad rally or sell-off moves this pair."
            )
        if adverse:
            out.append(
                f"Recent company disclosures ({', '.join(sorted(set(adverse)))}) may not be priced in yet."
            )
        if vr is not None and vr > 1.3:
            out.append("Volatility mismatch: equal-dollar legs carry unequal risk.")
        return out

    def _zone(self, leg: LegInputs, side: str, vol: float | None) -> EntryZone:
        bars = leg.bars
        q = leg.quote or {}
        cur = _f(q.get("price")) or (_f(bars[-1].get("close")) if bars else None)
        prev = _f(q.get("previous_close")) or (_f(bars[-2].get("close")) if len(bars) > 1 else None)
        sig = vol if vol else 0.015
        lo = round(cur * (1 - 0.5 * sig), 2) if cur else None
        hi = round(cur * (1 + 0.5 * sig), 2) if cur else None
        return EntryZone(
            ticker=leg.security["ticker"], side=side, reference_price=cur, previous_close=prev, band_low=lo, band_high=hi,
            basis=f"current/last close +/- 0.5 x 20d daily vol ({sig:.1%}); a sanity-check band for manual limit prices, NOT an executable instruction",
        )  # fmt: skip

    def _catalyst_ctx(self, inp, long_leg, short_leg, cats) -> dict[str, Any]:
        out = {}
        for leg in (long_leg, short_leg):
            sid, tk = leg.security["id"], leg.security["ticker"]
            fl = inp.filings.get(sid, [])
            out[tk] = {
                "filings_30d": {
                    f: sum(1 for x in fl if x.get("form_type") == f)
                    for f in ("8-K", "10-Q", "10-K", "4")
                },
                "catalysts_14d": [
                    {
                        "type": c["catalyst_type"],
                        "headline": c["headline"],
                        "date": c.get("event_date"),
                        "evidence": c.get("evidence_quality"),
                    }
                    for c in cats.get(sid, [])
                ],  # fmt: skip
            }
        return out

    def _news_ctx(self, news, long_leg, short_leg) -> dict[str, Any]:
        out = {}
        for leg in (long_leg, short_leg):
            items = news.get(leg.security["id"], [])
            good = [n for n in items if n.get("evidence_quality") in GOOD_EVIDENCE]
            out[leg.security["ticker"]] = {
                "items_14d": len(items),
                "primary_or_secondary": len(good),
                "unverified_ignored_as_evidence": len(items) - len(good),
                "top": [
                    {
                        "headline": n["headline"],
                        "publisher": n.get("publisher_name"),
                        "quality": n.get("evidence_quality"),
                    }
                    for n in sorted(
                        items,
                        key=lambda n: (
                            n.get("evidence_quality") not in GOOD_EVIDENCE,
                            str(n.get("published_at")),
                        ),
                        reverse=False,
                    )[:3]
                ],  # fmt: skip
            }
        return out

    def _checklist(self, tl, ts_, unverified, flags, override, inp) -> list[str]:
        c = [
            f"Confirm {tl} and {ts_} are available/eligible in Trader View (roster, long/short permissions, any borrow or position limits).",
            f"Verify earnings and major-event dates for {tl} and {ts_} for {inp.week_start}..{inp.week_end} on each company's IR calendar; record them in the blackout table.",
            "Check each company's latest 8-K list on SEC EDGAR and headlines since Friday's close.",
            "Confirm no pending split, merger, spin-off or ticker change for either leg.",
            "Compare live prices with the reference band; skip the pair if either leg gapped well outside it.",
            "Check Trader View's real liquidity/position limits against the estimated cap (the 1%-of-ADV figure is only an estimate).",
            "Decide the dollar size per leg yourself; keep legs roughly equal and within the suggested gross ceiling.",
            "After you enter the trades manually, record them in manual_trades; this system never does.",
        ]
        if unverified:
            c.insert(
                2,
                f"BLACKOUT NOT VERIFIED for {', '.join(unverified)}: do this before anything else.",
            )
        if override:
            c.insert(
                0,
                f"Blackout override in effect (reason on file: '{override}'): re-read that reason now.",
            )
        if any(f.startswith("WSR_ELIGIBILITY_UNVERIFIED") for f in flags):
            c.append("WSR eligibility is marked UNVERIFIED in the database for at least one leg.")
        return c


def to_series(bars: list[dict[str, Any]]) -> pd.Series:
    return close_series(bars)
