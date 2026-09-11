"""The evaluation API — recruiter-side, and deliberately narrow.

    GET  /sessions/{id}/evaluation            the current evaluation, or its status
    POST /sessions/{id}/evaluation            request it (idempotent); runs it
    GET  /sessions/{id}/evaluation/evidence   the validated evidence behind it
    GET  /sessions/{id}/evaluations           every run, newest last
    GET  /evaluations/{evaluation_id}         one run by id

What these routes will not serve, whatever is asked for:

  * the extractor's or the judge's prompt;
  * any model reasoning — the per-item `note` the extractor writes is dropped
    here rather than passed through, because a rationale is reasoning even when
    it is one sentence long;
  * quotes that failed validation. A rejected item's *reason* is reported so a
    thin-looking skill can be explained, without the text it was rejected for:
    the most common rejection is a fabricated quote, and printing it in a
    recruiter UI is exactly the harm the validator exists to prevent.

Everything lives under the recruiter namespace, which is the same boundary the
rest of the console sits behind. That boundary is a `RECRUITER_AUTH_REQUIRED`
flag, not a login — see `_authorise`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packages.types.evaluation import ENGINE_VERSION
from services.data import evaluations, interviews
from services.data import sessions as store
from services.data.evaluations import EvaluationRecord
from services.evaluation import jobs, result, snapshot
from services.orchestrator.state import SessionState
from services.security import ratelimit

router = APIRouter(tags=["evaluation"])


class EvaluationRequest(BaseModel):
    #: A re-evaluation is an explicit act. It supersedes the current record and
    #: keeps it, rather than overwriting what a recruiter may already have read.
    force: bool = False


# --------------------------------------------------------------------------- #
#  Authorization
# --------------------------------------------------------------------------- #
def _authorise(session_id: str, interview_id: str | None = None) -> SessionState:
    """Resolve the chain recruiter → interview → session → published version.

    **The limitation, stated plainly:** there is no recruiter identity in this
    build (see `app._guard_recruiter_routes`), so the first link cannot be
    checked — anyone who can reach the recruiter namespace can reach every
    session in it. What IS enforced here is the rest of the chain: the session
    must exist, must belong to an interview that exists, must be pinned to a
    published version, and — when the caller names an interview — must belong to
    that one. A recruiter cannot pull an evaluation for a session under an
    interview they did not ask about by guessing a session id.

    When real authentication lands, it slots in at the top of this function and
    nothing else in the module changes.
    """
    state = store.try_load(session_id)
    if state is None:
        raise HTTPException(404, "No such session.")
    if not state.interview_id or not state.interview_version:
        raise HTTPException(
            403,
            "This session is not linked to a published interview version, so it "
            "cannot be evaluated or served here.",
        )
    if interviews.get(state.interview_id) is None:
        raise HTTPException(403, "This session's interview is not on file.")
    if interview_id and state.interview_id != interview_id:
        raise HTTPException(403, "That session does not belong to this interview.")
    return state


# --------------------------------------------------------------------------- #
#  Serialisation
# --------------------------------------------------------------------------- #
def _envelope(record: EvaluationRecord) -> dict[str, Any]:
    """The stable wire contract. One shape for every status."""
    snap = record.snapshot or {}
    interview = snap.get("interview", {})
    session = snap.get("session", {})

    body: dict[str, Any] = {
        "evaluation_id": record.evaluation_id,
        "session_id": record.session_id,
        "interview_id": record.interview_id,
        "interview_version": record.interview_version,
        "evaluation_engine_version": record.engine_version,
        "status": record.status,
        "attempt": record.attempt,
        "superseded": record.superseded,
        "snapshot_checksum": record.snapshot_checksum,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
        # Read from the frozen snapshot, at every status, so a report can name
        # the candidate and the interview before there is anything to score.
        #
        # `interview_depth` is the CONFIGURED SCOPE of the conversation — short,
        # medium or deep. It is not the investigation ladder (direct / probed /
        # deep_probed) that appears on each skill row, and a reader who confuses
        # the two will misread every depth figure on the page.
        "interview": {
            "interview_id": record.interview_id,
            "version": record.interview_version,
            "title": interview.get("title", ""),
            "role_title": interview.get("role_title", ""),
            "interview_depth": interview.get("interview_depth", ""),
            "difficulty": interview.get("difficulty", ""),
            "recommended_duration_min": interview.get("recommended_duration_min", 0),
            "experience_from": interview.get("experience_from", 0),
            "experience_to": interview.get("experience_to", 0),
        },
        "session": {
            "candidate_name": session.get("candidate_name", ""),
            "phase": session.get("phase", ""),
            "channel": session.get("channel", ""),
            "started_at": session.get("started_at"),
            "completed_at": session.get("completed_at"),
            "questions_asked": len(snap.get("asked_question_ids", [])),
        },
    }

    if record.status == evaluations.FAILED:
        # Distinguishable on the wire, because they call for different actions:
        # a model failure is worth retrying, a validation failure means the
        # output was refused and retrying it unchanged will refuse it again.
        body["error_kind"] = record.error_kind
        body["error"] = record.error
        # Where it stopped. Not reasoning and not a prompt — the one fact that
        # decides whether re-running it has any chance of a different answer.
        body["failed_stage"] = record.failed_stage
        return body

    if record.status != evaluations.COMPLETED:
        # Pending and running carry no scores. There is nothing to show yet, and
        # an empty scaffold reads like a candidate who scored nothing.
        return body

    result = dict(record.result)
    body.update({
        "candidate_details": result.get("candidate_details", {}),
        "skill_assessment": result.get("skill_assessment", []),
        "strengths_and_improvement_areas": result.get(
            "strengths_and_improvement_areas", {"strengths": [], "areas_for_improvement": []}
        ),
        "recommendation": result.get("recommendation", ""),
        # Spelling is the existing contract's, deliberately preserved.
        "recommendation_explaination": result.get("recommendation_explaination", ""),
        "maximum_possible_score": result.get("maximum_possible_score", 0),
        "percentage": result.get("percentage", 0.0),
        "evidence": {
            "validated_items": len(record.evidence),
            "quarantined_items": len(record.quarantined),
            "constraints_applied": len(record.adjustments),
            "repairs": len(record.repairs),
        },
        # Which model produced this, and what it cost. Not reasoning and not a
        # prompt — the facts you need to explain an evaluation later. Never a
        # credential: `jobs._model_meta` reads the gateway's telemetry, which
        # has never carried one.
        "engine": {
            "engine_version": record.engine_version,
            **{
                key: record.model_meta.get(key)
                for key in ("provider", "configured_model", "resolved_model",
                            "calls", "prompt_tokens", "completion_tokens", "latency_ms")
                if key in record.model_meta
            },
        },
    })
    return body


def _evidence_rows(record: EvaluationRecord) -> list[dict[str, Any]]:
    """Validated evidence only, each item traceable to where it came from.

    `note` is dropped: it is the extractor's rationale, and rationale is model
    reasoning.
    """
    question_text = {
        q["id"]: q.get("question_text", "")
        for q in (record.snapshot or {}).get("questions", [])
    }
    return [
        {
            "session_id": record.session_id,
            "skill_id": item.get("skill_id", ""),
            "skill_name": item.get("skill_name", ""),
            "task_id": item.get("task_id", ""),
            "question_id": item.get("question_id", ""),
            # The question as published, so a reader can see what was asked
            # without a second request. A probed turn still shows the original
            # question; which rung it came from is `depth_stage`.
            "question_text": question_text.get(item.get("question_id", ""), ""),
            "turn_id": item.get("turn_id", ""),
            "depth_stage": item.get("depth_stage", ""),
            "depth_dimension": item.get("depth_dimension", ""),
            "evidence_type": item.get("evidence_type", ""),
            "evidence_strength": item.get("evidence_strength", ""),
            "supports_criterion": item.get("supports_criterion", ""),
            "candidate_quote": item.get("candidate_quote", ""),
        }
        for item in record.evidence
    ]


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
@router.get("/sessions/{session_id}/evaluation")
async def read_evaluation(
    session_id: str, interview_id: str | None = None
) -> dict[str, Any]:
    _authorise(session_id, interview_id)
    record = evaluations.current_for(session_id, ENGINE_VERSION)
    if record is None:
        raise HTTPException(
            404,
            "No evaluation has been requested for this session yet.",
        )
    return _envelope(record)


@router.post("/sessions/{session_id}/evaluation")
async def create_evaluation(
    session_id: str, body: EvaluationRequest, interview_id: str | None = None,
    _: None = Depends(ratelimit.limiter("evaluation")),
) -> dict[str, Any]:
    """Request an evaluation and run it.

    Idempotent: asking twice for the same completed session returns the same
    record rather than a second opinion. `force` is how a deliberate re-run is
    expressed, and it produces a new record that supersedes the old one.
    """
    state = _authorise(session_id, interview_id)
    if state.phase != "complete":
        raise HTTPException(
            409, "This interview is not complete, so there is nothing to evaluate."
        )
    try:
        record = await asyncio.to_thread(
            jobs.request_and_run, state, requested_by="recruiter", force=body.force
        )
    except snapshot.SnapshotError as exc:
        raise HTTPException(409, str(exc)) from exc
    except jobs.NotEvaluatable as exc:
        raise HTTPException(409, str(exc)) from exc
    return _envelope(record)


@router.get("/sessions/{session_id}/evaluation/evidence")
async def read_evidence(
    session_id: str, interview_id: str | None = None
) -> dict[str, Any]:
    _authorise(session_id, interview_id)
    record = evaluations.current_for(session_id, ENGINE_VERSION)
    if record is None:
        raise HTTPException(404, "No evaluation has been requested for this session yet.")
    return {
        "evaluation_id": record.evaluation_id,
        "session_id": record.session_id,
        "interview_version": record.interview_version,
        "evaluation_engine_version": record.engine_version,
        "status": record.status,
        "evidence": _evidence_rows(record),
        # Reasons without the text. What was refused is worth knowing; what it
        # said is exactly what must not be reproduced.
        "quarantined": [
            {"reason": row.get("reason", ""), "skill_id": row.get("skill_id", "")}
            for row in record.quarantined
        ],
    }


@router.get("/sessions/{session_id}/evaluation/result")
async def read_result(
    session_id: str, interview_id: str | None = None
) -> dict[str, Any]:
    """The whole assessment result, in one response.

        overall -> skills -> questions -> turns -> evidence -> criterion

    One request, because a report that assembles itself from several is a report
    that can show halves of two different evaluations. Everything in it is read
    from the persisted evaluation or counted from the frozen snapshot; no model
    is called and no total is recomputed from a second source.

    A result is only produced for a `completed` evaluation, and only if it
    agrees with itself — `result.validate` re-derives the arithmetic and the
    contract's rules from the parts and a contradiction is a 409 rather than a
    page that quietly says two things.
    """
    _authorise(session_id, interview_id)
    record = evaluations.current_for(session_id, ENGINE_VERSION)
    if record is None:
        raise HTTPException(404, "No evaluation has been requested for this session yet.")
    if record.status != evaluations.COMPLETED:
        # The status envelope, not a result. A pending or failed evaluation has
        # no assessment, and rendering one from a previous attempt is how a
        # stale verdict gets read as a current one.
        raise HTTPException(
            409,
            {"message": f"The evaluation is {record.status}; there is no result yet.",
             "evaluation": _envelope(record)},
        )
    try:
        return result.build_validated(record)
    except result.ResultError as exc:
        raise HTTPException(
            409,
            {"message": "The assessment result does not agree with itself and has not "
                        "been shown.",
             "violations": exc.violations},
        ) from exc


@router.get("/evaluations/{evaluation_id}/result")
async def read_result_by_id(evaluation_id: str) -> dict[str, Any]:
    """The same result, addressed by the evaluation rather than the session."""
    record = evaluations.get(evaluation_id)
    if record is None:
        raise HTTPException(404, "No such evaluation.")
    _authorise(record.session_id)
    if record.status != evaluations.COMPLETED:
        raise HTTPException(
            409,
            {"message": f"The evaluation is {record.status}; there is no result yet.",
             "evaluation": _envelope(record)},
        )
    try:
        return result.build_validated(record)
    except result.ResultError as exc:
        raise HTTPException(
            409,
            {"message": "The assessment result does not agree with itself and has not "
                        "been shown.",
             "violations": exc.violations},
        ) from exc


@router.get("/sessions/{session_id}/evaluations")
async def list_evaluations(
    session_id: str, interview_id: str | None = None
) -> dict[str, Any]:
    """Every run for this session, including superseded ones.

    History is part of the record: an evaluation that was replaced is a thing a
    hiring file has to be able to show, not something to quietly drop.
    """
    _authorise(session_id, interview_id)
    return {
        "session_id": session_id,
        "evaluations": [
            _envelope(record) for record in evaluations.list_for_session(session_id)
        ],
    }


@router.get("/evaluations/{evaluation_id}")
async def read_evaluation_by_id(evaluation_id: str) -> dict[str, Any]:
    record = evaluations.get(evaluation_id)
    if record is None:
        raise HTTPException(404, "No such evaluation.")
    _authorise(record.session_id)
    return _envelope(record)
