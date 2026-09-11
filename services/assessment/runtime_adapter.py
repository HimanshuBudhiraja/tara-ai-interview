"""Published assessment → the existing candidate runtime contract.

One direction, one canonical translation:

    InterviewVersion (immutable)  ─►  CandidateRuntimeAdapter  ─►  Pool / Plan
                                                                   (production classes)

The rule this module exists to hold: **the runtime is authoritative and does not
change.** The adapter's job is to present a published assessment in the shape
the orchestrator already reads. If a published question cannot be expressed in
that shape, publication fails — the orchestrator is not bent to fit it.

What the runtime reads off an item is a short list: prompt, competency,
difficulty, `looking_for`, `probe_bank`, `clarify`, `probe_eligible` and a time
estimate. Everything else on a published question — evaluation criteria, task
mapping, secondary skills, expected signal — is assessment metadata that the
runtime has no use for and the candidate must never see.

`competency = primary skill id` is preserved from the question-pool phase. It is
what lets the existing priority → coverage machinery work on a generated pool
with no change to selection at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.types import InterviewDefinition
from packages.types.definition import BankSpec, QuestionSpec
from services.orchestrator.pool import Item, Plan, Pool


class AdapterError(ValueError):
    """A published assessment the runtime could not be given.

    Raised at publish time, never at runtime. Finding this out with a candidate
    on the line is an outage; finding it out when a recruiter presses Publish is
    a form.
    """


#: Everything the orchestrator actually reads off an item. Named here so the
#: compatibility check is against a list someone can look at, rather than
#: against whatever `Item` happens to have today.
RUNTIME_REQUIRED = (
    "prompt", "competency", "difficulty", "looking_for", "clarify",
    "probe_eligible", "time_estimate_sec",
)


@dataclass(frozen=True)
class RuntimeAssessment:
    """What a session runs on. Read-only by construction."""

    interview_id: str
    version: int
    definition: InterviewDefinition
    pool: Pool
    plan: Plan

    @property
    def question_count(self) -> int:
        return len(self.pool.items)

    def item(self, question_id: str) -> Item:
        return self.pool.item(question_id)


def _needs_adapting(definition: InterviewDefinition) -> bool:
    """True for a generated pool, false for one backed by an authored bank."""
    return any(q.source in ("generated", "manual") for q in definition.questions)


def to_runtime_definition(definition: InterviewDefinition) -> InterviewDefinition:
    """Fill in what the runtime needs and the designer does not produce.

    A generated pool groups by skill rather than by an authored question bank,
    so the banks ARE the skills. Non-destructive: works on a copy, because the
    published version is immutable and this is called on every turn.
    """
    runtime = InterviewDefinition.from_dict(definition.to_dict())

    if not runtime.banks:
        runtime.banks = [
            BankSpec(id=s.id, label=s.name, weight=0.0, min_items=1)
            for s in runtime.skills
        ]
    for skill in runtime.skills:
        if not skill.question_bank:
            skill.question_bank = skill.id
    for question in runtime.questions:
        if not question.competency:
            question.competency = question.skill_id
    return runtime


def check_compatibility(definition: InterviewDefinition) -> list[str]:
    """Can every question in this assessment be given to the runtime?

    Run before publishing. A partially compatible assessment is not published:
    the candidate who happened to be selected the one broken question would be
    the one who found out.
    """
    problems: list[str] = []
    runtime = to_runtime_definition(definition)

    if not runtime.questions:
        problems.append("The interview has no questions, so nothing would be asked.")
        return problems

    bank_ids = {b.id for b in runtime.banks}
    for question in runtime.questions:
        label = question.id or "(a question with no id)"
        if not question.competency:
            problems.append(
                f"{label} has no competency, so selection could not count it against "
                f"any skill."
            )
        elif question.competency not in bank_ids:
            problems.append(
                f"{label} is counted against {question.competency}, which is not one of "
                f"this interview's skills."
            )
        if not (question.question_text or "").strip():
            problems.append(f"{label} has no text to speak.")
        if not question.looking_for:
            problems.append(
                f"{label} has no expected signals, so the runtime could not tell a "
                f"good answer from a bad one."
            )
        if not (question.clarify or "").strip():
            problems.append(
                f"{label} has no restatement, so 'what do you mean?' would have no "
                f"authored answer."
            )
        if question.probe_eligible and not question.probe_bank:
            problems.append(
                f"{label} can be probed but has no authored fallback, so a rejected "
                f"follow-up would leave nothing to ask."
            )
        if question.difficulty not in ("easy", "medium", "hard"):
            problems.append(f"{label} has difficulty {question.difficulty!r}.")
        if question.time_budget_sec <= 0:
            problems.append(f"{label} has no time estimate.")

    # The decisive check: actually build the runtime objects. Anything the two
    # classes disagree about surfaces here rather than mid-interview.
    try:
        pool = Pool.from_definition(runtime)
        plan = pool.plan_from_definition(runtime)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"The runtime could not load this assessment: {exc}")
        return problems

    if not plan.allowed:
        problems.append(
            "No question in this interview belongs to a skill that is being assessed, "
            "so selection would have nothing to choose from."
        )
    elif pool.select_next([], plan) is None:
        problems.append("The runtime would not select a first question.")

    # Every item the runtime builds must carry what the turn loop reads.
    for item in pool.items:
        for attribute in RUNTIME_REQUIRED:
            if not hasattr(item, attribute):
                problems.append(
                    f"Question {item.id} is missing {attribute}, which the runtime reads."
                )
    return problems


def adapt(definition: InterviewDefinition) -> RuntimeAssessment:
    """Build the runtime objects for a published assessment.

    Raises `AdapterError` rather than returning something half-built — a
    partially adapted assessment is one where some candidate meets a question
    the runtime cannot handle.
    """
    problems = check_compatibility(definition)
    if problems:
        raise AdapterError("; ".join(problems[:3]))

    runtime = to_runtime_definition(definition)
    pool = Pool.from_definition(runtime)
    return RuntimeAssessment(
        interview_id=definition.interview_id,
        version=definition.version,
        definition=runtime,
        pool=pool,
        plan=pool.plan_from_definition(runtime),
    )


def candidate_safe_summary(definition: InterviewDefinition) -> dict[str, Any]:
    """What the welcome screen may be told about an interview.

    Deliberately tiny. A candidate needs to know how long it will take and
    roughly what it covers; they must never receive the questions, the expected
    signals, the criteria, the priorities or the task mapping (§20, §23).
    """
    runtime = to_runtime_definition(definition)
    pool = Pool.from_definition(runtime)
    plan = pool.plan_from_definition(runtime)
    return {
        "role_title": definition.role_title,
        "question_count": min(plan.budget, len(plan.allowed)),
        "estimated_minutes": definition.recommended_duration_min,
        # Skill NAMES only — no ids, no priorities, no counts, nothing that
        # would tell a candidate where to concentrate.
        "competencies": [s.name for s in definition.skills if s.question_bank or s.id],
    }
