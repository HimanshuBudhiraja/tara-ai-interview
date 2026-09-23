"""Loading knowledge bases from disk.

The same seam as `scenarios.py`: a base reaches the engine as a `KnowledgeBase`
and the engine cannot tell whether an author wrote the JSON, a console published
it, or an ingestion pipeline chunked a PDF into it.

Validation runs at load and raises. A knowledge base with a duplicate passage id
grounds two different facts on one reference, and the place to discover that is
a test run, not a transcript nobody can audit six months later.
"""
from __future__ import annotations

import json
from pathlib import Path

from packages.types.knowledge import KnowledgeBase

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content" / "knowledge"


class KnowledgeError(RuntimeError):
    """A knowledge base that cannot be used as written."""


def load_file(path: Path) -> KnowledgeBase:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeError(f"{path.name}: {exc}") from exc
    kb = KnowledgeBase.from_dict(raw)
    problems = kb.validate()
    if problems:
        raise KnowledgeError(f"{path.name}: " + "; ".join(problems))
    return kb


def load_all(directory: Path | None = None) -> dict[str, KnowledgeBase]:
    root = directory or CONTENT_DIR
    if not root.exists():
        return {}
    return {kb.kb_id: kb for kb in (load_file(p) for p in sorted(root.glob("*.json")))}


def load(kb_id: str, directory: Path | None = None) -> KnowledgeBase:
    found = load_all(directory).get(kb_id)
    if found is None:
        raise KnowledgeError(f"no knowledge base '{kb_id}'")
    return found
