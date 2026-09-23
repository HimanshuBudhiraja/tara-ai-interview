"""Loading authored scenarios from disk.

The role-play equivalent of `orchestrator/pool.py`'s `Pool.load`, and the same
seam: a scenario reaches the engine as a `ScenarioDefinition` and the engine
cannot tell whether an author wrote it by hand, a console published it, or a
generator produced it. Today all three scenarios ship as JSON in `content/`.

Validation runs at load and raises, rather than being deferred to the first
session. A scenario with a beat that nobody can pass is a content bug, and the
place to find out is a test run or a publish — never a person sitting in front
of a microphone.
"""
from __future__ import annotations

import json
from pathlib import Path

from packages.types.scenario import ScenarioDefinition

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content" / "scenarios"


class ScenarioError(RuntimeError):
    """A scenario that cannot be run as written."""


def load_file(path: Path) -> ScenarioDefinition:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"{path.name}: {exc}") from exc
    defn = ScenarioDefinition.from_dict(raw)
    problems = defn.validate()
    if problems:
        raise ScenarioError(f"{path.name}: " + "; ".join(problems))
    return defn


def load_all(directory: Path | None = None) -> dict[str, ScenarioDefinition]:
    """Every shipped scenario, keyed by id."""
    root = directory or CONTENT_DIR
    out: dict[str, ScenarioDefinition] = {}
    for path in sorted(root.glob("*.json")):
        defn = load_file(path)
        out[defn.scenario_id] = defn
    return out


def load(scenario_id: str, directory: Path | None = None) -> ScenarioDefinition:
    found = load_all(directory).get(scenario_id)
    if found is None:
        raise ScenarioError(f"no scenario '{scenario_id}'")
    return found


def by_surface(surface: str, directory: Path | None = None) -> list[ScenarioDefinition]:
    return [d for d in load_all(directory).values() if d.policy.surface == surface]
