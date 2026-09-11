"""The assessment result: one object a recruiter screen can render whole.

The evaluation record already holds everything — the frozen snapshot, the skill
rows, the validated evidence. What it does not hold is the shape a reader needs:

    Overall  ─►  Skills  ─►  Questions  ─►  Turns  ─►  Evidence  ─►  Criterion

Assembling that here rather than in the browser is the point. Every number in
the result is either read from the persisted evaluation or counted from the
frozen snapshot; nothing is recomputed from a different source and nothing is
asked of a model. A report that derives its own totals is a second scoring
system that will eventually disagree with the first, and the disagreement will
surface in front of a candidate.

Three things this module is careful about:

  * **The snapshot is the only source of interview truth.** Questions, tasks,
    turns and the interview's configuration all come from
    `record.snapshot`, never from the live draft — so a recruiter editing the
    draft next week cannot change what a completed assessment says.

  * **Score and coverage are different questions.** The score is over skills
    that were substantively discussed; coverage reports how many that was out
    of the whole assessment. Neither is folded into the other.

  * **A contradictory result is refused, not shown.** `validate` re-derives the
    arithmetic and the contract's rules from the parts. If they disagree, the
    caller gets an error rather than a page that quietly says two things.
"""
from __future__ import annotations

from typing import Any

from packages.types.evaluation import (
    CRITERIA,
    ENGINE_VERSION,
    DIMENSIONS,
    RECOMMENDATIONS,
    RESULT_CONTRACT_VERSION,
    STAGES,
    UNRATED,
    Coverage,
    EvidenceItem,
    overall_rating,
)
from services.data.evaluations import EvaluationRecord
from services.evaluation import evaluator as E

#: The most any one skill can score: five criteria, five points each. A shape
#: fact from the contract, sent to clients so nothing has to hardcode 25.
SKILL_MAX_SCORE = 5 * len(CRITERIA)

#: What each discussion status permits per criterion. Sent alongside the scores
#: so a reader knows what was achievable rather than inferring it: a `mentioned`
#: row showing 1/5 is at its ceiling, not one point off a good answer.
CRITERION_CEILING: dict[str, int] = {"discussed": 5, "mentioned": 1, "not_discussed": 0}


