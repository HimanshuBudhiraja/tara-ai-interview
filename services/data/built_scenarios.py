"""Scenarios made in the Scenario Builder.

Kept apart from `content/scenarios/`, which is authored by hand and checked in.
These are runtime state, written by a configurer, and live under `DATA_DIR`
beside everything else a deployment creates.

A row holds the working draft and, once published, the frozen version the
engine runs. They are separate on purpose: editing a published scenario must not
change what the next person sits until it is published again, or two people on
"the same scenario" were measured against different keys.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from services import config


def _dir() -> Path:
    d = config.DATA_DIR / "built_scenarios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(scenario_id: str) -> Path:
    safe = "".join(ch for ch in scenario_id if ch.isalnum() or ch in "_-")
    return _dir() / f"{safe}.json"


def new_id(title: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in title.lower()).strip("_")
    slug = "_".join(p for p in slug.split("_") if p)[:32] or "roleplay"
    return f"rp_{slug}_{secrets.token_hex(3)}"


def load(scenario_id: str) -> dict[str, Any] | None:
    p = _path(scenario_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save(row: dict[str, Any]) -> dict[str, Any]:
    row["updated_at"] = time.time()
    tmp = _path(row["scenario_id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(row, indent=1))
    tmp.replace(_path(row["scenario_id"]))
    return row


def list_all() -> list[dict[str, Any]]:
    rows = []
    for p in _dir().glob("*.json"):
        try:
            rows.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda r: r.get("updated_at", 0), reverse=True)


def published() -> list[dict[str, Any]]:
    """Every published version's frozen configuration."""
    return [r["published"] for r in list_all() if r.get("published")]
