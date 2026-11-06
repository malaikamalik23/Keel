"""A small in-memory sliding-window rate limiter.

Why it exists: /api/chat spends money (every call is one or more paid LLM requests). The limit protects your API
bill from a runaway script or a leaked API key, not from a determined attacker -- single process, single user.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float | None:
        """Record a hit. Returns None if allowed, else the number of seconds until a slot frees up."""
        if self.per_minute <= 0:
            return None
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return max(60 - (now - q[0]), 0.1)
        q.append(now)
        if len(self._hits) > 1000:  # don't grow forever
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= 60]:
                del self._hits[k]
        return None
