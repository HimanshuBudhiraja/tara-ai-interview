"""The gate an evaluation has to pass before it is allowed to be a record.

Phase 08 already validates evidence as it comes out of the extractor. This runs
the same deterministic checks again at the persistence boundary, plus the ones
that only make sense once a whole evaluation exists, and it is deliberately not
a formality:

    extractor  ─►  evidence.validate_evidence  ─►  evaluator  ─►  THIS  ─►  disk

The reason for checking twice is that these are different questions. The first
asks "did the model make this quote up?". The second asks "is this object safe
to keep, serve to a recruiter, and put in front of a hiring decision?" — and it
is asked against the frozen snapshot, so it holds whatever path the object took
to get here, including a future caller that assembles evidence some other way.

Nothing here repairs an evaluation. A violation means the run failed: an
apparently valid score that quietly had a rule applied to it after the fact is
worse than a failure someone can see.
"""
from __future__ import annotations

from typing import Any

from packages.types.evaluation import (
    CRITERIA,
    UNRATED,
    Coverage,
    DIMENSIONS,
    RECOMMENDATIONS,
    STAGES,
    Evaluation,
    EvidenceItem,
    overall_rating,
    stage_index,
)
from services.evaluation import evidence as EV
from services.evaluation import evaluator as E

EVIDENCE_TYPES = ("supported", "partial", "contradicted", "missing", "unclear")
STRENGTHS = ("strong", "moderate", "weak")
DISCUSSION_STATUSES = ("discussed", "mentioned", "not_discussed")
NOT_DISCUSSED_REMARK = "Not discussed in interview"


