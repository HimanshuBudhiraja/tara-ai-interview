"""The question pool: generate, review, edit, regenerate.

Everything here writes the **draft**. Nothing publishes, and nothing is reachable
by a candidate — the candidate runtime still serves the authored pool until the
publication phase connects a version (§29).

The unit of work is a blueprint slot. One slot failing costs one slot's coverage
and is retryable; the rest of the pool, including every recruiter edit, is
untouched. That is deliberately unlike the Interview Designer's regeneration,
which rewrites the assessment structure and therefore has to be destructive.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packages.types import InterviewDefinition, new_id
from packages.types.definition import CriterionSpec, QuestionSpec
from services.assessment import blueprint as bp
from services.security import ratelimit
from services.assessment import pool as pool_service
from services.assessment import preview as preview_service
from services.assessment.blueprint import Blueprint, BlueprintError
from services.assessment.validation import (
    coverage_summary,
    validate_question,
    validate_question_pool_coverage,
)
from services.data import audit, interviews, versions
from services.api import progress
from services.data.interviews import InterviewConfig

router = APIRouter(tags=["questions"])


# --------------------------------------------------------------------------- #
#  Requests
# --------------------------------------------------------------------------- #
class GenerateQuestions(BaseModel):
    #: Retry a subset. Empty means "fill the whole blueprint".
    slot_ids: list[str] | None = None


class CriterionInput(BaseModel):
    id: str | None = None
    label: str
    description: str = ""
    importance: str = "medium"


class QuestionInput(BaseModel):
    question_text: str
    primary_skill_id: str
    secondary_skill_ids: list[str] = []
    task_id: str = ""
    question_type: str = "situational"
    difficulty: str = "medium"
    looking_for: list[str] = []
    evaluation_criteria: list[CriterionInput] = []
    probe_eligible: bool = True
    probe_bank: list[str] = []
    clarify: str = ""
    time_budget_sec: int | None = None


class QuestionPatch(BaseModel):
    question_text: str | None = None
    primary_skill_id: str | None = None
    secondary_skill_ids: list[str] | None = None
    task_id: str | None = None
    question_type: str | None = None
    difficulty: str | None = None
    looking_for: list[str] | None = None
    evaluation_criteria: list[CriterionInput] | None = None
    probe_eligible: bool | None = None
    probe_bank: list[str] | None = None
    clarify: str | None = None
    time_budget_sec: int | None = None


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _load(interview_id: str) -> InterviewConfig:
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    return cfg


def _definition(cfg: InterviewConfig) -> InterviewDefinition:
    return interviews.build_definition(cfg)


def _plan(cfg: InterviewConfig, definition: InterviewDefinition) -> Blueprint:
    """The stored blueprint, or a fresh one if the design has moved on.

    Rebuilt when the stored plan no longer matches the skills — a recruiter who
    added a skill after generating should see a slot for it, not a plan that
    quietly pretends the skill is not there.
    """
    stored = Blueprint.from_dict(cfg.blueprint) if cfg.blueprint else None
    if stored and {c.skill_id for c in stored.coverage} == {s.id for s in definition.skills}:
        return stored
    try:
        return bp.build(definition)
    except BlueprintError as exc:
        raise HTTPException(422, detail={"errors": {"design": str(exc)}}) from exc


def _persist(cfg: InterviewConfig, questions: list[QuestionSpec], plan: Blueprint) -> None:
    from dataclasses import asdict

    cfg.questions = [asdict(q) for q in questions]
    cfg.blueprint = plan.to_dict()
    # One budget, not two. The blueprint derives how many questions fit in the
    # duration; the definition's runtime limit is what selection actually obeys.
    # Left unsynchronised, the running-order preview showed six questions while
    # the blueprint above it said three — the same interview described two ways
    # on one screen.
    cfg.question_budget = plan.live_item_budget
    interviews.save(cfg)
    versions.save_draft(cfg.id, interviews.build_definition(cfg), notes="Question pool")


def _question_payload(q: QuestionSpec, definition: InterviewDefinition) -> dict[str, Any]:
    skills = {s.id: s.name for s in definition.skills}
    task = next((t for t in definition.tasks if t.id == q.task_id), None)
    return {
        "question_id": q.id,
        "prompt": q.question_text,
        "question_type": q.question_type,
        "difficulty": q.difficulty,
        "primary_skill": {"id": q.skill_id, "name": skills.get(q.skill_id, q.skill_id)},
        "secondary_skills": [
            {"id": s, "name": skills.get(s, s)} for s in q.secondary_skill_ids
        ],
        "task": {"id": task.id, "name": task.label} if task else None,
        "expected_signal": q.expected_signal,
        "looking_for": list(q.looking_for),
        "evaluation_criteria": [
            {"criterion_id": c.id, "label": c.label,
             "description": c.description, "importance": c.importance}
            for c in q.evaluation_criteria
        ],
        "probe_eligible": q.probe_eligible,
        "probe_bank": list(q.probe_bank),
        "clarify": q.clarify,
        "estimated_base_answer_sec": q.time_budget_sec,
        "source": q.source,
        "slot_id": q.slot_id,
    }


def _pool_payload(cfg: InterviewConfig) -> dict[str, Any]:
    definition = _definition(cfg)
    plan = _plan(cfg, definition) if definition.skills else None
    coverage = (
        validate_question_pool_coverage(definition, plan) if plan else None
    )
    skills = {s.id: s for s in definition.skills}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for q in definition.questions:
        grouped.setdefault(q.skill_id, []).append(_question_payload(q, definition))

    return {
        "interview_id": cfg.id,
        "role_title": cfg.role_title or cfg.title,
        "generated": bool(definition.questions),
        "summary": coverage_summary(definition, plan) if plan else {},
        "coverage": coverage.as_dict() if coverage else {"ok": False, "problems": []},
        "blueprint": {
            "slots": [s.to_dict() for s in plan.slots],
            "pool_size": plan.pool_size,
            "live_item_budget": plan.live_item_budget,
            "target_duration_min": plan.target_duration_min,
        } if plan else None,
        # Grouped by skill: a recruiter reviewing a pool is checking whether
        # each skill is properly covered, not reading a flat list of forty.
        "groups": [
            {
                "skill_id": sid,
                "skill_name": skills[sid].name if sid in skills else sid,
                "priority": skills[sid].priority if sid in skills else "medium",
                "assessment_scope": skills[sid].assessment_scope if sid in skills else "",
                "questions": qs,
            }
            for sid, qs in sorted(
                grouped.items(),
                key=lambda kv: (
                    {"high": 0, "medium": 1, "low": 2}.get(
                        skills[kv[0]].priority if kv[0] in skills else "medium", 1
                    ),
                    skills[kv[0]].name if kv[0] in skills else kv[0],
                ),
            )
        ],
        "running_order": preview_service.running_order(definition),
        "questions_generated_at": cfg.questions_generated_at,
    }


def _spec_from_input(
    body: QuestionInput, definition: InterviewDefinition, question_id: str = ""
) -> QuestionSpec:
    return QuestionSpec(
        id=question_id or new_id("q"),
        question_text=body.question_text.strip(),
        competency=body.primary_skill_id,
        skill_id=body.primary_skill_id,
        secondary_skill_ids=list(body.secondary_skill_ids),
        task_id=body.task_id,
        question_type=body.question_type,
        difficulty=body.difficulty,
        looking_for=[c.strip() for c in body.looking_for if c.strip()],
        evaluation_criteria=[
            CriterionSpec(
                id=c.id or new_id("crit"), label=c.label.strip(),
                description=c.description.strip(), importance=c.importance,
            )
            for c in body.evaluation_criteria if c.label.strip()
        ],
        probe_eligible=body.probe_eligible,
        probe_bank=[p.strip() for p in body.probe_bank if p.strip()],
        clarify=body.clarify.strip(),
        time_budget_sec=body.time_budget_sec or 90,
        source="manual",
    )


def _slots_below_floor(questions: list[QuestionSpec], plan: Blueprint) -> list[str]:
    """Slot ids belonging to skills that ended up under their coverage floor.

    The same question the publish check asks, asked early enough to do
    something about it. Every slot for a short skill is returned, because the
    generator regenerates a slot's whole batch and needs to see the skill's
    other questions to avoid writing a near-duplicate of one.
    """
    counts: dict[str, int] = {}
    for q in questions:
        counts[q.skill_id] = counts.get(q.skill_id, 0) + 1
    short = {
        target.skill_id for target in plan.coverage
        if counts.get(target.skill_id, 0) < target.min_items
    }
    return [slot.id for slot in plan.slots if slot.skill_id in short]


async def write_questions(
    cfg: InterviewConfig,
    *,
    only_slots: list[str] | None = None,
    progress_token: str = "",
) -> tuple[int, Any]:
    """Fill the blueprint from the approved design. Returns (written, report).

    Shared by the endpoint and by interview creation. It is the same call
    either way — a design without questions is not something anyone can
    publish, so generating them as part of the recommendation is completing the
    job rather than doing the recruiter a favour.

    Returns `(0, report)` rather than raising when nothing could be written:
    the caller decides whether that is a failed request or a draft the
    recruiter can retry from.
    """
    definition = _definition(cfg)
    if not definition.skills or not definition.tasks:
        return 0, None

    plan = _plan(cfg, definition)
    audit.product(
        audit.QUESTION_GENERATION_STARTED, actor="recruiter",
        subject_type="interview", subject_id=cfg.id,
        slots=len(only_slots or plan.slots), pool_size=plan.pool_size,
        stub=pool_service.stub_enabled(),
    )
    # Real per-slot progress. `pool.generate` already calls back as each slot
    # finishes, so the recruiter's screen can count actual questions written
    # rather than animate a bar. The callback runs on the worker thread, and
    # `progress.update` is lock-guarded and swallows an unknown token, so it
    # cannot fail the generation it is describing.
    slots_total = len(only_slots or plan.slots)
    progress.update(
        progress_token, stage="writing_questions", done=0, total=slots_total,
        detail="Writing the questions",
    )
    written_slots = 0

    def _slot_done(outcome: Any) -> None:
        nonlocal written_slots
        written_slots += 1
        progress.update(progress_token, done=written_slots, total=slots_total)

    questions, report = await asyncio.to_thread(
        pool_service.generate, definition, plan,
        job_description=cfg.jd_text, only_slots=only_slots,
        on_slot=_slot_done if progress_token else None,
    )
    if not questions:
        audit.product(
            audit.QUESTION_GENERATION_FAILED, actor="recruiter",
            subject_type="interview", subject_id=cfg.id,
            failed_slots=len(report.failed_slots), rejected=len(report.rejected),
            latency_ms=report.latency_ms,
        )
        return 0, report

    cfg.questions_generated_at = time.time()
    _persist(cfg, questions, plan)

    # --- one retry for the skills that came back under their floor ---------
    #
    # A slot asks for two questions in one batch and the model sometimes
    # returns one, or the second is rejected as a near-duplicate. Nothing
    # noticed, so the shortfall surfaced at publication as "Roadmap
    # Prioritization has 1 question(s) but needs at least 2" — after the
    # recruiter had reviewed everything and pressed publish.
    #
    # Two things make this correct, and getting either wrong doubles the pool:
    #
    #   * `generate(only_slots=…)` returns the COMPLETE pool — the questions it
    #     kept from untouched slots, plus the ones it just wrote. So its result
    #     replaces `questions`; it is not something to append.
    #   * `keeping` is read from the definition, so the first pass has to be
    #     persisted before the retry runs. Against a stale definition the retry
    #     would keep the pool as it was BEFORE this generation.
    #
    # Bounded at one extra pass: a slot that fails twice has a problem the
    # publish check should report in language a recruiter can act on, rather
    # than one this loop should keep paying a provider to rediscover.
    if only_slots is None:
        short = _slots_below_floor(questions, plan)
        if short:
            audit.product(
                audit.QUESTION_GENERATION_STARTED, actor="recruiter",
                subject_type="interview", subject_id=cfg.id,
                slots=len(short), retry=True, stub=pool_service.stub_enabled(),
            )
            complete, retry_report = await asyncio.to_thread(
                pool_service.generate, _definition(cfg), plan,
                job_description=cfg.jd_text, only_slots=short,
            )
            if complete:
                questions, report = complete, retry_report
                _persist(cfg, questions, plan)

    audit.product(
        audit.QUESTION_GENERATED, actor="recruiter",
        subject_type="interview", subject_id=cfg.id,
        version=versions.DRAFT_VERSION, workload="question_generator",
        generated=report.generated, failed_slots=len(report.failed_slots),
        rejected=len(report.rejected), latency_ms=report.latency_ms,
        # On the COMPLETION event, not only on the start: a pool written by the
        # stub has to be distinguishable from one a real model wrote, and the
        # row that says the questions exist is the one anyone reads.
        stub=pool_service.stub_enabled(),
    )
    return len(questions), report


# --------------------------------------------------------------------------- #
#  Endpoints
# --------------------------------------------------------------------------- #
@router.post("/interviews/{interview_id}/questions/generate")
async def generate_questions(
    interview_id: str, body: GenerateQuestions,
    _: None = Depends(ratelimit.limiter("generation")),
) -> dict[str, Any]:
    """Fill the blueprint from the approved design.

    Passing `slot_ids` retries just those slots and leaves everything else —
    including every recruiter edit — exactly as it is.
    """
    cfg = _load(interview_id)
    definition = _definition(cfg)

    if not definition.skills or not definition.tasks:
        raise HTTPException(
            422,
            detail={"errors": {"design": (
                "Generate and review the skills and tasks first — questions are written "
                "from them."
            )}},
        )

    written, report = await write_questions(cfg, only_slots=body.slot_ids)
    if not written:
        raise HTTPException(
            502,
            detail={
                "message": (
                    "Tara couldn't write questions for this interview. Nothing has been "
                    "saved — try again, and if it keeps failing, check that the skills "
                    "have a clear assessment scope."
                ),
                "retryable": True,
                "failed_slots": report.failed_slots if report else [],
            },
        )
    cfg = _load(interview_id)
    payload = _pool_payload(cfg)
    payload["report"] = report.as_dict()
    return payload


@router.get("/interviews/{interview_id}/questions")
async def read_questions(interview_id: str) -> dict[str, Any]:
    return _pool_payload(_load(interview_id))


@router.post("/interviews/{interview_id}/questions", status_code=201)
async def add_question(interview_id: str, body: QuestionInput) -> dict[str, Any]:
    """Add one by hand.

    Held to exactly the same validation as a generated question. A hand-written
    question without a skill mapping, expected signals and a rubric is one the
    runtime cannot classify and the scoring engine cannot judge — so it is not
    a question this system can accept, whoever wrote it.
    """
    cfg = _load(interview_id)
    definition = _definition(cfg)
    spec = _spec_from_input(body, definition)

    verdict = validate_question(spec, definition, existing=definition.questions)
    if not verdict.ok:
        raise HTTPException(422, detail={"errors": {"question": verdict.messages}})

    from dataclasses import asdict

    cfg.questions.append(asdict(spec))
    plan = _plan(cfg, definition)
    _persist(cfg, [*definition.questions, spec], plan)
    audit.product(
        audit.QUESTION_ADDED, actor="recruiter", subject_type="interview",
        subject_id=cfg.id, question_id=spec.id, skill_id=spec.skill_id,
    )
    return _pool_payload(cfg)


@router.patch("/interviews/{interview_id}/questions/{question_id}")
async def edit_question(
    interview_id: str, question_id: str, body: QuestionPatch
) -> dict[str, Any]:
    """Edit one. Human-edited content is not automatically trusted."""
    cfg = _load(interview_id)
    definition = _definition(cfg)
    target = next((q for q in definition.questions if q.id == question_id), None)
    if target is None:
        raise HTTPException(404, "No such question.")

    changed: list[str] = []
    for field_name in (
        "question_text", "task_id", "question_type", "difficulty", "clarify",
        "probe_eligible", "time_budget_sec",
    ):
        value = getattr(body, field_name)
        if value is not None:
            setattr(target, field_name, value.strip() if isinstance(value, str) else value)
            changed.append(field_name)
    if body.primary_skill_id is not None:
        target.skill_id = target.competency = body.primary_skill_id
        changed.append("primary_skill_id")
    if body.secondary_skill_ids is not None:
        target.secondary_skill_ids = list(body.secondary_skill_ids)
        changed.append("secondary_skill_ids")
    if body.looking_for is not None:
        target.looking_for = [c.strip() for c in body.looking_for if c.strip()]
        changed.append("looking_for")
    if body.probe_bank is not None:
        target.probe_bank = [p.strip() for p in body.probe_bank if p.strip()]
        changed.append("probe_bank")
    if body.evaluation_criteria is not None:
        target.evaluation_criteria = [
            CriterionSpec(
                id=c.id or new_id("crit"), label=c.label.strip(),
                description=c.description.strip(), importance=c.importance,
            )
            for c in body.evaluation_criteria if c.label.strip()
        ]
        changed.append("evaluation_criteria")

    verdict = validate_question(
        target, definition,
        existing=[q for q in definition.questions if q.id != question_id],
    )
    if not verdict.ok:
        raise HTTPException(422, detail={"errors": {"question": verdict.messages}})

    plan = _plan(cfg, definition)
    _persist(cfg, definition.questions, plan)
    audit.product(
        audit.QUESTION_EDITED, actor="recruiter", subject_type="interview",
        subject_id=cfg.id, question_id=question_id, changed=changed,
    )
    return _pool_payload(cfg)


@router.delete("/interviews/{interview_id}/questions/{question_id}")
async def remove_question(interview_id: str, question_id: str) -> dict[str, Any]:
    """Remove one — unless it would drop a skill below its floor.

    The backend is authoritative here. A recruiter tidying the pool should not
    be able to leave a high-priority skill resting on one answer without being
    told, and the screen showing the warning is not the thing enforcing it.
    """
    cfg = _load(interview_id)
    definition = _definition(cfg)
    if not any(q.id == question_id for q in definition.questions):
        raise HTTPException(404, "No such question.")

    remaining = [q for q in definition.questions if q.id != question_id]
    plan = _plan(cfg, definition)

    trial = InterviewDefinition.from_dict(definition.to_dict())
    trial.questions = remaining
    verdict = validate_question_pool_coverage(trial, plan)
    fatal = [p for p in verdict.problems if p.fatal]
    if fatal:
        raise HTTPException(
            409,
            detail={
                "message": "Removing that question would leave the interview short.",
                "problems": [p.message for p in fatal],
            },
        )

    _persist(cfg, remaining, plan)
    audit.product(
        audit.QUESTION_REMOVED, actor="recruiter", subject_type="interview",
        subject_id=cfg.id, question_id=question_id,
    )
    return _pool_payload(cfg)


@router.post("/interviews/{interview_id}/questions/{question_id}/regenerate")
async def regenerate_one(interview_id: str, question_id: str) -> dict[str, Any]:
    """Rewrite ONE question against its own blueprint slot.

    Same skill, same task, same difficulty, same coverage requirement. Every
    other question — and every edit made to them — is untouched. If the
    replacement cannot be produced or fails validation, the original stays:
    a regeneration that fails must not leave a hole in the pool.
    """
    cfg = _load(interview_id)
    definition = _definition(cfg)
    plan = _plan(cfg, definition)

    try:
        questions, report = await asyncio.to_thread(
            pool_service.regenerate_question,
            definition, plan, question_id, job_description=cfg.jd_text,
        )
    except LookupError:
        raise HTTPException(404, "No such question.") from None

    if not report.generated:
        audit.product(
            audit.QUESTION_GENERATION_FAILED, actor="recruiter",
            subject_type="interview", subject_id=cfg.id, question_id=question_id,
            failed_slots=len(report.failed_slots),
        )
        raise HTTPException(
            502,
            detail={
                "message": (
                    "Tara couldn't write a replacement for that question. The original "
                    "is still here — nothing was lost."
                ),
                "retryable": True,
                "problems": [
                    m for r in report.rejected for m in r.get("problems", [])
                ][:3],
            },
        )

    _persist(cfg, questions, plan)
    audit.product(
        audit.QUESTION_REGENERATED, actor="recruiter", subject_type="interview",
        subject_id=cfg.id, question_id=question_id, workload="question_generator",
        latency_ms=report.latency_ms,
    )
    return _pool_payload(cfg)


@router.get("/interviews/{interview_id}/questions/validate")
async def validate_pool(interview_id: str) -> dict[str, Any]:
    """Run the coverage validator. This becomes the publication gate."""
    cfg = _load(interview_id)
    definition = _definition(cfg)
    plan = _plan(cfg, definition)
    verdict = validate_question_pool_coverage(definition, plan)
    audit.product(
        audit.QUESTION_POOL_VALIDATED, actor="recruiter", subject_type="interview",
        subject_id=cfg.id, ok=verdict.ok, problems=len(verdict.problems),
    )
    return {
        "coverage": verdict.as_dict(),
        "summary": coverage_summary(definition, plan),
        "preview": preview_service.coverage_preview(definition),
    }
