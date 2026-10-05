"""Monte Carlo of the first post-report move for one company.

It turns the model's P(up) and an expected move size into a return DISTRIBUTION (tails, expected
value, odds of a big loss). It does not add independent evidence about direction: the direction
odds are the model's own, with their uncertainty spread by a Beta draw per path (less confidence =
wider spread). Move size is Student-t (4 degrees of freedom) to keep the fat tails real earnings
moves have. For t(4), E|T| = 1, so the draw is scaled directly by the expected absolute move.
"""

import zlib
from datetime import date

import numpy as np

from app.earnings.models import McResult

DF = 4
RET_FLOOR, RET_CAP = -0.60, 1.00


def seed_for(ticker: str, report_date: date) -> int:
    return zlib.crc32(f"{ticker}|{report_date.isoformat()}".encode())


def simulate(
    p_up: float,
    expected_move: float,
    confidence: float,
    n_paths: int,
    seed: int,
    up_ratio: float | None = None,
    down_ratio: float | None = None,
    bins: int = 41,
) -> McResult:
    rng = np.random.default_rng(seed)
    k = 15 + 85 * max(0.0, min(1.0, confidence))  # Beta concentration: more confidence, tighter
    p_i = rng.beta(p_up * k, (1 - p_up) * k, size=n_paths)
    up = rng.random(n_paths) < p_i
    mag = np.abs(rng.standard_t(DF, size=n_paths)) * expected_move
    ret = np.where(up, mag * (up_ratio or 1.0), -mag * (down_ratio or 1.0))
    ret = np.clip(ret, RET_FLOOR, RET_CAP)
    q = np.percentile(ret, [5, 25, 50, 75, 95])
    worst = np.sort(ret)[: max(1, n_paths // 20)]
    lo, hi = np.percentile(ret, [1, 99])
    counts, edges = np.histogram(ret, bins=bins, range=(float(lo), float(hi)))
    mean = float(ret.mean())
    return McResult(
        n_paths=n_paths,
        p_up=round(float((ret > 0).mean()), 4),
        p_gain_5=round(float((ret > 0.05).mean()), 4),
        p_loss_5=round(float((ret < -0.05).mean()), 4),
        p_loss_10=round(float((ret < -0.10).mean()), 4),
        mean=round(mean, 4),
        median=round(float(q[2]), 4),
        p05=round(float(q[0]), 4),
        p25=round(float(q[1]), 4),
        p75=round(float(q[3]), 4),
        p95=round(float(q[4]), 4),
        cvar5=round(float(worst.mean()), 4),
        expected_move=round(expected_move, 4),
        edge=round(mean / expected_move, 4) if expected_move else 0.0,
        hist_edges=[round(float(e), 4) for e in edges],
        hist_counts=[int(c) for c in counts],
    )
