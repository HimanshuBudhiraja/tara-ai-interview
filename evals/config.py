"""Reading models.yaml — without adding a YAML dependency.

The file is deliberately a small, flat subset of YAML (lists, maps, scalars,
inline lists), so it stays readable and editable by whoever is choosing models
without the harness pulling in a parser the product does not otherwise need.
PyYAML is used when it happens to be installed; otherwise this reader handles it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = EVALS_DIR / "models.yaml"
RESULTS_DIR = EVALS_DIR / "results"


@dataclass
class ModelSpec:
    model: str
    label: str = ""
    prompt_usd_per_m: float = 0.0
    completion_usd_per_m: float = 0.0
    note: str = ""

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.prompt_usd_per_m
            + completion_tokens * self.completion_usd_per_m
        ) / 1_000_000

    @property
    def name(self) -> str:
        return self.label or self.model


@dataclass
class EvalConfig:
    models: list[ModelSpec] = field(default_factory=list)
    workload_models: dict[str, list[str]] = field(default_factory=dict)
    priorities: dict[str, dict[str, float]] = field(default_factory=dict)
    latency_budget_ms: dict[str, int] = field(default_factory=dict)

    def spec(self, model: str) -> ModelSpec:
        found = next((m for m in self.models if m.model == model), None)
        # A model named for a workload but absent from evaluation_models is not
        # an error worth stopping for — it just has no pricing, so cost comes
        # back as zero and the report says so.
        return found or ModelSpec(model=model, label=model)

    def models_for(self, workload: str) -> list[ModelSpec]:
        names = self.workload_models.get(workload)
        if not names:
            return list(self.models)
        return [self.spec(n) for n in names]

    def priority(self, workload: str) -> dict[str, float]:
        return self.priorities.get(workload, {"quality": 1.0, "latency": 0.0, "cost": 0.0})


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #
def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [_scalar(p) for p in raw[1:-1].split(",") if p.strip()]
    if raw.startswith("{") and raw.endswith("}"):
        out: dict[str, Any] = {}
        for part in raw[1:-1].split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                out[k.strip()] = _scalar(v)
        return out
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d*\.\d+", raw):
        return float(raw)
    return raw.strip("'\"")


def _parse(text: str) -> dict[str, Any]:
    """Enough YAML for this file: nested maps, lists of maps, inline collections."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = raw_line.split("  #")[0].rstrip()
        indent = len(line) - len(line.lstrip())
        body = line.strip()

        while stack and stack[-1][0] >= indent and len(stack) > 1:
            stack.pop()
        parent = stack[-1][1]

        if body.startswith("- "):
            item_body = body[2:].strip()
            # `_Pending` is a list that has not committed to being one yet — a
            # key whose value turns out to be a list of maps reaches this line
            # as a pending container, and refusing it here read `models.yaml`
            # as empty on any machine without PyYAML installed.
            if not isinstance(parent, (list, _Pending)):
                continue
            if ":" in item_body:
                entry: dict[str, Any] = {}
                key, value = item_body.split(":", 1)
                entry[key.strip()] = _scalar(value)
                parent.append(entry)
                # One past this line's indent, not two: the entry's own keys sit
                # at indent + 2, and pushing it at that depth popped it again on
                # its first key — which is how every `label` and price in
                # `models.yaml` used to vanish without PyYAML installed.
                stack.append((indent + 1, entry))
            else:
                parent.append(_scalar(item_body))
            continue

        if ":" not in body:
            continue
        key, value = body.split(":", 1)
        key, value = key.strip(), value.strip()
        # A `_Pending` is a map that has not committed to being one yet, and it
        # forwards `[]=` onto its container. Excluding it here read every nested
        # map — `workload_models`, `latency_budget_ms` — as empty.
        if isinstance(parent, _Pending) and not isinstance(parent.container, dict):
            continue
        if not isinstance(parent, (dict, _Pending)):
            continue
        if value:
            parent[key] = _scalar(value)
        else:
            container: Any = {}
            # Peek is unnecessary: a key with no value is a list if the next
            # meaningful line at deeper indent starts with "- ". Resolved lazily
            # by seeding a dict and swapping on first "- ".
            parent[key] = container
            stack.append((indent, _Pending(parent, key, container)))
    return root


class _Pending:
    """A container whose type isn't known until its first child appears."""

    def __init__(self, parent: dict[str, Any], key: str, container: Any) -> None:
        self.parent, self.key, self.container = parent, key, container

    def append(self, item: Any) -> None:
        if not isinstance(self.container, list):
            self.container = []
            self.parent[self.key] = self.container
        self.container.append(item)

    def __setitem__(self, key: str, value: Any) -> None:
        self.container[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.container


def load(path: Path | str = DEFAULT_CONFIG) -> EvalConfig:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        raw = yaml.safe_load(text)
    except ImportError:
        raw = _parse(text)

    models = [
        ModelSpec(
            model=m["model"],
            label=str(m.get("label", "")),
            prompt_usd_per_m=float(m.get("prompt_usd_per_m", 0) or 0),
            completion_usd_per_m=float(m.get("completion_usd_per_m", 0) or 0),
            note=str(m.get("note", "")),
        )
        for m in (raw.get("evaluation_models") or [])
    ]
    return EvalConfig(
        models=models,
        workload_models={k: list(v) for k, v in (raw.get("workload_models") or {}).items()},
        priorities={k: dict(v) for k, v in (raw.get("priorities") or {}).items()},
        latency_budget_ms=dict(raw.get("latency_budget_ms") or {}),
    )
