"""Agents made in the Agent Builder.

Runtime state written by a recruiter, kept under `DATA_DIR` beside the
Scenario Builder's rows. A row holds the working configuration and, once
published, a frozen copy. They are kept apart for the same reason as there:
editing a published agent must not change what the next participant meets
until it is published again.

Each row records the organization that made it. A recruiter only ever sees
their own organization's agents, and that check sits in the API.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from services import config


def _dir() -> Path:
    d = config.DATA_DIR / "built_agents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(agent_id: str) -> Path:
    safe = "".join(ch for ch in agent_id if ch.isalnum() or ch in "_-")
    return _dir() / f"{safe}.json"


def new_id(title: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in (title or "").lower()).strip("_")
    slug = "_".join(p for p in slug.split("_") if p)[:32] or "agent"
    return f"ab_{slug}_{secrets.token_hex(3)}"


def load(agent_id: str) -> dict[str, Any] | None:
    p = _path(agent_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save(row: dict[str, Any]) -> dict[str, Any]:
    row["updated_at"] = time.time()
    row.setdefault("created_at", row["updated_at"])
    target = _path(row["agent_id"])
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, indent=1))
    tmp.replace(target)
    return row


def list_all() -> list[dict[str, Any]]:
    rows = []
    for p in _dir().glob("*.json"):
        try:
            rows.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda r: r.get("updated_at", 0), reverse=True)


def by_call(call_id: str) -> dict[str, Any] | None:
    """The agent a Retell test call was placed for."""
    for row in list_all():
        if any(t.get("call_id") == call_id for t in row.get("tests") or []):
            return row
    return None
