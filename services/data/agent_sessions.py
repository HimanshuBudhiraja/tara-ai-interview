"""Participant sessions for published Agent Builder agents.

One row per person who signed in with an access code: who they are, which
published version they sat, the Retell calls placed for them, the transcript,
their feedback, and the score. The score is the recruiter's: the participant
API never returns it.

A row carries the SHA-256 of its session grant, never the grant itself. The
grant lives only in the participant's HttpOnly cookie.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from services import config


def _dir() -> Path:
    d = config.DATA_DIR / "agent_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "_-")
    return _dir() / f"{safe}.json"


def new_id() -> str:
    return "ps_" + secrets.token_hex(8)


def hash_grant(grant: str) -> str:
    return hashlib.sha256(grant.encode()).hexdigest()


def load(session_id: str) -> dict[str, Any] | None:
    p = _path(session_id)
    return json.loads(p.read_text()) if p.exists() else None


def save(row: dict[str, Any]) -> dict[str, Any]:
    row["updated_at"] = time.time()
    target = _path(row["session_id"])
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, indent=1))
    tmp.replace(target)
    return row


def for_agent(agent_id: str) -> list[dict[str, Any]]:
    rows = []
    for p in _dir().glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("agent_id") == agent_id:
            rows.append(r)
    return sorted(rows, key=lambda r: r.get("created_at", 0), reverse=True)


def all_bookings() -> list[dict[str, Any]]:
    """Every booked slot across all agents: Retell's call limit is account-wide."""
    out = []
    for p in _dir().glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("booking"):
            out.append({**r["booking"], "session_id": r["session_id"], "status": r.get("status")})
    return out


def by_call(call_id: str) -> dict[str, Any] | None:
    for p in _dir().glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if call_id in (r.get("calls") or []):
            return r
    return None
