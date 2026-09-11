"""The question pool — fixed, authored, loaded from disk.

There is NO question generation here. TARA conducts the interview; it does not
author questions. Selection is deterministic: pick the competency that is
furthest behind its target coverage, then the easiest unasked item in it.

Two ways in, one shape out:

  `Pool.load`             the authored JSON pool for a role (the fixed CSR bank)
  `Pool.from_definition`  an `InterviewDefinition` — a published version's frozen
                          questions, wherever they originally came from

The second is the one the runtime actually uses. It is why the orchestrator
cannot tell an authored question from a generated one: by the time a question
reaches it, both are `Item`s selected by the same policy. A Question Generation
Engine plugs in by producing a definition, not by touching this file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from packages.types import InterviewDefinition
from services import config


@dataclass(frozen=True)
class Item:
    id: str
    type: str
    competency: str
    difficulty: str
    prompt: str
    probe_eligible: bool
    probe_bank: list[str]
    looking_for: list[str]
    clarify: str = ""
    modality: list[str] = field(default_factory=lambda: ["voice", "text"])
    time_estimate_sec: int = 90


@dataclass(frozen=True)
class Competency:
    id: str
    label: str
    weight: float
    min_items: int


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}


@dataclass(frozen=True)
class Plan:
    """A recruiter's configuration, reduced to what selection actually needs.

    Keeping this separate from `InterviewConfig` means the selection policy
    never has to know how a recruiter expressed their intent — only the budget,
    the permitted items, and the distribution across competencies.
    """

    budget: int
    allowed: set[str]
    weights: dict[str, float]
    min_items: dict[str, int]


class Pool:
    """One role's authored items plus the deterministic selection policy."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.role: str = raw["role"]
        self.role_title: str = raw["role_title"]
        self.closing: str = raw.get("closing", "That's everything. Thank you for your time.")
        self.competencies: list[Competency] = [
            Competency(c["id"], c["label"], float(c["weight"]), int(c.get("min_items", 1)))
            for c in raw["competencies"]
        ]
        self.items: list[Item] = [
            Item(
                id=i["id"],
                type=i["type"],
                competency=i["competency"],
                difficulty=i.get("difficulty", "medium"),
                prompt=i["prompt"],
                probe_eligible=bool(i.get("probe_eligible", True)),
                probe_bank=list(i.get("probe_bank", [])),
                clarify=i.get("clarify", ""),
                looking_for=list(i.get("looking_for", [])),
                modality=list(i.get("modality", ["voice", "text"])),
                time_estimate_sec=int(i.get("time_estimate_sec", 90)),
            )
            for i in raw["items"]
        ]
        self._by_id = {i.id: i for i in self.items}
        self._comp_by_id = {c.id: c for c in self.competencies}

    # -- loading ----------------------------------------------------------- #
    @classmethod
    def load(cls, role: str = config.ROLE) -> "Pool":
        path = config.CONTENT_DIR / f"{role}_pool.json"
        if not path.exists():
            raise FileNotFoundError(f"no authored pool for role {role!r} at {path}")
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_definition(cls, defn: "InterviewDefinition") -> "Pool":
        """Build a pool from a published contract.

        Question order is preserved exactly as the definition stores it, because
        pool order is selection's final tie-break: reordering here would quietly
        change which question a candidate meets third.
        """
        return cls(
            {
                "role": defn.role,
                "role_title": defn.role_title,
                "closing": defn.closing,
                "competencies": [
                    {"id": b.id, "label": b.label, "weight": b.weight, "min_items": b.min_items}
                    for b in defn.banks
                ],
                "items": [
                    {
                        "id": q.id,
                        "type": q.type,
                        "competency": q.competency,
                        "difficulty": q.difficulty,
                        "prompt": q.question_text,
                        "probe_eligible": q.probe_eligible,
                        "probe_bank": list(q.probe_bank),
                        "clarify": q.clarify,
                        "looking_for": list(q.looking_for),
                        "modality": list(q.modality),
                        "time_estimate_sec": q.time_budget_sec,
                    }
                    for q in defn.questions
                ],
            }
        )

    # -- lookups ----------------------------------------------------------- #
    def item(self, item_id: str) -> Item:
        return self._by_id[item_id]

    def competency_label(self, comp_id: str) -> str:
        c = self._comp_by_id.get(comp_id)
        return c.label if c else comp_id

    # -- the recruiter's configuration, in the form selection needs --------- #
    def plan(self, cfg: Any = None) -> "Plan":
        """Turn an InterviewConfig into a selection plan, or fall back to the
        pool's authored defaults when no interview has been configured."""
        if cfg is None:
            return Plan(
                budget=config.QUESTION_BUDGET,
                allowed={i.id for i in self.items},
                weights={c.id: c.weight for c in self.competencies},
                min_items={c.id: c.min_items for c in self.competencies},
            )
        weights = cfg.pool_weights()
        if not weights:
            # Nothing extracted yet, or nothing the pool can answer. Fall back to
            # the authored defaults so a half-configured interview still runs
            # rather than silently asking nothing.
            return self.plan(None)
        allowed = {i.id for i in self.items if i.competency in weights}
        return Plan(
            budget=cfg.question_budget,
            allowed=allowed,
            weights=weights,
            min_items=cfg.pool_min_items(),
        )

    def plan_from_definition(self, defn: "InterviewDefinition") -> "Plan":
        """The selection plan a published version produces.

        Identical arithmetic to `plan(cfg)` — both go through
        `derive_bank_weights` — so a draft preview and the live interview it
        previews cannot disagree.
        """
        weights = defn.bank_weights()
        if not weights:
            # A definition with no skill mapped to any bank would ask nothing.
            # Fall back to the definition's own authored bank weights rather
            # than running a silent zero-question interview.
            weights = {c.id: c.weight for c in self.competencies if c.weight > 0}
            if not weights:
                return self.plan(None)
            total = sum(weights.values())
            weights = {k: v / total for k, v in weights.items()}
            min_items = {c.id: c.min_items for c in self.competencies}
        else:
            min_items = defn.bank_min_items()
        allowed = {i.id for i in self.items if i.competency in weights}
        return Plan(
            budget=defn.runtime.question_budget,
            allowed=allowed,
            weights=weights,
            min_items=min_items,
        )

    # -- selection --------------------------------------------------------- #
    def select_next(self, asked_ids: list[str], plan: "Plan | None" = None) -> Item | None:
        """Deterministic next item, or None when the interview should close.

        Policy, in order:
          1. stop once the question budget is spent or the pool is exhausted
          2. prefer the competency with the largest shortfall against its share
             of the budget (min_items acts as a floor)
          3. break shortfall ties by competency weight, so when the budget is
             too small to satisfy every floor, the competencies that matter
             most to the role are the ones that get their second item
          4. within a competency, ask easier items before harder ones — the
             candidate should warm up before the hard scenarios
          5. remaining ties break on pool order, so the same inputs always give
             the same interview (auditable)

        Determinism is what lets the recruiter console show the exact question
        order a configuration will produce, before anyone is invited.
        """
        plan = plan or self.plan()
        asked = set(asked_ids)
        if len(asked) >= plan.budget:
            return None

        remaining = [i for i in self.items if i.id not in asked and i.id in plan.allowed]
        if not remaining:
            return None

        asked_per_comp: dict[str, int] = {}
        for item_id in asked:
            item = self._by_id.get(item_id)
            if item:
                asked_per_comp[item.competency] = asked_per_comp.get(item.competency, 0) + 1

        def shortfall(comp_id: str) -> float:
            target = max(plan.min_items.get(comp_id, 0), plan.weights.get(comp_id, 0.0) * plan.budget)
            return target - asked_per_comp.get(comp_id, 0)

        def rank(item: Item) -> tuple:
            return (
                -shortfall(item.competency),
                -plan.weights.get(item.competency, 0.0),
                _DIFFICULTY_ORDER.get(item.difficulty, 1),
                self.items.index(item),
            )

        def opening_rank(item: Item) -> tuple:
            # The first question is a warm-up, so difficulty outranks coverage.
            # Nobody should meet a hard scenario as the first thing they hear.
            return (_DIFFICULTY_ORDER.get(item.difficulty, 1),) + rank(item)

        return sorted(remaining, key=opening_rank if not asked else rank)[0]

    def running_order(self, plan: "Plan | None" = None) -> list[Item]:
        """The exact items this plan will ask, in order.

        Only meaningful because selection is deterministic — this is the same
        code path a real interview takes, not a simulation of it.
        """
        plan = plan or self.plan()
        asked: list[str] = []
        order: list[Item] = []
        while True:
            item = self.select_next(asked, plan)
            if item is None:
                return order
            order.append(item)
            asked.append(item.id)

    def coverage(self, asked_ids: list[str], plan: "Plan | None" = None) -> list[dict[str, Any]]:
        """Per-competency progress — drives the candidate's progress rail."""
        plan = plan or self.plan()
        asked = set(asked_ids)
        out = []
        for c in self.competencies:
            if c.id not in plan.weights:
                continue  # switched off for this interview
            n = sum(1 for i in self.items if i.id in asked and i.competency == c.id)
            out.append({
                "id": c.id,
                "label": c.label,
                "asked": n,
                "target": plan.min_items.get(c.id, c.min_items),
            })
        return out


_POOLS: dict[str, Pool] = {}


def get_pool(role: str = config.ROLE) -> Pool:
    if role not in _POOLS:
        _POOLS[role] = Pool.load(role)
    return _POOLS[role]