class IntegrityError(RuntimeError):
    """An evaluation that must not be persisted. Carries every reason."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations[:6]) or "integrity check failed")
        self.violations = violations


# --------------------------------------------------------------------------- #
#  Evidence
# --------------------------------------------------------------------------- #
def check_evidence(
    items: list[EvidenceItem], snap: dict[str, Any]
) -> tuple[list[EvidenceItem], list[dict[str, str]]]:
    """Evidence that may be kept, and everything quarantined with its reason.

    Quarantine rather than failure: one bad item is the extractor being sloppy
    about one quote, not a reason to throw away an interview. The evaluation
    then runs on what survived, and what did not survive stays visible in the
    record so a thin-looking skill can be explained.
    """
    turns = {t["turn_id"]: t for t in snap.get("turns", [])}
    skills = {s["id"] for s in snap.get("skills", [])}
    questions = {q["id"]: q for q in snap.get("questions", [])}
    task_skills = {t["id"]: set(t.get("skill_ids", [])) for t in snap.get("tasks", [])}

    kept: list[EvidenceItem] = []
    rejected: list[dict[str, str]] = []

    def drop(reason: str, item: EvidenceItem) -> None:
        rejected.append({"reason": reason, "quote": item.candidate_quote[:80],
                         "skill_id": item.skill_id, "turn_id": item.turn_id})

    for item in items:
        turn = turns.get(item.turn_id)
        if turn is None:
            drop("turn is not in the evaluated snapshot", item)
            continue
        if turn.get("flagged") or not (turn.get("answer") or "").strip():
            drop(f"turn is not usable ({turn.get('flag_reason') or 'empty'})", item)
            continue
        if item.skill_id not in skills:
            drop("skill is not in the published version", item)
            continue
        if item.question_id not in questions:
            drop("question is not in the published version", item)
            continue
        if turn.get("question_id") != item.question_id:
            drop("evidence attributes a turn to the wrong question", item)
            continue
        if item.task_id and item.task_id not in task_skills:
            drop("task is not in the published version", item)
            continue
        if item.task_id and item.skill_id not in task_skills[item.task_id]:
            drop("the published task does not assess this skill", item)
            continue
        if item.depth_stage not in STAGES:
            drop(f"unknown depth stage {item.depth_stage!r}", item)
            continue
        if item.depth_stage != turn.get("depth_stage"):
            # The rung a turn sat on is the runtime's fact. Evidence that
            # disagrees with it is claiming the conversation went differently.
            drop("depth stage does not match the turn it came from", item)
            continue
        if item.depth_dimension not in DIMENSIONS:
            drop(f"unknown depth dimension {item.depth_dimension!r}", item)
            continue
        if item.evidence_type not in EVIDENCE_TYPES:
            drop(f"unknown evidence type {item.evidence_type!r}", item)
            continue
        if item.evidence_strength not in STRENGTHS:
            drop(f"unknown evidence strength {item.evidence_strength!r}", item)
            continue
        if item.supports_criterion not in CRITERIA:
            drop(f"unknown criterion {item.supports_criterion!r}", item)
            continue
        if not EV.quote_is_real(item.candidate_quote, turn.get("answer", "")):
            drop("quote is not in the transcript", item)
            continue
        protected = EV.mentions_protected_topic(item.candidate_quote)
        if protected:
            drop(f"quote touches {protected}", item)
            continue
        if item.note and EV.mentions_protected_topic(item.note):
            drop(f"note touches {EV.mentions_protected_topic(item.note)}", item)
            continue
        kept.append(item)

    return kept, rejected


# --------------------------------------------------------------------------- #
#  The evaluation object
# --------------------------------------------------------------------------- #
def check_evaluation(
    evaluation: Evaluation, evidence: list[EvidenceItem], snap: dict[str, Any]
) -> list[str]:
    """Every violation found. Empty means the object is safe to persist."""
    violations: list[str] = []
    skills = {s["id"]: s for s in snap.get("skills", [])}
    by_skill: dict[str, list[EvidenceItem]] = {}
    for item in evidence:
        by_skill.setdefault(item.skill_id, []).append(item)

    seen: set[str] = set()
    for row in evaluation.skill_assessment:
        name = row.skill_name or row.skill_id
        if row.skill_id and row.skill_id not in skills:
            violations.append(f"{name}: not a skill in the published version")
        if row.skill_id in seen:
            violations.append(f"{name}: assessed more than once")
        seen.add(row.skill_id)

        if row.discussion_status not in DISCUSSION_STATUSES:
            violations.append(f"{name}: invalid discussion status {row.discussion_status!r}")

        criteria = row.criteria()
        for criterion, value in criteria.items():
            if not isinstance(value, int) or not 0 <= value <= 5:
                violations.append(f"{name}: {criterion} is {value!r}, outside 0-5")
        if row.score != sum(criteria.values()):
            violations.append(
                f"{name}: score {row.score} is not the sum of its criteria"
            )

        # The deterministic contract, checked rather than trusted.
        if row.discussion_status == "not_discussed":
            if any(criteria.values()):
                violations.append(f"{name}: not_discussed but scored above zero")
            if row.remarks != NOT_DISCUSSED_REMARK:
                violations.append(
                    f"{name}: not_discussed remarks must be exactly "
                    f"{NOT_DISCUSSED_REMARK!r}"
                )
        elif row.discussion_status == "mentioned":
            if max(criteria.values(), default=0) > 1:
                violations.append(f"{name}: mentioned but a criterion scored above 1")
        elif row.discussion_status == "discussed":
            if min(criteria.values(), default=0) < 1:
                violations.append(f"{name}: discussed but a criterion scored zero")

        depth = row.depth_evaluation
        if depth.depth_reached not in STAGES:
            violations.append(f"{name}: invalid depth_reached {depth.depth_reached!r}")
        if depth.depth_demonstrated not in STAGES:
            violations.append(
                f"{name}: invalid depth_demonstrated {depth.depth_demonstrated!r}"
            )
        if depth.depth_reached in STAGES and depth.depth_reached != snap.get(
            "depth_reached", {}
        ).get(row.skill_id, depth.depth_reached):
            violations.append(
                f"{name}: depth_reached disagrees with the frozen conversation"
            )
        # The ceiling. Code decides what the evidence supports; nothing
        # downstream may claim more than that, whatever the model returned.
        supported = E.depth_demonstrated_from(by_skill.get(row.skill_id, []))
        if depth.depth_demonstrated in STAGES and stage_index(
            depth.depth_demonstrated
        ) > stage_index(supported):
            violations.append(
                f"{name}: depth_demonstrated {depth.depth_demonstrated!r} is above "
                f"what the validated evidence supports ({supported!r})"
            )
        if any(d not in DIMENSIONS for d in depth.dimensions_demonstrated):
            violations.append(f"{name}: unknown dimension in dimensions_demonstrated")

        for label, text in (("remarks", row.remarks),):
            protected = EV.mentions_protected_topic(text)
            if protected:
                violations.append(f"{name}: {label} touch {protected}")
            if E._NUMBER_IN_REMARKS.search(text or ""):
                violations.append(f"{name}: {label} contain a numeric score")

    # Aggregation is arithmetic, so it is checkable. The score is over DISCUSSED
    # skills only; mentioned and not-discussed skills are reported as coverage
    # and contribute to neither side of the fraction.
    assessed = [r for r in evaluation.skill_assessment if r.discussion_status == "discussed"]
    total = sum(r.score for r in assessed)
    maximum = 5 * len(CRITERIA) * len(assessed)
    if evaluation.candidate_details.total_score != total:
        violations.append(
            "total_score is not the sum of the substantively discussed skills")
    if evaluation.maximum_possible_score != maximum:
        violations.append(
            "maximum_possible_score is not 25 per discussed skill")
    expected_pct = round((total / maximum * 100) if maximum else 0.0, 1)
    if abs(evaluation.percentage - expected_pct) > 0.05:
        violations.append("percentage does not follow from the total")
    expected_rating = overall_rating(expected_pct) if assessed else UNRATED
    if evaluation.candidate_details.overall_rating != expected_rating:
        violations.append(
            "overall_rating does not follow from the percentage"
            if assessed else
            "nothing was substantively discussed, so there is no rating to give")

    # Coverage is a count of statuses. It cannot disagree with the rows.
    expected_coverage = Coverage.of(
        [r.discussion_status for r in evaluation.skill_assessment])
    if evaluation.coverage.to_dict() != expected_coverage.to_dict():
        violations.append(
            f"coverage does not match the skill rows: {evaluation.coverage.to_dict()} "
            f"!= {expected_coverage.to_dict()}")
    if evaluation.recommendation not in RECOMMENDATIONS:
        violations.append(f"recommendation {evaluation.recommendation!r} is not allowed")

    for label, texts in (
        ("strengths", evaluation.strengths),
        ("areas_for_improvement", evaluation.areas_for_improvement),
        ("recommendation_explaination", [evaluation.recommendation_explaination]),
    ):
        for text in texts:
            protected = EV.mentions_protected_topic(text or "")
            if protected:
                violations.append(f"{label} touch {protected}")

    return violations


def require_persistable(
    evaluation: Evaluation, evidence: list[EvidenceItem], snap: dict[str, Any]
) -> None:
    violations = check_evaluation(evaluation, evidence, snap)
    if violations:
        raise IntegrityError(violations)