class ResultError(RuntimeError):
    """The result contradicts itself and must not be shown."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations[:6]) or "result failed validation")
        self.violations = violations


# --------------------------------------------------------------------------- #
#  Assembly
# --------------------------------------------------------------------------- #
def build(record: EvaluationRecord) -> dict[str, Any]:
    """The whole result for one completed evaluation.

    Only `completed` records produce a result. A pending, running or failed
    evaluation has no assessment to report, and rendering one from a previous
    attempt is how a recruiter ends up reading a stale verdict as a current one.
    """
    if record.status != "completed":
        raise ResultError(
            [f"evaluation is {record.status}, so there is no result to assemble"]
        )
    if record.engine_version != ENGINE_VERSION:
        # Refused rather than reinterpreted. A `deep_evidence_v1` record was
        # scored over every listed skill; reading it with v2's denominator would
        # invent a percentage nobody computed and attribute it to a run that
        # never produced it. Re-evaluating the session mints a v2 record.
        raise ResultError([
            f"this evaluation was produced by {record.engine_version}, whose scoring "
            f"differed from {ENGINE_VERSION}; re-run the evaluation to get a "
            f"{ENGINE_VERSION} result"
        ])

    snap = record.snapshot or {}
    stored = record.result or {}
    interview = snap.get("interview", {})
    session = snap.get("session", {})

    questions = {q["id"]: q for q in snap.get("questions", [])}
    tasks = snap.get("tasks", [])
    skills = {s["id"]: s for s in snap.get("skills", [])}
    turns = snap.get("turns", [])
    evidence = [EvidenceItem(**item) for item in record.evidence]

    # Which tasks name each skill. The published version draws the mapping; the
    # result reports it rather than re-deriving a different one.
    tasks_for_skill: dict[str, list[str]] = {}
    for task in tasks:
        for skill_id in task.get("skill_ids", []):
            tasks_for_skill.setdefault(skill_id, []).append(task["id"])

    rows = stored.get("skill_assessment", [])
    by_name = {row["skill_name"]: row for row in rows}
    # `skill_id` is internal to the engine and absent from the wire row, so the
    # published skill order is what pairs them — and it is also the order the
    # result must preserve (§9 of the report contract: never reordered).
    ordered: list[tuple[str, dict[str, Any]]] = []
    for skill in snap.get("skills", []):
        row = by_name.get(skill["name"])
        if row is not None:
            ordered.append((skill["id"], row))

    question_results = _questions(
        snap, questions, skills, turns, evidence, tasks_for_skill
    )
    questions_by_skill: dict[str, list[str]] = {}
    for question in question_results:
        questions_by_skill.setdefault(question["skill_id"], []).append(
            question["question_id"]
        )

    skill_results = _skills(ordered, skills, evidence, questions_by_skill, tasks_for_skill)
    coverage = stored.get("coverage") or Coverage.of(
        [row["discussion_status"] for _, row in ordered]
    ).to_dict()
    details = stored.get("candidate_details", {})

    return {
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "evaluation": {
            "evaluation_id": record.evaluation_id,
            "engine_version": record.engine_version,
            "status": record.status,
            "attempt": record.attempt,
            "superseded": record.superseded,
            # What the result was computed from. Two results with the same
            # checksum read the same interview.
            "snapshot_checksum": record.snapshot_checksum,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
        },
        "interview": {
            "interview_id": record.interview_id,
            "version": record.interview_version,
            "title": interview.get("title", ""),
            "role_title": interview.get("role_title", ""),
            # The CONFIGURED scope of the conversation: short | medium | deep.
            # Not the probe ladder on each question — see `probe_stage_scale`.
            "interview_depth": interview.get("interview_depth", ""),
            "difficulty": interview.get("difficulty", ""),
            "recommended_duration_min": interview.get("recommended_duration_min", 0),
            "experience_from": interview.get("experience_from", 0),
            "experience_to": interview.get("experience_to", 0),
            "experience_level": interview.get("experience_level", ""),
        },
        "session": {
            "session_id": record.session_id,
            "candidate_name": details.get("name") or session.get("candidate_name", ""),
            "phase": session.get("phase", ""),
            "channel": session.get("channel", ""),
            "started_at": session.get("started_at"),
            "completed_at": session.get("completed_at"),
            "duration_sec": _duration(session),
            "questions_asked": len(snap.get("asked_question_ids", [])),
        },
        "overall": {
            "total_score": details.get("total_score", 0),
            "max_score": stored.get("maximum_possible_score", 0),
            "percentage": stored.get("percentage", 0.0),
            "overall_rating": details.get("overall_rating", ""),
            "recommendation": stored.get("recommendation", ""),
            # The score counts substantively discussed skills only. Said in the
            # payload so a reader never has to guess what the denominator was.
            "scored_over": "skills with substantive evidence (discussion_status "
                           "= discussed)",
            "skill_max_score": SKILL_MAX_SCORE,
        },
        "coverage": coverage,
        "skills": skill_results,
        "questions": question_results,
        "evidence": [_evidence_row(item, questions) for item in evidence],
        "summary": {
            "strengths": list(
                (stored.get("strengths_and_improvement_areas") or {}).get("strengths", [])
            ),
            "areas_for_improvement": list(
                (stored.get("strengths_and_improvement_areas") or {}).get(
                    "areas_for_improvement", []
                )
            ),
            "recommendation": stored.get("recommendation", ""),
            # Both spellings. The typo is the existing contract and is not
            # dropped; the corrected key is offered next to it.
            "recommendation_explaination": stored.get("recommendation_explaination", ""),
            "recommendation_explanation": stored.get("recommendation_explaination", ""),
            # Whether this recommendation is sitting on one of its own
            # thresholds. Derived, disclosed, and deliberately NOT smoothed —
            # see `recommendation_boundary`.
            "recommendation_boundary": recommendation_boundary(rows),
        },
        "scales": {
            "criteria": list(CRITERIA),
            "criterion_max": 5,
            "skill_max_score": SKILL_MAX_SCORE,
            "rating_bands": ["Poor <40", "Average 40-59", "Good 60-79", "Excellent >=80",
                             f"{UNRATED} (nothing discussed)"],
            "recommendations": list(RECOMMENDATIONS),
            # The two depth vocabularies, named apart in the payload itself.
            "interview_depth_scale": ["short", "medium", "deep"],
            "probe_stage_scale": list(STAGES),
            "evidence_dimensions": list(DIMENSIONS),
        },
        "integrity": {
            "validated_evidence": len(record.evidence),
            "quarantined_evidence": len(record.quarantined),
            "constraints_applied": len(record.adjustments),
            "repairs": len(record.repairs),
        },
    }


def recommendation_boundary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether this recommendation is sitting on one of its own thresholds.

    The walk itself lives beside the rules in `evaluator.boundary_of`, so there
    is one definition of "on the boundary" rather than a second one here that
    could drift from the branches it describes. This only reshapes the persisted
    wire rows into the view it takes.
    """
    return E.boundary_of([
        (
            row.get("discussion_status", ""),
            {name: int(row.get(name, 0)) for name in CRITERIA},
            row.get("skill_name", ""),
        )
        for row in rows
    ])


