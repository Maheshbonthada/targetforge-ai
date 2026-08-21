"""In-process sliding-window rate limiter, applied to `POST /runs`.

Every run costs real money (up to 7 LLM-calling agents x up to 25 genes,
against live OpenAI/Azure OpenAI) — the one endpoint in this API where "a
bug in some future caller retries in a tight loop" or "a compromised token"
translates directly into an uncapped bill, not just extra load. This is a
first line of defense, not the final word.

Explicitly in-process, not Redis-backed: correct for the single-replica
deployment this stack runs today (`container-app-api.bicep`'s
`minReplicas: 1` in prod — see that module's own comment on why). The moment
this API runs more than one replica, an in-process limiter under-counts (each
replica enforces its own independent window), and this needs to move to a
shared store (Redis, or Azure API Management's built-in rate limiting policy,
which would also move this concern out of application code entirely — the
more likely real fix). Tracked here rather than silently assumed to already
be "solved" by this module.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._calls_by_key: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Not `async` — the critical section is pure in-memory list
        arithmetic with no I/O, so a plain `threading.Lock` held for
        microseconds is simpler and cheaper than an `asyncio.Lock` here,
        and (unlike an asyncio.Lock) also stays correct if this is ever
        called from a sync context.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            calls = self._calls_by_key[key]
            calls[:] = [t for t in calls if t > cutoff]
            if len(calls) >= self._max_calls:
                return False
            calls.append(now)
            return True
