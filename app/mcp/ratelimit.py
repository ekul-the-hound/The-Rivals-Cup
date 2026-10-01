"""Small in-process sliding-window limiter (single instance). Use an edge/WAF limit for multi-instance."""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: dict[str, deque[tuple[float, int]]] = defaultdict(deque)

    def check(self, key: str, cost: int = 1) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0][0] >= self.window:
            q.popleft()
        used = sum(c for _, c in q)
        if used + cost > self.limit:
            retry = int(self.window - (now - q[0][0])) + 1 if q else 1
            return False, max(retry, 1)
        q.append((now, cost))
        if len(self._hits) > 5000:  # bound memory
            for k in [k for k, v in self._hits.items() if not v][:1000]:
                del self._hits[k]
        return True, 0
