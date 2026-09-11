"""The assessment blueprint — what the question pool has to cover.

Built **deterministically** from the recruiter-approved design, before any model
is asked for anything. That order is the point: coverage is an assessment
decision, and letting a model decide it would mean two candidates for the same
role could be assessed on different amounts of the job.

    skills + priorities + scopes          ─┐
    tasks + priorities + mappings          ├─►  Blueprint  ─►  slots  ─►  generator
    interview type + difficulty + duration ─┘

A blueprint is a list of **slots**. Each slot is one generation request with a
named responsibility — this skill, this task, this difficulty, this many
questions — which is what makes coverage measurable afterwards and regeneration
localised to one question rather than the whole interview.

Every policy number here is a module constant, not a sentence in a prompt.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.types import InterviewDefinition, duration_band
from packages.types.definition import PRIORITY_RANK, SkillSpec, TaskSpec

# --------------------------------------------------------------------------- #
#  Policy
#
#  Configurable, and deliberately out here where it can be read and argued with.
# --------------------------------------------------------------------------- #

#: How much of the interview a skill is worth, by priority band. Ratios, not
#: percentages — the budget is divided in proportion to these.
PRIORITY_WEIGHT: dict[str, float] = {"high": 3.0, "medium": 1.5, "low": 1.0}

#: The floor. A skill the role depends on must not rest on a single answer,
#: because one bad question then decides it.
MIN_ITEMS: dict[str, int] = {"high": 2, "medium": 1, "low": 1}

#: The ceiling per skill, by priority. Per-priority rather than one global cap:
#: a single cap flattens the whole point of priority as soon as the pool is
#: large enough to reach it — four skills at a cap of four is an equal pool
#: whatever the recruiter marked as essential.
MAX_ITEMS: dict[str, int] = {"high": 5, "medium": 3, "low": 2}

#: How long a spoken answer takes, by difficulty. Measured in seconds of
#: candidate speech, not of wall clock.
BASE_ANSWER_SEC: dict[str, int] = {"easy": 60, "medium": 90, "hard": 120}

#: What a follow-up costs when one happens. Probes are the reason duration is
#: not questions × minutes: a thin answer draws two, and the interview runs long
#: exactly when the candidate is struggling.
PROBE_SEC = 45

#: The share of questions expected to draw a follow-up in a typical interview.
#: From the candidate runtime's own persona runs: a strong candidate drew ~6
#: follow-ups across 8 questions, a thin one ~16.
EXPECTED_PROBE_RATE = 0.55

#: Greeting, transitions and the close.
OVERHEAD_SEC = 90

#: The pool is bigger than one interview consumes, so selection has something to
#: choose between and a candidate re-sitting does not meet the same eight
#: questions. Too large and the recruiter cannot review it, which is worse than
#: too small: an unreviewed pool is an unapproved assessment.
POOL_MULTIPLIER = 1.75
MAX_POOL_SIZE = 40

#: Difficulty mix by interview difficulty. Every interview opens easy — the
#: runtime's first question is a warm-up, and a pool with no easy question
#: cannot honour that.
DIFFICULTY_MIX: dict[str, dict[str, float]] = {
    "easy":   {"easy": 0.5, "medium": 0.4, "hard": 0.1},
    "medium": {"easy": 0.25, "medium": 0.5, "hard": 0.25},
    "hard":   {"easy": 0.15, "medium": 0.4, "hard": 0.45},
}

#: Which question type suits which situation. The generator is told the type
#: rather than choosing it, so a technical role is not interviewed entirely in
#: "tell me about a time" and a service role is not given a quiz.
def question_type_for(skill: SkillSpec, task: TaskSpec | None, experience_to: int) -> str:
    scope = f"{skill.name} {skill.description} {skill.assessment_scope}".lower()
    technical_markers = (
        "design", "debug", "architect", "concurren", "algorith", "database", "sql",
        "api", "latency", "throughput", "idempot", "distributed", "security",
        "correctness", "failure", "query", "schema", "code", "test",
    )
    if any(m in scope for m in technical_markers):
        # A concrete task to reason about beats an abstract knowledge check.
        return "task_based" if task else "technical"
    if task:
        # Junior candidates may not have the history a behavioural question
        # needs; a situation they can reason about measures the same thing
        # without penalising them for a short CV.
        return "behavioral" if experience_to >= 4 else "situational"
    return "situational"


# --------------------------------------------------------------------------- #
#  Shapes
# --------------------------------------------------------------------------- #
SlotStatus = str   # pending | generated | failed | invalid


@dataclass
class Slot:
    """One generation request with a named coverage responsibility."""

    id: str
    skill_id: str
    skill_name: str
    task_id: str = ""
    task_name: str = ""
    question_type: str = "situational"
    #: The primary difficulty, for display. `difficulties` is what generation
    #: uses — one slot asks for several questions at once so the generator can
    #: see them together and make them genuinely different, rather than being
    #: asked the same thing N times and returning N variations of one question.
    difficulty: str = "medium"
    difficulties: list[str] = field(default_factory=list)
    count: int = 1
    priority: str = "medium"
    status: SlotStatus = "pending"
    error: str = ""
    question_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillCoverage:
    skill_id: str
    skill_name: str
    priority: str
    target_question_count: int
    min_items: int


@dataclass
class Blueprint:
    interview_id: str = ""
    target_duration_min: int = 20
    difficulty: str = "medium"
    interview_type: str = "medium"
    #: How many questions one candidate is expected to be asked.
    live_item_budget: int = 8
    pool_size: int = 14
    coverage: list[SkillCoverage] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interview_id": self.interview_id,
            "target_duration_min": self.target_duration_min,
            "difficulty": self.difficulty,
            "interview_type": self.interview_type,
            "live_item_budget": self.live_item_budget,
            "pool_size": self.pool_size,
            "coverage": [asdict(c) for c in self.coverage],
            "slots": [s.to_dict() for s in self.slots],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Blueprint":
        bp = Blueprint(
            interview_id=d.get("interview_id", ""),
            target_duration_min=d.get("target_duration_min", 20),
            difficulty=d.get("difficulty", "medium"),
            interview_type=d.get("interview_type", "medium"),
            live_item_budget=d.get("live_item_budget", 8),
            pool_size=d.get("pool_size", 14),
        )
        bp.coverage = [SkillCoverage(**c) for c in d.get("coverage", [])]
        known = set(Slot.__dataclass_fields__)
        bp.slots = [
            Slot(**{k: v for k, v in s.items() if k in known}) for s in d.get("slots", [])
        ]
        return bp

    def slot(self, slot_id: str) -> Slot | None:
        return next((s for s in self.slots if s.id == slot_id), None)

    def target_for(self, skill_id: str) -> SkillCoverage | None:
        return next((c for c in self.coverage if c.skill_id == skill_id), None)


class BlueprintError(ValueError):
    """The approved design cannot produce a usable blueprint."""


# --------------------------------------------------------------------------- #
#  Budget
# --------------------------------------------------------------------------- #
def live_item_budget(
    duration_min: int, difficulty: str, *, probe_rate: float = EXPECTED_PROBE_RATE
) -> int:
    """How many questions fit in the interview, with room for follow-ups.

    Not `duration / minutes_per_question`. A follow-up costs time and happens
    exactly when the candidate is struggling, so a budget that ignores probing
    overruns on precisely the interviews that were already going badly.

        seconds_per_item = base_answer + probe_rate × probe_cost
        budget           = (duration − overhead) / seconds_per_item
    """
    base = BASE_ANSWER_SEC.get(difficulty, BASE_ANSWER_SEC["medium"])
    per_item = base + probe_rate * PROBE_SEC
    usable = max(0, duration_min * 60 - OVERHEAD_SEC)
    return max(3, int(usable // per_item))


def pool_size_for(budget: int, floors: int) -> int:
    """How many questions to generate.

    Bigger than one interview consumes, so selection has something to choose
    between — but never so big the recruiter stops reading it, because an
    unreviewed pool is an unapproved assessment. Never smaller than the sum of
    the minimum floors, or the floors could not be met at all.
    """
    return min(MAX_POOL_SIZE, max(floors, int(round(budget * POOL_MULTIPLIER))))


def _difficulty_sequence(difficulty: str, count: int) -> list[str]:
    """`count` difficulties in the configured mix, easiest first.

    Easiest first because the runtime asks an easy question first and works up;
    a pool whose easy questions are all for one skill cannot honour that.
    """
    mix = DIFFICULTY_MIX.get(difficulty, DIFFICULTY_MIX["medium"])
    out: list[str] = []
    for level in ("easy", "medium", "hard"):
        out += [level] * int(round(mix[level] * count))
    while len(out) < count:
        out.append("medium")
    return out[:count]


def _stable_slot_id(interview_id: str, skill_id: str, index: int) -> str:
    """Deterministic, so regenerating a blueprint does not renumber every slot
    and orphan the questions already generated against them."""
    digest = hashlib.sha1(
        f"{interview_id}:{skill_id}:{index}".encode("utf-8")
    ).hexdigest()[:8]
    return f"slot_{digest}"


# --------------------------------------------------------------------------- #
#  Build
# --------------------------------------------------------------------------- #
def build(definition: InterviewDefinition) -> Blueprint:
    """Turn an approved design into the coverage the pool must satisfy."""
    skills = list(definition.skills)
    if not skills:
        raise BlueprintError(
            "This interview has no skills yet, so there is nothing to write questions about."
        )
    if not definition.tasks:
        raise BlueprintError(
            "This interview has no tasks yet. Questions are grounded in the work, so the "
            "tasks have to exist first."
        )

    band_low, band_high = duration_band(definition.interview_type)
    duration = min(max(definition.recommended_duration_min, band_low), band_high)
    budget = live_item_budget(duration, definition.difficulty)

    # --- share the budget out by priority ---
    weights = {s.id: PRIORITY_WEIGHT.get(s.priority, 1.0) for s in skills}
    total_weight = sum(weights.values()) or 1.0
    floors = {s.id: MIN_ITEMS.get(s.priority, 1) for s in skills}

    pool_size = pool_size_for(budget, sum(floors.values()))

    ceilings = {s.id: MAX_ITEMS.get(s.priority, 2) for s in skills}
    # The pool can never exceed what the per-skill ceilings allow. Asking for
    # more would push every skill to its cap and flatten priority again.
    pool_size = min(pool_size, sum(ceilings.values()))

    targets: dict[str, int] = {}
    for skill in skills:
        share = weights[skill.id] / total_weight
        want = int(round(share * pool_size))
        targets[skill.id] = max(floors[skill.id], min(ceilings[skill.id], want))

    # Rounding and the floor/ceiling clamps rarely land on exactly pool_size.
    # Trim from the lowest-priority skills that are still above their floor, and
    # top up the highest-priority ones still below the ceiling — so the drift
    # lands where it costs least.
    def rank(skill: SkillSpec) -> tuple:
        return (PRIORITY_RANK.get(skill.priority, 2), skill.name)

    while sum(targets.values()) > pool_size:
        trimmable = [
            s for s in sorted(skills, key=rank) if targets[s.id] > floors[s.id]
        ]
        if not trimmable:
            break
        targets[trimmable[0].id] -= 1
    while sum(targets.values()) < pool_size:
        growable = [
            s for s in sorted(skills, key=rank, reverse=True)
            if targets[s.id] < ceilings[s.id]
        ]
        if not growable:
            break
        targets[growable[0].id] += 1

    coverage = [
        SkillCoverage(
            skill_id=s.id,
            skill_name=s.name,
            priority=s.priority,
            target_question_count=targets[s.id],
            min_items=floors[s.id],
        )
        for s in skills
    ]

    # --- slots: one per (skill, task) responsibility, asked for together ---
    #
    # Batched rather than one slot per question (§19). A generator asked five
    # separate times for "a question about idempotency grounded in this task"
    # has no way to know it already wrote four; asked once for five, it can make
    # them different. It also means one failure costs one slot's worth of
    # coverage, not the whole skill.
    slots: list[Slot] = []
    for skill in skills:
        tasks = definition.tasks_for_skill(skill.id)
        want = targets[skill.id]
        difficulties = _difficulty_sequence(definition.difficulty, want)

        # Spread the skill's questions across the tasks that need it, so a skill
        # three pieces of work depend on is assessed against more than one.
        buckets: list[list[int]] = []
        bucket_count = min(len(tasks), want) or 1
        for index in range(want):
            slot_index = index % bucket_count
            while len(buckets) <= slot_index:
                buckets.append([])
            buckets[slot_index].append(index)

        for slot_index, indices in enumerate(buckets):
            task = tasks[slot_index] if slot_index < len(tasks) else None
            slot_difficulties = [difficulties[i] for i in indices]
            slots.append(
                Slot(
                    id=_stable_slot_id(definition.interview_id, skill.id, slot_index),
                    skill_id=skill.id,
                    skill_name=skill.name,
                    task_id=task.id if task else "",
                    task_name=(task.label if task else ""),
                    question_type=question_type_for(
                        skill, task, definition.experience_to
                    ),
                    difficulty=slot_difficulties[0],
                    difficulties=slot_difficulties,
                    count=len(indices),
                    priority=skill.priority,
                )
            )

    # The runtime ALWAYS opens with the easiest available question — that is the
    # warm-up, and it is not optional. A small pool at high difficulty can round
    # every skill's easy allocation to zero, which leaves a nervous candidate
    # meeting a hard scenario as the first thing they hear. Guarantee one, on the
    # highest-priority skill, so the interview opens where it matters most.
    if slots and not any("easy" in s.difficulties for s in slots):
        opener = max(slots, key=lambda s: PRIORITY_RANK.get(s.priority, 2))
        opener.difficulties[0] = "easy"
        opener.difficulty = "easy"

    # The duration allows `budget` questions; the pool contains `len(slots)`.
    # The interview cannot ask more questions than exist, so the number the
    # recruiter is shown is the smaller of the two rather than an aspiration
    # the pool cannot meet.
    return Blueprint(
        interview_id=definition.interview_id,
        target_duration_min=duration,
        difficulty=definition.difficulty,
        interview_type=definition.interview_type,
        live_item_budget=min(budget, sum(s.count for s in slots)),
        pool_size=sum(s.count for s in slots),
        coverage=coverage,
        slots=slots,
    )
