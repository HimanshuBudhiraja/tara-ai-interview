"""Persistence for role-play sessions.

The sibling of `sessions.py`, and separate for the same reason `RoleplayState`
is separate from `SessionState`: almost none of the interview session's fields
mean anything here. Sharing one store would mean one loader branching on a type
tag, and a loader that can return two shapes is one every caller has to guard.

Same atomic write, same reason. A crash partway through would otherwise leave a
torn session, and someone mid-scenario would reopen their link to find nothing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from services import config
from services.orchestrator.roleplay import RoleplayState


def _dir() -> Path:
    """Resolved per call, not cached.

    `config.DATA_DIR` is monkeypatched per test to a temp directory. A path
    bound at import time would send the suite's writes into the real `data/`,
    which is both a corruption risk and a source of tests that pass on
    yesterday's rows.
    """
    d = config.DATA_DIR / "roleplay_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    return _dir() / f"{session_id}.json"


def exists(session_id: str) -> bool:
    return _path(session_id).exists()


def save(state: RoleplayState) -> None:
    state.updated_at = time.time()
    path = _path(state.session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def load(session_id: str) -> RoleplayState:
    return RoleplayState.from_dict(
        json.loads(_path(session_id).read_text(encoding="utf-8"))
    )


def try_load(session_id: str) -> RoleplayState | None:
    try:
        return load(session_id)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def delete(session_id: str) -> None:
    _path(session_id).unlink(missing_ok=True)


def attempts_for(subject_id: str, scenario_id: str) -> int:
    """How many times this person has already sat this scenario.

    Counted from disk rather than held on a counter, because the counter and
    the sessions would be two sources of truth for the same fact, and the
    first time they disagreed someone would get an attempt they were not owed
    — or be refused one they were.
    """
    n = 0
    for path in _dir().glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if raw.get("subject_id") == subject_id and raw.get("scenario_id") == scenario_id:
            n += 1
    return n
