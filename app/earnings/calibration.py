"""Logs every prediction and later scores it against what the stock actually did.

This is the only honest way to learn whether the probabilities mean anything: run the scan weekly,
then `python -m scripts.earnings_scan --score` after reports have happened.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.earnings.models import Scan
from app.earnings.service import OUT_DIR
from app.earnings.sources import reaction_for

LOG = OUT_DIR / "predictions.jsonl"


def log_predictions(scan: Scan, path: Path = LOG) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in scan.reports:
            fh.write(
                json.dumps(
                    {
                        "logged_at": scan.generated_at.isoformat(),
                        "model": scan.model_version,
                        "mock": scan.mock,
                        "ticker": r.ticker,
                        "report_date": r.report_date.isoformat(),
                        "session": r.session,
                        "p_up": r.p_up,
                        "confidence": r.confidence,
                        "expected_move": r.expected_move,
                    }
                )
                + "\n"
            )
    return len(scan.reports)


def read_log(path: Path = LOG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def latest_per_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the newest prediction made before the report for each (ticker, report date); mock
    predictions are never scored."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r.get("mock") or r["logged_at"][:10] > r["report_date"]:
            continue
        k = (r["ticker"], r["report_date"])
        if k not in best or r["logged_at"] > best[k]["logged_at"]:
            best[k] = r
    return list(best.values())


def evaluate(scored: list[dict[str, Any]], base_p_up: float = 0.52) -> dict[str, Any]:
    """scored rows need p_up and outcome_up (bool). Brier: lower is better; 0.25 is a coin flip."""
    n = len(scored)
    if not n:
        return {"n": 0}
    brier = sum((r["p_up"] - r["outcome_up"]) ** 2 for r in scored) / n
    base = sum((base_p_up - r["outcome_up"]) ** 2 for r in scored) / n
    hits = sum((r["p_up"] > 0.5) == bool(r["outcome_up"]) for r in scored)
    bins = []
    for lo, hi in ((0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 1.01)):
        grp = [r for r in scored if lo <= r["p_up"] < hi]
        if grp:
            bins.append(
                {
                    "range": f"{lo:.2f}-{min(hi, 1):.2f}",
                    "n": len(grp),
                    "mean_p": round(sum(r["p_up"] for r in grp) / len(grp), 3),
                    "actual_up": round(sum(r["outcome_up"] for r in grp) / len(grp), 3),
                }
            )
    return {
        "n": n,
        "hit_rate": round(hits / n, 3),
        "brier": round(brier, 4),
        "brier_always_prior": round(base, 4),
        "beats_prior": brier < base,
        "up_rate": round(sum(r["outcome_up"] for r in scored) / n, 3),
        "bins": bins,
        "caution": "fewer than 30 scored reports: treat these numbers as noise" if n < 30 else "",
    }


async def score_log(
    yahoo: Any, today: date, path: Path = LOG, base_p_up: float = 0.52
) -> dict[str, Any]:
    scored = []
    for r in latest_per_report(read_log(path)):
        rd = date.fromisoformat(r["report_date"])
        if rd > today - timedelta(days=3):
            continue  # the reaction may not be in the price data yet
        try:
            h = await yahoo.daily_history(r["ticker"], "2y")
        except Exception:
            continue
        dates, closes = [b.bar_date for b in h.bars], [b.close for b in h.bars]
        move = reaction_for(rd, r["session"] if r["session"] != "unknown" else "", dates, closes)
        if move is not None:
            scored.append({**r, "outcome_up": move > 0, "move": move})
    return {
        **evaluate(scored, base_p_up),
        "pending": len(latest_per_report(read_log(path))) - len(scored),
    }
