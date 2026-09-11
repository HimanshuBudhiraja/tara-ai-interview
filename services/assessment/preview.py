"""Running-order preview — the real selection algorithm, not a simulation.

§28's rule: the recruiter preview and the live interview must not implement two
different selection policies. So this does not model selection; it calls it.

    definition (generated pool)  ─►  Pool.from_definition  ─►  select_next
                                          the production classes

The only thing this module adds is the adapter that makes a generated
definition look like something `Pool` can load: banks derived from the skills,
`competency` already set to the primary skill id by the generator, and the
plan's weights coming from the same `derive_bank_weights` the runtime uses.

**No candidate runtime behaviour changes.** Nothing here is wired into a
session; a candidate still sits the authored pool until the publication phase
connects a version. This is a read-only view of what selection *would* do.
"""
from __future__ import annotations

from typing import Any

from packages.types import InterviewDefinition
from packages.types.definition import BankSpec
from services.orchestrator.pool import Plan, Pool


def as_runtime_definition(definition: InterviewDefinition) -> InterviewDefinition:
    """Fill in what the runtime needs and the designer does not produce.

    A generated pool groups by skill rather than by an authored question bank,
    so the banks ARE the skills. That is what lets priority → coverage work
    unchanged: `derive_bank_weights` reads skill priority either way.
    """
    runtime = InterviewDefinition.from_dict(definition.to_dict())
    if not runtime.banks:
        runtime.banks = [
            BankSpec(id=s.id, label=s.name, weight=0.0, min_items=1)
            for s in runtime.skills
        ]
    for skill in runtime.skills:
        # The skill is its own bank. Set here rather than at generation so an
        # interview designed before questions existed still previews correctly.
        if not skill.question_bank:
            skill.question_bank = skill.id
    for question in runtime.questions:
        if not question.competency:
            question.competency = question.skill_id
    return runtime


def build_pool(definition: InterviewDefinition) -> tuple[Pool, Plan]:
    """The production `Pool` and `Plan`, built from a generated definition."""
    runtime = as_runtime_definition(definition)
    pool = Pool.from_definition(runtime)
    return pool, pool.plan_from_definition(runtime)


def running_order(definition: InterviewDefinition) -> list[dict[str, Any]]:
    """The exact questions this pool would ask, in order.

    Only meaningful because selection is deterministic — this is the same code
    path a live interview takes, so what the recruiter sees is what a candidate
    would get.
    """
    if not definition.questions:
        return []
    runtime = as_runtime_definition(definition)
    pool, plan = build_pool(definition)
    by_id = {q.id: q for q in runtime.questions}
    skills = {s.id: s.name for s in runtime.skills}

    order: list[dict[str, Any]] = []
    for position, item in enumerate(pool.running_order(plan), start=1):
        question = by_id.get(item.id)
        order.append({
            "position": position,
            "question_id": item.id,
            "question_text": item.prompt,
            "difficulty": item.difficulty,
            "skill_id": question.skill_id if question else item.competency,
            "skill_name": skills.get(item.competency, item.competency),
            "question_type": question.question_type if question else "",
            "probe_eligible": item.probe_eligible,
        })
    return order


def coverage_preview(definition: InterviewDefinition) -> list[dict[str, Any]]:
    """What the running order actually covers, per skill.

    The number that matters is not how many questions exist for a skill but how
    many the interview will reach — a skill with four questions that selection
    never gets to is a skill the interview does not assess.
    """
    order = running_order(definition)
    counts: dict[str, int] = {}
    for entry in order:
        counts[entry["skill_id"]] = counts.get(entry["skill_id"], 0) + 1
    return [
        {
            "skill_id": s.id,
            "skill_name": s.name,
            "priority": s.priority,
            "in_pool": sum(1 for q in definition.questions if q.skill_id == s.id),
            "asked": counts.get(s.id, 0),
        }
        for s in definition.skills
    ]
