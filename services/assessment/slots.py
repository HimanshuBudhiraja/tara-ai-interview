"""Slot booking: participants pick a time, so live calls never exceed Retell's limit.

Retell caps how many calls run at once for the whole account (20 on the current
plan, read live from `GET /get-concurrency`). Without booking, a candidate link
shared with fifty people could put fifty calls on the line at 10:00 and most of
them would fail. So every participant books a slot first, and a slot only has as
many places as there are concurrent calls.

    time ──┬───────┬───────┬───────┬──▶
           │ 10:00 │ 10:30 │ 11:00 │      30-minute blocks
           │ 20    │ 20    │ 20    │      places per block = concurrency limit
           └───────┴───────┴───────┘
    a 20-min conversation (cap 25) booked at 10:00 holds one place in 10:00
    a 30-min conversation (cap 35) booked at 10:00 holds one in 10:00 AND 10:30

Bookings count across every agent, because the limit is account-wide. The call
endpoint enforces the booking too: a call starts only from 5 minutes before the
booked time to 15 minutes after, and never while Retell reports the limit
reached. The screen's countdown is a convenience; the server is the rule.
"""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from services import config

SLOT_MINUTES = int(os.environ.get("TARA_SLOT_MINUTES", "30"))
DAYS_AHEAD = int(os.environ.get("TARA_SLOT_DAYS", "7"))
HOURS = os.environ.get("TARA_SLOT_HOURS", "09:00-21:00")
TZ = os.environ.get("TARA_SLOT_TZ", "Asia/Kolkata")
JOIN_EARLY_MIN = int(os.environ.get("TARA_SLOT_JOIN_EARLY", "5"))
JOIN_LATE_MIN = int(os.environ.get("TARA_SLOT_JOIN_LATE", "15"))
#: Most conversations booked into one block: 18 by default (TARA_SLOT_CAPACITY),
#: leaving headroom under Retell's 20 lines for builder test calls. If Retell
#: reports a lower account limit, the lower number wins.
DEFAULT_CAPACITY = 18

#: One booking at a time, so two people can't both take the last place.
LOCK = threading.Lock()

_cap_cache: tuple[float, int] = (0.0, 0)


def capacity() -> int:
    """Places per block: TARA_SLOT_CAPACITY (default 18), never above Retell's
    concurrency limit (asked at most every 10 minutes)."""
    global _cap_cache
    raw = os.environ.get("TARA_SLOT_CAPACITY", "").strip()
    configured = max(1, int(raw)) if raw.isdigit() else DEFAULT_CAPACITY
    if not (time.time() - _cap_cache[0] < 600 and _cap_cache[1]):
        _cap_cache = (time.time(), retell_concurrency()[1])
    return min(configured, _cap_cache[1]) if _cap_cache[1] else configured


def retell_concurrency() -> tuple[int, int]:
    """(calls running now, limit) from Retell, or (0, 0) if it can't be asked."""
    if not config.RETELL_API_KEY:
        return 0, 0
    try:
        import httpx

        r = httpx.get("https://api.retellai.com/get-concurrency", timeout=8,
                      headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"})
        if r.status_code < 400:
            d = r.json()
            return int(d.get("current_concurrency") or 0), int(d.get("concurrency_limit") or 0)
    except Exception:  # noqa: BLE001 — no answer means "don't know", not "full"
        pass
    return 0, 0


def _block(dt: datetime) -> int:
    """Index of the 30-minute block a moment falls in (minutes since epoch / 30)."""
    return int(dt.timestamp() // 60) // SLOT_MINUTES


def blocks_for(start: datetime, cap_minutes: int) -> list[int]:
    """Every block a conversation booked at `start` can run into."""
    first = _block(start)
    return list(range(first, first + max(1, math.ceil(cap_minutes / SLOT_MINUTES))))


def parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def usage(bookings: list[dict[str, Any]], exclude: str = "") -> dict[int, int]:
    """Places taken per block, from every live booking except `exclude`'s."""
    used: dict[int, int] = {}
    for b in bookings:
        if b.get("session_id") == exclude or b.get("status") == "complete":
            continue
        for k in blocks_for(parse(b["start"]), int(b["cap_minutes"])):
            used[k] = used.get(k, 0) + 1
    return used


def offered(now: datetime | None = None) -> list[datetime]:
    """Every slot start offered: within the daily hours, from the next slot on."""
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(TZ)
    h0, h1 = HOURS.split("-")
    open_h, open_m = map(int, h0.split(":"))
    close_h, close_m = map(int, h1.split(":"))
    out: list[datetime] = []
    today = now.astimezone(tz).date()
    for d in range(DAYS_AHEAD + 1):
        day = today + timedelta(days=d)
        t = datetime(day.year, day.month, day.day, open_h, open_m, tzinfo=tz)
        end = datetime(day.year, day.month, day.day, close_h, close_m, tzinfo=tz)
        while t < end:
            # The slot already under way is still offered while it can be joined
            # (up to JOIN_LATE_MIN after it started), so someone can start now.
            if t + timedelta(minutes=JOIN_LATE_MIN) > now:
                out.append(t.astimezone(timezone.utc))
            t += timedelta(minutes=SLOT_MINUTES)
    return out


def available(bookings: list[dict[str, Any]], cap_minutes: int, session_id: str = "",
              now: datetime | None = None) -> list[dict[str, Any]]:
    """Every offered slot with how many places it has left for this conversation."""
    cap = capacity()
    used = usage(bookings, exclude=session_id)
    out = []
    for start in offered(now):
        left = min(cap - used.get(k, 0) for k in blocks_for(start, cap_minutes))
        out.append({"start": iso(start), "left": max(0, left)})
    return out


def window(booking: dict[str, Any]) -> tuple[datetime, datetime, datetime]:
    """(joining opens, joining closes, the booking's end)."""
    start = parse(booking["start"])
    return (start - timedelta(minutes=JOIN_EARLY_MIN), start + timedelta(minutes=JOIN_LATE_MIN),
            start + timedelta(minutes=int(booking["cap_minutes"]) + JOIN_LATE_MIN))


def can_join(booking: dict[str, Any] | None, resuming: bool = False, now: datetime | None = None) -> bool:
    """A first call only inside the joining window; a reconnect until the booking ends."""
    if not booking:
        return False
    now = now or datetime.now(timezone.utc)
    opens, closes, ends = window(booking)
    return opens <= now <= (ends if resuming else closes)


def now_option(bookings: list[dict[str, Any]], cap_minutes: int, session_id: str = "",
               now: datetime | None = None) -> dict[str, Any] | None:
    """'Start now', when there's room: the blocks from this minute have a free place
    and Retell's live calls are below the cap. None when it isn't offered."""
    now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    local = now.astimezone(ZoneInfo(TZ))
    h0, h1 = HOURS.split("-")
    if not (h0 <= local.strftime("%H:%M") < h1):   # outside the daily hours: no "now"
        return None
    cap = capacity()
    used = usage(bookings, exclude=session_id)
    left = min(cap - used.get(k, 0) for k in blocks_for(now, cap_minutes))
    running, _ = retell_concurrency()
    left = min(left, cap - running)
    return {"start": iso(now), "left": max(0, left), "now": True} if left > 0 else None
