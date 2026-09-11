"""The evaluation snapshot — what an evaluation is allowed to look at.

An evaluation is a hiring document, and a hiring document that can silently
change is worse than none. So before any model is called, everything the
evaluation will read is frozen into one deterministic object:

    published InterviewVersion  ─┐
    the session's ItemRecords    ├─►  snapshot  ─►  checksum
    the stage-tagged turns      ─┘

Two properties this buys, and the reasons they matter:

  * **Reproducibility.** The snapshot carries the published skills, tasks and
    questions themselves — not a pointer to "the interview" — so re-running an
    evaluation a year later reads the assessment the candidate actually sat.
    `transcript_from` and `definition_from` rebuild the engine's inputs out of
    the snapshot alone, with no lookup that could have moved underneath it.

  * **Idempotency.** The checksum is what "the same evaluation" means. Two
    requests for one completed session hash identically and resolve to one
    record; a session that somehow gained a turn hashes differently and is
    honestly a different evaluation rather than an overwrite.

What is deliberately NOT in here: the draft, the job description, the
recruiter's notes, the invitation token, the candidate's email address, and any
score from a previous run. An evaluation needs the conversation and the contract
it was held under. Everything else is either mutable, someone else's, or a
number this run is supposed to be deriving for itself.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.types.definition import (
    InterviewDefinition,
    QuestionSpec,
    SkillSpec,
    TaskSpec,
)
from packages.types.evaluation import ENGINE_VERSION, experience_level
from services.data import versions
from services.evaluation import transcript as T
from services.orchestrator.state import SessionState


class SnapshotError(RuntimeError):
    """This session cannot be evaluated, and the reason is not the model's."""


def _title_for(interview_id: str) -> str:
    """The interview's name, if the container is still on file.

    Best-effort: a missing container is not a reason to refuse an evaluation,
    because everything the evaluation actually judges lives on the published
    version rather than on the mutable row.
    """
    from services.data import interviews

    row = interviews.get(interview_id)
    return row.title if row else ""


def build(state: SessionState) -> dict[str, Any]:
    """Freeze one completed session against the version the candidate sat.

    Refuses rather than falls back. A session pinned to no published version
    (or to one that is missing) has no immutable contract to be judged against,
    and evaluating it against the live draft would be exactly the thing the
    whole versioning story exists to prevent.
    """
    if not state.interview_id or not state.interview_version:
        raise SnapshotError(
            "This session is not pinned to a published interview version, so there "
            "is no immutable contract to evaluate it against."
        )

    definition = versions.definition_for(state.interview_id, state.interview_version)
    if definition is None:
        raise SnapshotError(
            f"Published version {state.interview_version} of {state.interview_id} "
            f"is not on file."
        )

    reconstructed = T.build(state, definition)

    return {
        "engine_version": ENGINE_VERSION,
        "session": {
            "session_id": state.session_id,
            # The candidate's name is on the report a person reads; their email,
            # their invitation token and their candidate row are not needed to
            # judge an answer and are left out.
            "candidate_name": state.candidate_name,
            "candidate_id": state.candidate_id,
            "interview_id": state.interview_id,
            "interview_version": state.interview_version,
            "channel": state.channel,
            "phase": state.phase,
            "started_at": state.created_at,
            "completed_at": state.completed_at,
        },
        "interview": {
            # The interview's name at the moment it was sat. The container's
            # title is mutable, so it is frozen here rather than read live —
            # a report that renames itself when a recruiter renames the
            # interview is a report about a different thing.
            "title": _title_for(state.interview_id),
            "role_title": definition.role_title,
            "language": definition.language,
            # Interview depth (short / medium / deep) is the intended SCOPE of
            # the conversation. It is not the investigation ladder, and nothing
            # downstream may treat it as one.
            "interview_depth": definition.interview_type,
            "difficulty": definition.difficulty,
            "experience_from": definition.experience_from,
            "experience_to": definition.experience_to,
            "experience_level": experience_level(definition.experience_to),
            "recommended_duration_min": definition.recommended_duration_min,
        },
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "priority": s.priority,
                "description": s.description,
                "assessment_scope": s.assessment_scope,
                "question_bank": s.question_bank,
                "expected_proficiency": s.proficiency_target,
            }
            for s in definition.skills
        ],
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "skill_ids": list(t.skill_ids),
            }
            for t in definition.tasks
        ],
        "questions": [
            {
                "id": q.id,
                "question_text": q.question_text,
                "skill_id": q.skill_id,
                "task_id": q.task_id,
                "difficulty": q.difficulty,
                "question_type": q.question_type,
                "looking_for": list(q.looking_for),
            }
            for q in definition.questions
        ],
        "turns": [
            {
                "turn_id": turn.turn_id,
                "question_id": turn.question_id,
                "skill_id": turn.skill_id,
                "task_id": turn.task_id,
                "depth_stage": turn.depth_stage,
                "prompt_text": turn.prompt_text,
                "answer": turn.answer,
                "flagged": turn.flagged,
                "flag_reason": turn.flag_reason,
            }
            for question in reconstructed.questions
            for turn in question.turns
        ],
        # Facts about the conversation, computed once here so every consumer
        # reads the same number rather than deriving its own.
        "depth_reached": {
            s.id: reconstructed.depth_reached_for(s.id) for s in definition.skills
        },
        "asked_question_ids": [q.question_id for q in reconstructed.questions],
    }


