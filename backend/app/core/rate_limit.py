"""
Minimal in-memory, per-IP sliding-window rate limiter. Deliberately simple:
good enough to stop a naive script from hammering the (compute-expensive)
processing endpoint from a single machine, not a substitute for a real
gateway-level limiter (e.g. Redis-backed, or your cloud provider's WAF) in
front of a multi-instance deployment -- this state doesn't survive a
restart and isn't shared across processes.
"""
import time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)


def allow(key: str, limit: int, window_seconds: int = 60) -> bool:
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True