def _duration(session: dict[str, Any]) -> int | None:
    started, completed = session.get("started_at"), session.get("completed_at")
    if started is None or completed is None:
        return None
    return max(0, round(completed - started))


def _skills(
    ordered: list[tuple[str, dict[str, Any]]],
    skills: dict[str, dict[str, Any]],
    evidence: list[EvidenceItem],
    questions_by_skill: dict[str, list[str]],
    tasks_for_skill: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """One row per published skill, in the published order.

    The score and its maximum are per-skill contract facts, not a rollup: a
    skill is out of 25 whatever its status, and `criterion_ceiling` says what
    was actually reachable. A mentioned skill showing 5/25 is at its ceiling.
    """
    out: list[dict[str, Any]] = []
    for skill_id, row in ordered:
        published = skills.get(skill_id, {})
        status = row["discussion_status"]
        mine = [e for e in evidence if e.skill_id == skill_id]
        score = row["score"]
        out.append({
            "skill_id": skill_id,
            "skill_name": row["skill_name"],
            "priority": published.get("priority", ""),
            "expected_proficiency": published.get("expected_proficiency"),
            "assessment_scope": published.get("assessment_scope", ""),
            "discussion_status": status,
            "score": score,
            "max_score": SKILL_MAX_SCORE,
            "percentage": round(score / SKILL_MAX_SCORE * 100, 1),
            "criterion_ceiling": CRITERION_CEILING.get(status, 0),
            # Whether this skill's score reached the overall total at all.
            "counts_toward_overall_score": status == "discussed",
            "criteria": {name: row[name] for name in CRITERIA},
            "remarks": row["remarks"],
            "depth": dict(row["depth_evaluation"]),
            "task_ids": tasks_for_skill.get(skill_id, []),
            "question_ids": questions_by_skill.get(skill_id, []),
            "evidence_count": len(mine),
        })
    return out


def _questions(
    snap: dict[str, Any],
    questions: dict[str, dict[str, Any]],
    skills: dict[str, dict[str, Any]],
    turns: list[dict[str, Any]],
    evidence: list[EvidenceItem],
    tasks_for_skill: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """One row per question the candidate was actually asked.

    `depth_reached` is counted from the turns; `depth_demonstrated` is derived
    from this question's own evidence by the SAME function the skill rows use,
    so the two levels cannot disagree about what a dimension is worth.
    """
    out: list[dict[str, Any]] = []
    for question_id in snap.get("asked_question_ids", []):
        published = questions.get(question_id)
        if published is None:
            continue
        mine_turns = [t for t in turns if t.get("question_id") == question_id]
        usable = [t for t in mine_turns if not t.get("flagged")
                  and (t.get("answer") or "").strip()]
        mine_evidence = [e for e in evidence if e.question_id == question_id]
        skill_id = published.get("skill_id", "")

        reached = "direct"
        for turn in usable:
            stage = turn.get("depth_stage", "direct")
            if STAGES.index(stage) > STAGES.index(reached):
                reached = stage

        out.append({
            "question_id": question_id,
            "question_text": published.get("question_text", ""),
            "skill_id": skill_id,
            "skill_name": skills.get(skill_id, {}).get("name", ""),
            "task_ids": tasks_for_skill.get(skill_id, []),
            "task_id": published.get("task_id", ""),
            "difficulty": published.get("difficulty", ""),
            "question_type": published.get("question_type", ""),
            "answered": bool(usable),
            # The exchange in order: the prompt that was put, and what came
            # back. `stage` is the rung of the probe ladder, never the
            # interview's configured depth.
            "turns": [
                {
                    "turn_id": turn.get("turn_id", ""),
                    "stage": turn.get("depth_stage", "direct"),
                    "prompt": turn.get("prompt_text", ""),
                    "answer": turn.get("answer", ""),
                    "excluded": bool(turn.get("flagged")),
                    "excluded_reason": turn.get("flag_reason", ""),
                }
                for turn in mine_turns
            ],
            "probe_count": max(0, len(mine_turns) - 1),
            "depth_reached": reached,
            "depth_demonstrated": E.depth_demonstrated_from(mine_evidence),
            "evidence_count": len(mine_evidence),
        })
    return out


def _evidence_row(item: EvidenceItem, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One evidence item, traceable in both directions.

    `note` is not here. It is the extractor's rationale, and a rationale is
    reasoning — the recruiter gets the quote, where it came from, and what it
    bears on.
    """
    return {
        "skill_id": item.skill_id,
        "skill_name": item.skill_name,
        "question_id": item.question_id,
        "question_text": questions.get(item.question_id, {}).get("question_text", ""),
        "task_id": item.task_id,
        "turn_id": item.turn_id,
        "stage": item.depth_stage,
        "dimension": item.depth_dimension,
        "evidence_type": item.evidence_type,
        "evidence_strength": item.evidence_strength,
        "supports_criterion": item.supports_criterion,
        "candidate_quote": item.candidate_quote,
    }


# --------------------------------------------------------------------------- #
#  §22 — the result checks itself before anyone reads it
# --------------------------------------------------------------------------- #
def validate(payload: dict[str, Any], record: EvaluationRecord) -> list[str]:
    """Every claim the result makes, re-derived from its own parts.

    Not a formality. The result is assembled from three places — the persisted
    evaluation, the frozen snapshot and the validated evidence — and the failure
    this guards against is the quiet one: a page that says 84% at the top and
    shows skills that add to something else. A recruiter cannot audit arithmetic
    they cannot see, so the arithmetic is audited here and a result that
    contradicts itself is refused rather than rendered.
    """
    violations: list[str] = []
    overall = payload["overall"]
    skills = payload["skills"]
    questions = payload["questions"]
    coverage = payload["coverage"]
    snap = record.snapshot or {}

    published_skills = {s["id"] for s in snap.get("skills", [])}
    published_questions = {q["id"] for q in snap.get("questions", [])}
    published_tasks = {t["id"] for t in snap.get("tasks", [])}
    turn_ids = {t["turn_id"] for t in snap.get("turns", [])}

    # ---- the score is over discussed skills, and adds up ------------------ #
    assessed = [s for s in skills if s["discussion_status"] == "discussed"]
    total = sum(s["score"] for s in assessed)
    maximum = SKILL_MAX_SCORE * len(assessed)
    if overall["total_score"] != total:
        violations.append(
            f"total_score {overall['total_score']} is not the sum of the discussed "
            f"skills ({total})")
    if overall["max_score"] != maximum:
        violations.append(
            f"max_score {overall['max_score']} is not {SKILL_MAX_SCORE} per discussed "
            f"skill ({maximum})")
    expected_pct = round((total / maximum * 100) if maximum else 0.0, 1)
    if abs(overall["percentage"] - expected_pct) > 0.05:
        violations.append(
            f"percentage {overall['percentage']} does not follow from {total}/{maximum}")
    expected_rating = overall_rating(expected_pct) if assessed else UNRATED
    if overall["overall_rating"] != expected_rating:
        violations.append(
            f"overall_rating {overall['overall_rating']!r} does not follow from "
            f"{expected_pct}% (expected {expected_rating!r})")
    if overall["recommendation"] not in RECOMMENDATIONS:
        violations.append(f"recommendation {overall['recommendation']!r} is not allowed")

    # ---- every skill row obeys the contract ------------------------------- #
    seen: set[str] = set()
    for skill in skills:
        name = skill["skill_name"]
        if skill["skill_id"] not in published_skills:
            violations.append(f"{name}: not a skill in the published version")
        if skill["skill_id"] in seen:
            violations.append(f"{name}: appears more than once")
        seen.add(skill["skill_id"])

        criteria = skill["criteria"]
        if set(criteria) != set(CRITERIA):
            violations.append(f"{name}: criteria are not the canonical five")
        if "Reasoning" in criteria:
            violations.append(f"{name}: 'Reasoning' is a dimension, not a criterion")
        for criterion, value in criteria.items():
            if not isinstance(value, int) or not 0 <= value <= 5:
                violations.append(f"{name}: {criterion} is {value!r}, outside 0-5")
        if skill["score"] != sum(criteria.values()):
            violations.append(f"{name}: score is not the sum of its criteria")

        status = skill["discussion_status"]
        if status not in CRITERION_CEILING:
            violations.append(f"{name}: invalid discussion status {status!r}")
        elif status == "not_discussed":
            if any(criteria.values()):
                violations.append(f"{name}: not discussed but scored above zero")
            if skill["remarks"] != "Not discussed in interview":
                violations.append(f"{name}: not-discussed remarks are not the contract's")
        elif status == "mentioned":
            if max(criteria.values(), default=0) > 1:
                violations.append(f"{name}: mentioned but a criterion exceeds 1")
        elif min(criteria.values(), default=0) < 1:
            violations.append(f"{name}: discussed but a criterion is zero")

        if skill["counts_toward_overall_score"] != (status == "discussed"):
            violations.append(f"{name}: disagrees about whether it reached the total")

        depth = skill["depth"]
        for key in ("depth_reached", "depth_demonstrated"):
            if depth.get(key) not in STAGES:
                violations.append(f"{name}: {key} is {depth.get(key)!r}")
        for question_id in skill["question_ids"]:
            if question_id not in published_questions:
                violations.append(f"{name}: references question {question_id} which is "
                                  f"not in the published version")
        for task_id in skill["task_ids"]:
            if task_id not in published_tasks:
                violations.append(f"{name}: references a task not in the published version")

    listed = {s["id"] for s in snap.get("skills", [])}
    if seen != listed:
        violations.append(
            f"the result covers {len(seen)} skills but the published version lists "
            f"{len(listed)}")

    # The assembled rows are keyed off the snapshot's skill list, so a stored row
    # that is duplicated or names an unpublished skill would be silently DROPPED
    # rather than shown. Silently dropping is the wrong kind of impossible: the
    # stored rows are checked against the snapshot here so the mismatch is a
    # refusal instead of a quiet omission.
    stored_rows = (record.result or {}).get("skill_assessment", [])
    stored_names = [row.get("skill_name") for row in stored_rows]
    published_names = {s["name"] for s in snap.get("skills", [])}
    if len(stored_names) != len(set(stored_names)):
        duplicates = sorted({n for n in stored_names if stored_names.count(n) > 1})
        violations.append(
            f"the persisted evaluation has more than one row for {duplicates}")
    for name in set(stored_names) - published_names:
        violations.append(
            f"the persisted evaluation assesses {name!r}, which the published version "
            f"does not list")
    if len(stored_rows) != len(skills):
        violations.append(
            f"the persisted evaluation has {len(stored_rows)} skill rows but the "
            f"result assembled {len(skills)}")

    # ---- coverage is a count of those statuses --------------------------- #
    expected_coverage = Coverage.of([s["discussion_status"] for s in skills]).to_dict()
    if coverage != expected_coverage:
        violations.append(f"coverage {coverage} does not match the skill rows "
                          f"({expected_coverage})")

    # ---- questions and evidence point at real things --------------------- #
    for question in questions:
        if question["question_id"] not in published_questions:
            violations.append(
                f"question {question['question_id']} is not in the published version")
        if question["skill_id"] and question["skill_id"] not in published_skills:
            violations.append(
                f"question {question['question_id']} names an unpublished skill")
        for key in ("depth_reached", "depth_demonstrated"):
            if question[key] not in STAGES:
                violations.append(f"question {question['question_id']}: {key} invalid")
        for turn in question["turns"]:
            if turn["turn_id"] not in turn_ids:
                violations.append(
                    f"question {question['question_id']} references turn "
                    f"{turn['turn_id']}, which is not in the snapshot")

    for item in payload["evidence"]:
        where = item["turn_id"] or "?"
        if item["skill_id"] not in published_skills:
            violations.append(f"evidence {where} names an unpublished skill")
        if item["question_id"] not in published_questions:
            violations.append(f"evidence {where} names an unpublished question")
        if item["turn_id"] not in turn_ids:
            violations.append(f"evidence {where} references a turn not in the snapshot")
        if item["supports_criterion"] not in CRITERIA:
            violations.append(
                f"evidence {where} names {item['supports_criterion']!r}, which is not "
                f"one of the five criteria")
        if item["dimension"] not in DIMENSIONS:
            violations.append(f"evidence {where} names an unknown dimension")
        if item["stage"] not in STAGES:
            violations.append(f"evidence {where} has an invalid stage")

    # ---- the summary is prose, and gaps are not weaknesses --------------- #
    gaps = [s["skill_name"] for s in skills
            if s["discussion_status"] == "not_discussed"]
    improvements = " ".join(payload["summary"]["areas_for_improvement"]).lower()
    for gap in gaps:
        if gap.lower() in improvements:
            violations.append(
                f"{gap} was never discussed but appears as an area for improvement — "
                f"a coverage gap is not a weakness")

    return violations


def build_validated(record: EvaluationRecord) -> dict[str, Any]:
    """The result, or an error. Never a result that disagrees with itself."""
    payload = build(record)
    violations = validate(payload, record)
    if violations:
        raise ResultError(violations)
    return payload
