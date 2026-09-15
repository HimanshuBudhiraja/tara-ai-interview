"""Read-modify-write on a whole-file JSON store, without losing anything.

Several small repositories here keep one JSON object per file — the interviews,
the invitations, the pilot runs. Each update reads the whole file, changes one
row and writes it back, which is fine until two of those happen at once. Then
one of them is silently lost: both read the same starting point and the second
write wins.

That is not theoretical for this build. The pilot runs three candidates at a
time, and every one of them updates its own invitation twice — once when the
session starts, once when the interview completes. Three concurrent interviews
sharing one `invites.json` is exactly the shape that drops a row.

Two guarantees, and only two:

  * **Serialised within the process.** `guarded(path)` is a re-entrant lock per
    path, so a read-modify-write cannot interleave with another one, and a
    function that takes the lock may call another that takes it again.
  * **Never torn.** `write_atomic` writes a temp file and renames it, so a
    crash mid-write leaves the previous contents rather than half a file. On
    `invites.json` — which is now megabytes — half a file would lose every
    invitation on the system.

What it deliberately does NOT do is coordinate across processes. The evaluation
lock in `services/evaluation/jobs.py` takes an `flock` because an evaluation is
expensive and duplicating one costs real money; these writes are cheap, and the
deployment this build supports is one process on one host (see
PILOT_READINESS.md §8 and PILOT_PROTOCOL.md). Pretending otherwise here would
be a guarantee nobody could rely on.
"""
from __future__ import annotations

import contextlib
import json
import threading
from pathlib import Path
from typing import Any, Iterator

_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _GUARD:
        # Re-entrant on purpose: `mark_opened` calls `update`, and a plain lock
        # would deadlock the moment one guarded function called another.
        return _LOCKS.setdefault(key, threading.RLock())


@contextlib.contextmanager
def guarded(path: Path) -> Iterator[None]:
    """Hold this file's lock for the whole read-modify-write."""
    lock = _lock_for(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def write_atomic(path: Path, payload: Any, *, indent: int = 2) -> None:
    """Serialise `payload` to `path` through a temp file and a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=indent, ensure_ascii=False), encoding="utf-8"
    )
    tmp.replace(path)
