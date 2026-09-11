"""The minimum abuse boundary, and an honest note about what it is not.

A fixed-window counter, in memory, per process. It exists for the handful of
operations an unauthenticated stranger can reach — logging in, validating an
invitation token, taking a turn — where the cost of an unbounded loop is real:
password guessing, token enumeration, and a candidate session driven by a script.

What it is **not**: a defence against a distributed attacker, or a substitute
for the rate limiting a reverse proxy or API gateway does properly. It is
in-process, so N app processes multiply every limit by N, and it forgets
everything on restart. Both are documented in PRODUCTION_SECURITY_MATRIX.md as
deployment dependencies rather than pretended away.

It fails OPEN by design. A limiter that throws on its own bookkeeping would turn
a bug in this file into an outage in the interview.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Limit:
    name: str
    requests: int
    window_sec: int

    @property
    def description(self) -> str:
        return f"{self.requests} per {self.window_sec}s"


#: Every limit in the product, in one table. Numbers chosen to be far above what
#: a person does and far below what a script does.
LIMITS = {
    # Ten passwords a minute is a typo-tolerant human and a useless attacker.
    "login": Limit("login", 10, 60),
    # A candidate opens their link, reloads, maybe shares a screen. Sixty a
    # minute is generous; six hundred is enumeration.
    "invitation": Limit("invitation", 60, 60),
    # One turn every couple of seconds is faster than speech.
    "turn": Limit("turn", 120, 60),
    # Evaluations cost money. A double-clicked button is two; twenty is a loop.
    "evaluation": Limit("evaluation", 20, 300),
    # The AI design and question-generation endpoints, which are the most
    # expensive thing an authenticated user can trigger.
    "generation": Limit("generation", 30, 300),
}

_HITS: dict[tuple[str, str], tuple[int, float]] = {}
_LOCK = threading.Lock()


def _client(request: Request) -> str:
    """Who to count against.

    The peer address, plus a forwarded-for hop when one is present — a
    deployment behind a proxy would otherwise count every client as the proxy.
    Only the first hop is used and it is never trusted for anything but
    bucketing.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def check(name: str, key: str) -> bool:
    """Count one request. False when the window is already full."""
    limit = LIMITS.get(name)
    if limit is None:
        return True
    now = time.time()
    bucket = (name, key)
    with _LOCK:
        count, started = _HITS.get(bucket, (0, now))
        if now - started >= limit.window_sec:
            count, started = 0, now
        count += 1
        _HITS[bucket] = (count, started)
        # Keep the table from growing without bound in a long-lived process.
        if len(_HITS) > 20_000:
            for stale, (_, when) in list(_HITS.items()):
                if now - when > 3600:
                    _HITS.pop(stale, None)
    return count <= limit.requests


def enforce(name: str, key: str) -> None:
    limit = LIMITS[name]
    if not check(name, key):
        raise HTTPException(
            429,
            "Too many requests. Try again in a moment.",
            headers={"Retry-After": str(limit.window_sec)},
        )


def limiter(name: str, *, by_path: str = ""):
    """A dependency that limits one route.

    `by_path` names a path parameter to include in the bucket, so a per-session
    limit cannot be exhausted for every candidate by hammering one session.
    """

    def dependency(request: Request) -> None:
        key = _client(request)
        if by_path:
            key = f"{key}:{request.path_params.get(by_path, '')}"
        enforce(name, key)

    # Named, because the access matrix reads dependency names to say what
    # guards each route, and "dependency" tells a reader nothing.
    dependency.__name__ = f"rate_limit[{name}]"
    dependency.__qualname__ = dependency.__name__
    return dependency


def reset() -> None:
    """Forget every counter. For tests, which must not inherit each other's."""
    with _LOCK:
        _HITS.clear()
