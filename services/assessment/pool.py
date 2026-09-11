"""Generating and maintaining the question pool on a draft.

The unit of work is a **blueprint slot**, not the interview. One slot fails, one
slot is retried; the rest of the pool — including everything the recruiter has
already edited — is untouched. That is the difference between this and the
Interview Designer's regeneration, which is destructive by design because it
rewrites the assessment structure itself.

Nothing here publishes. Generation and every edit write the draft version.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from packages.types import InterviewDefinition, QuestionSpec
from services.assessment import blueprint as bp
from services.assessment.blueprint import Blueprint, Slot
from services.assessment.validation import Verdict, validate_question
from services.ai.workloads import question_writer

#: Turning the stub on is an explicit choice. With it off and no provider, a
#: generation attempt fails retryably rather than quietly inventing content —
#: fake success in an assessment tool is worse than an outage.
STUB_ENV = "TARA_QUESTION_STUB"


def stub_enabled() -> bool:
    return os.environ.get(STUB_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _writer() -> Callable[..., question_writer.SlotResult]:
    if stub_enabled():
        from services.ai.workloads import question_writer_stub

        return question_writer_stub.generate_slot
    return question_writer.generate_slot


@dataclass
class SlotOutcome:
    slot: Slot
    questions: list[QuestionSpec] = field(default_factory=list)
    rejected: list[tuple[QuestionSpec, Verdict]] = field(default_factory=list)
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.rejected and not self.questions:
            return "invalid"
        return "generated" if self.questions else "failed"


@dataclass
class GenerationReport:
    generated: int = 0
    failed_slots: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "failed_slots": self.failed_slots,
            "rejected": self.rejected,
            "latency_ms": self.latency_ms,
        }


def _run_slot(
    slot: Slot,
    definition: InterviewDefinition,
    job_description: str,
    accepted_so_far: list[QuestionSpec],
) -> SlotOutcome:
    """Generate one slot and keep only what survives validation."""
    try:
        result = _writer()(slot, definition, job_description=job_description)
    except Exception as exc:  # noqa: BLE001 — one slot must not take the run down
        return SlotOutcome(slot, error=str(exc)[:300])

    if result.error:
        return SlotOutcome(slot, error=result.error)

    kept: list[QuestionSpec] = []
    rejected: list[tuple[QuestionSpec, Verdict]] = []
    for question in result.questions:
        # Validated against everything accepted so far, so two slots cannot
        # both produce the same question.
        verdict = validate_question(
            question, definition, existing=[*accepted_so_far, *kept]
        )
        if verdict.ok:
            kept.append(question)
        else:
            rejected.append((question, verdict))
    return SlotOutcome(slot, questions=kept, rejected=rejected)


def generate(
    definition: InterviewDefinition,
    plan: Blueprint,
    *,
    job_description: str = "",
    only_slots: list[str] | None = None,
    on_slot: Callable[[SlotOutcome], None] | None = None,
) -> tuple[list[QuestionSpec], GenerationReport]:
    """Fill the blueprint, slot by slot.

    Returns the questions that passed validation and a report of what did not.
    A slot that fails is recorded and retryable; the pool keeps whatever the
    other slots produced, because throwing away eleven good questions because
    the twelfth failed helps nobody.
    """
    started = time.perf_counter()
    report = GenerationReport()

    # Questions already on the definition that belong to slots we are NOT
    # regenerating. They stay exactly as they are, edits included.
    keeping = (
        [q for q in definition.questions if q.slot_id not in set(only_slots)]
        if only_slots else []
    )
    accepted: list[QuestionSpec] = list(keeping)

    slots = [s for s in plan.slots if not only_slots or s.id in set(only_slots)]
    for slot in slots:
        outcome = _run_slot(slot, definition, job_description, accepted)
        slot.status = outcome.status
        slot.error = outcome.error
        slot.question_ids = [q.id for q in outcome.questions]

        accepted.extend(outcome.questions)
        report.generated += len(outcome.questions)
        if outcome.error:
            report.failed_slots.append({
                "slot_id": slot.id,
                "skill": slot.skill_name,
                "difficulty": slot.difficulty,
                "error": outcome.error,
            })
        for question, verdict in outcome.rejected:
            report.rejected.append({
                "slot_id": slot.id,
                "skill": slot.skill_name,
                "question": question.question_text[:120],
                "problems": verdict.messages,
            })
        if on_slot:
            on_slot(outcome)

    report.latency_ms = int((time.perf_counter() - started) * 1000)
    return accepted, report


def regenerate_question(
    definition: InterviewDefinition,
    plan: Blueprint,
    question_id: str,
    *,
    job_description: str = "",
) -> tuple[list[QuestionSpec], GenerationReport]:
    """Rewrite ONE question against its own blueprint slot.

    The slot is preserved — same skill, same task, same difficulty, same
    coverage requirement — unless the recruiter has explicitly changed those.
    Every other question, including every edit made to them, is left alone.
    """
    target = next((q for q in definition.questions if q.id == question_id), None)
    if target is None:
        raise LookupError(question_id)

    slot = plan.slot(target.slot_id) if target.slot_id else None
    if slot is None:
        # A question added by hand, or one whose slot has gone. Rebuild a slot
        # from the question's own mapping so regeneration still means "another
        # question like this one" rather than "a question about anything".
        slot = Slot(
            id=target.slot_id or f"slot_adhoc_{question_id}",
            skill_id=target.skill_id,
            skill_name=(definition.skill(target.skill_id).name
                        if definition.skill(target.skill_id) else ""),
            task_id=target.task_id,
            question_type=target.question_type,
            difficulty=target.difficulty,
            count=1,
        )

    others = [q for q in definition.questions if q.id != question_id]
    outcome = _run_slot(slot, definition, job_description, others)

    report = GenerationReport()
    if outcome.error or not outcome.questions:
        report.failed_slots.append({
            "slot_id": slot.id,
            "skill": slot.skill_name,
            "error": outcome.error or "the replacement question failed validation",
        })
        for question, verdict in outcome.rejected:
            report.rejected.append({
                "slot_id": slot.id,
                "question": question.question_text[:120],
                "problems": verdict.messages,
            })
        # The original stays. A regeneration that fails must not leave a hole.
        return list(definition.questions), report

    replacement = outcome.questions[0]
    replacement.slot_id = slot.id
    report.generated = 1

    # Replaced in place, so the pool's order — and the recruiter's sense of
    # where they were on the page — does not shuffle.
    return [
        replacement if q.id == question_id else q for q in definition.questions
    ], report
