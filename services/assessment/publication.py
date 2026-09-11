"""Publication — the one-way door from draft to immutable assessment.

    draft  ──validate──►  InterviewVersion (immutable)  ──►  invitation  ──►  candidate

Everything upstream of this is editable and provisional. Everything downstream
is fixed: a candidate is judged against the interview as it was when they were
invited, and no later edit can reach them.

That guarantee is only worth anything if publication is strict, so this module
refuses more than it accepts. It checks metadata, skills, tasks, questions,
coverage, safety, and — the decisive one — that every question can actually be
handed to the existing runtime. A partially compatible assessment is never
published: the candidate who happened to be asked the one broken question would
be the one who found out.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from packages.types import DURATION_BANDS, InterviewDefinition
from packages.types.definition import DIFFICULTIES, INTERVIEW_TYPES, PRIORITY_RANK
from services.assessment import blueprint as bp
from services.assessment import runtime_adapter
from services.assessment.blueprint import Blueprint, BlueprintError
from services.assessment.validation import (
    validate_question,
    validate_question_pool_coverage,
)
from services.data.jobs import SUPPORTED_LANGUAGES


@dataclass
class PublishCheck:
    """Everything wrong at once, grouped by where the recruiter would fix it."""

    problems: list[dict[str, str]] = field(default_factory=list)

    def add(self, area: str, message: str) -> None:
        self.problems.append({"area": area, "message": message})

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def messages(self) -> list[str]:
        return [p["message"] for p in self.problems]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": self.problems}


def summarise(definition: InterviewDefinition, plan: Blueprint | None = None) -> dict[str, Any]:
    """The counts shown on the publish confirmation — all from persisted values.

    Nothing here is estimated or rounded up. A recruiter about to make an
    assessment immutable is entitled to the real numbers.
    """
    questions = definition.questions
    by_difficulty: dict[str, int] = {}
    for q in questions:
        by_difficulty[q.difficulty] = by_difficulty.get(q.difficulty, 0) + 1
    return {
        "title": definition.role_title,
        "language": definition.language,
        "language_label": SUPPORTED_LANGUAGES.get(definition.language, definition.language),
        "interview_type": definition.interview_type,
        "difficulty": definition.difficulty,
        "duration_min": definition.recommended_duration_min,
        "skills": len(definition.skills),
        "high_priority_skills": len(definition.high_priority_skills()),
        "tasks": len(definition.tasks),
        "questions": len(questions),
        "questions_asked_per_candidate": (
            plan.live_item_budget if plan else definition.runtime.question_budget
        ),
        "difficulty_distribution": by_difficulty,
    }


def validate_for_publish(
    definition: InterviewDefinition, plan: Blueprint | None = None
) -> PublishCheck:
    """Can this become an immutable assessment a candidate can sit?"""
    check = PublishCheck()

    # --- skills -----------------------------------------------------------
    # A skill with no domain is a skill that can never be counted alongside the
    # same competency in another interview. Resolution proposes one on every
    # save, so reaching publication without one means nothing in the skill
    # master matched its words — which is exactly when a human should choose.
    undomained = [s.name for s in definition.skills if not (s.domain or "").strip()]
    if undomained:
        shown = ", ".join(undomained[:4])
        more = f" and {len(undomained) - 4} more" if len(undomained) > 4 else ""
        check.add(
            "skills",
            f"These skills have no domain: {shown}{more}. Pick one on the review "
            "screen — without it the skill cannot be compared across interviews.",
        )

    # --- metadata ---------------------------------------------------------
    if not (definition.role_title or "").strip():
        check.add("interview", "The interview has no title.")
    if definition.language not in SUPPORTED_LANGUAGES:
        check.add("interview",
                  f"Tara can't interview in {definition.language!r} yet.")
    if definition.experience_from > definition.experience_to:
        check.add("interview",
                  f"The experience range runs backwards: {definition.experience_from} "
                  f"to {definition.experience_to} years.")
    if definition.interview_type not in INTERVIEW_TYPES:
        check.add("interview",
                  f"{definition.interview_type!r} is not an interview type.")
    if definition.difficulty not in DIFFICULTIES:
        check.add("interview", f"{definition.difficulty!r} is not a difficulty.")
    if definition.interview_type in DURATION_BANDS:
        low, high = DURATION_BANDS[definition.interview_type]
        if not low <= definition.recommended_duration_min <= high:
            check.add("interview",
                      f"A {definition.interview_type} interview runs {low}-{high} minutes, "
                      f"but this one is set to {definition.recommended_duration_min}.")

    # --- skills -----------------------------------------------------------
    if not definition.skills:
        check.add("skills", "The interview assesses no skills.")
    seen_skill_ids: set[str] = set()
    seen_skill_names: set[str] = set()
    for skill in definition.skills:
        if not skill.id:
            check.add("skills", f"The skill {skill.name!r} has no stable id.")
        elif skill.id in seen_skill_ids:
            check.add("skills", f"Two skills share the id {skill.id}.")
        seen_skill_ids.add(skill.id)

        name = skill.name.strip().lower()
        if not name:
            check.add("skills", "A skill has no name.")
        elif name in seen_skill_names:
            check.add("skills", f"Two skills are both called {skill.name!r}.")
        seen_skill_names.add(name)

        if skill.priority not in PRIORITY_RANK:
            check.add("skills",
                      f"{skill.name} has an unknown priority {skill.priority!r}.")
        if not (skill.assessment_scope or "").strip():
            check.add("skills",
                      f"{skill.name} has no assessment scope, so nothing says what "
                      f"would actually be probed within it.")

    # --- tasks ------------------------------------------------------------
    seen_task_ids: set[str] = set()
    for task in definition.tasks:
        if not task.id:
            check.add("tasks", f"The task {task.label!r} has no stable id.")
        elif task.id in seen_task_ids:
            check.add("tasks", f"Two tasks share the id {task.id}.")
        seen_task_ids.add(task.id)

        if not (task.description or "").strip():
            check.add("tasks", f"The task {task.id} has no description.")
        orphans = [s for s in task.skill_ids if s not in seen_skill_ids]
        if orphans:
            check.add("tasks",
                      f"{task.label!r} assesses skills that aren't part of this "
                      f"interview: {', '.join(orphans)}.")
        if not task.skill_ids:
            check.add("tasks", f"{task.label!r} assesses no skill at all.")

    # --- questions --------------------------------------------------------
    if not definition.questions:
        check.add("questions",
                  "No questions have been generated yet, so there is no interview to sit.")
    seen_question_ids: set[str] = set()
    for question in definition.questions:
        if not question.id:
            check.add("questions", "A question has no stable id.")
        elif question.id in seen_question_ids:
            check.add("questions", f"Two questions share the id {question.id}.")
        seen_question_ids.add(question.id)

        # The same validator the recruiter's edits go through. Publication is
        # not a second, weaker standard.
        verdict = validate_question(
            question, definition,
            existing=[q for q in definition.questions if q.id != question.id],
        )
        for message in verdict.messages:
            if any(p.fatal for p in verdict.problems):
                check.add("questions", f"{question.id}: {message}")

    # --- coverage ---------------------------------------------------------
    if definition.skills and definition.tasks:
        if plan is None:
            try:
                plan = bp.build(definition)
            except BlueprintError as exc:
                check.add("coverage", str(exc))
        if plan is not None:
            coverage = validate_question_pool_coverage(definition, plan)
            for problem in coverage.problems:
                if problem.fatal:
                    check.add("coverage", problem.message)

    # --- runtime compatibility -------------------------------------------
    #
    # The decisive check. Everything above can pass and this still fail, and if
    # it does the assessment must not be published: an interview the runtime
    # cannot run is not an interview.
    for problem in runtime_adapter.check_compatibility(definition):
        check.add("runtime", problem)

    return check


@dataclass
class PublishResult:
    version: int
    checksum: str
    published_at: float
    created: bool          # False when an identical publish already existed
    summary: dict[str, Any]


def snapshot(definition: InterviewDefinition) -> InterviewDefinition:
    """A detached copy, stamped with when it was frozen.

    Copied rather than referenced because a published version must not share
    objects with the draft it came from — a later edit reaching through a shared
    list is exactly the failure immutability exists to prevent.
    """
    frozen = InterviewDefinition.from_dict(definition.to_dict())
    frozen.evaluation.criteria = sorted(
        {c.label for q in frozen.questions for c in q.evaluation_criteria}
    )
    return frozen


def published_at_now() -> float:
    return time.time()