def checksum(snap: dict[str, Any]) -> str:
    """A content hash of everything the evaluation will read.

    Canonical JSON with sorted keys, so two dictionaries that say the same thing
    hash the same regardless of how they were built.
    """
    payload = json.dumps(snap, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
#  Rebuilding the engine's inputs from a snapshot alone
# --------------------------------------------------------------------------- #
def definition_from(snap: dict[str, Any]) -> InterviewDefinition:
    """The published contract, out of the snapshot rather than the store.

    Reading it back from `versions` would be equivalent today and wrong in
    principle: a re-run must not depend on anything outside the frozen record.
    """
    interview = snap.get("interview", {})
    session = snap.get("session", {})
    return InterviewDefinition(
        interview_id=session.get("interview_id", ""),
        version=session.get("interview_version", 0),
        role_title=interview.get("role_title", ""),
        language=interview.get("language", "en"),
        experience_from=interview.get("experience_from", 0),
        experience_to=interview.get("experience_to", 0),
        interview_type=interview.get("interview_depth", "medium"),
        difficulty=interview.get("difficulty", "medium"),
        recommended_duration_min=interview.get("recommended_duration_min", 0),
        skills=[
            SkillSpec(
                id=s["id"], name=s["name"], priority=s.get("priority", "medium"),
                description=s.get("description", ""),
                assessment_scope=s.get("assessment_scope", ""),
                question_bank=s.get("question_bank", s["id"]),
                proficiency_target=s.get("expected_proficiency", 3),
                domain=s.get("domain", ""),
            )
            for s in snap.get("skills", [])
        ],
        tasks=[
            TaskSpec(
                id=t["id"], name=t.get("name", ""),
                description=t.get("description", ""),
                skill_ids=list(t.get("skill_ids", [])),
            )
            for t in snap.get("tasks", [])
        ],
        questions=[
            QuestionSpec(
                id=q["id"], question_text=q.get("question_text", ""),
                competency=q.get("skill_id", ""), skill_id=q.get("skill_id", ""),
                task_id=q.get("task_id", ""),
                difficulty=q.get("difficulty", "medium"),
                question_type=q.get("question_type", "task_based"),
                looking_for=list(q.get("looking_for", [])),
            )
            for q in snap.get("questions", [])
        ],
    )


def transcript_from(snap: dict[str, Any]) -> T.InterviewTranscript:
    """The stage-tagged transcript, rebuilt from the frozen turns.

    The guardrail verdict is read from the snapshot rather than recomputed. A
    turn that was flagged when the interview happened stays flagged, even if the
    scanner's patterns change later — otherwise a re-run of a historical
    evaluation would quietly disagree with the one on file.
    """
    session = snap.get("session", {})
    out = T.InterviewTranscript(
        session_id=session.get("session_id", ""),
        interview_id=session.get("interview_id", ""),
        interview_version=session.get("interview_version", 0),
        candidate_name=session.get("candidate_name", ""),
    )
    by_question: dict[str, T.QuestionTranscript] = {}
    question_text = {q["id"]: q.get("question_text", "") for q in snap.get("questions", [])}

    for raw in snap.get("turns", []):
        question_id = raw.get("question_id", "")
        question = by_question.get(question_id)
        if question is None:
            question = T.QuestionTranscript(
                question_id=question_id,
                question_text=question_text.get(question_id, raw.get("prompt_text", "")),
                skill_id=raw.get("skill_id", ""),
                task_id=raw.get("task_id", ""),
            )
            by_question[question_id] = question
            out.questions.append(question)

        turn = T.Turn(
            turn_id=raw.get("turn_id", ""),
            question_id=question_id,
            question_text=question.question_text,
            prompt_text=raw.get("prompt_text", ""),
            answer=raw.get("answer", ""),
            depth_stage=raw.get("depth_stage", "direct"),
            skill_id=raw.get("skill_id", ""),
            task_id=raw.get("task_id", ""),
            flagged=bool(raw.get("flagged")),
            flag_reason=raw.get("flag_reason", ""),
        )
        question.turns.append(turn)
        if turn.usable:
            question.answered = True
            from packages.types.evaluation import deeper_of

            question.depth_reached = deeper_of(question.depth_reached, turn.depth_stage)

    return out
