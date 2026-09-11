"""The pilot's own surface: runs, reviews, and the numbers they produce.

Mounted under the recruiter namespace, because everything here is recruiter-side
and sits behind the same (currently absent — see `app._guard_recruiter_routes`)
boundary as the rest of the console.

    POST /pilot/runs                 open a run; whichever was open is closed
    GET  /pilot/runs                 every run, oldest first
    POST /pilot/runs/{id}/stop       close one
    GET  /pilot/summary              the scorecard, plus any threshold crossed
    GET  /pilot/dataset              one row per completed interview
    GET  /pilot/stability            repeated evaluations of identical input
    POST /sessions/{id}/review       a reviewer's verdict on the current result
    GET  /sessions/{id}/review       what reviewers have said about it

What these routes deliberately cannot do: change a score, a recommendation, or a
piece of evidence. A review is recorded next to the assessment and never inside
it, and no evaluator code path reads one back (§13).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from packages.types.evaluation import ENGINE_VERSION
from services.ai.gateway import Workload, workload_config
from services.data import evaluations, pilot
from services.security import authz
from services.data import sessions as store
from services.pilot import metrics

router = APIRouter(tags=["pilot"])


class StartRun(BaseModel):
    label: str = ""
    notes: str = ""


class ReviewIn(BaseModel):
    """One reviewer's read of one completed assessment.

    `recommendation` is what the REVIEWER would have said. It is stored beside
    Tara's, never over it: the pilot is collecting disagreement, not letting a
    reviewer edit the hiring document.
    """

    reviewer: str = Field(min_length=1, max_length=80)
    verdict: str
    reasons: list[str] = Field(default_factory=list)
    recommendation: str = ""
    note: str = Field(default="", max_length=1000)


# --------------------------------------------------------------------------- #
#  Runs
# --------------------------------------------------------------------------- #
@router.post("/pilot/runs", status_code=201)
async def start_run(request: Request, body: StartRun) -> dict[str, Any]:
    run = pilot.start_run(
        body.label,
        notes=body.notes,
        engine_version=ENGINE_VERSION,
        configured_model=workload_config(Workload.SCORING).model,
        created_by=_principal(request).user_id,
        organization_id=_org(request),
    )
    return run.to_dict()


@router.get("/pilot/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    org = _org(request)
    active = pilot.active_run(org)
    return {
        "runs": [r.to_dict() for r in pilot.list_runs(org)],
        "active": active.to_dict() if active else None,
    }


@router.post("/pilot/runs/{pilot_run_id}/stop")
async def stop_run(request: Request, pilot_run_id: str) -> dict[str, Any]:
    # `pilot_run_id` is not one of the ids the router's ownership guard knows,
    # so the tenant check is here. Another organization's run answers exactly
    # what a nonexistent one answers.
    run = pilot.stop_run(pilot_run_id, organization_id=_org(request))
    if run is None:
        raise HTTPException(404, authz.NOT_FOUND)
    return run.to_dict()


# --------------------------------------------------------------------------- #
#  Reporting
# --------------------------------------------------------------------------- #
def _scope(pilot_run_id: str | None, organization_id: str = "") -> str:
    """Which run a report covers.

    Unset means the run that is open, not everything ever recorded — a summary
    whose header names one run and whose numbers cover six months of test data
    is a summary that will be quoted wrongly. `pilot_run_id=all` asks for the
    whole store explicitly.
    """
    if pilot_run_id is None:
        return pilot.active_run_id(organization_id or None)
    return "" if pilot_run_id.lower() == "all" else pilot_run_id


def _principal(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None:  # pragma: no cover — the router guard runs first
        raise HTTPException(401, "Sign in to use the recruiter API.")
    return principal


def _org(request: Request) -> str:
    """The organization every pilot number is computed over.

    Taken from the verified principal, never from a query parameter: a pilot
    report is an aggregate over sessions and evaluations, and an aggregate is
    exactly where a tenant leak hides.
    """
    return _principal(request).organization_id


@router.get("/pilot/summary")
async def summary(request: Request, pilot_run_id: str | None = None) -> dict[str, Any]:
    """The scorecard. Counts, rates and durations — never candidate content."""
    org = _org(request)
    return metrics.summary(_scope(pilot_run_id, org), org)


@router.get("/pilot/dataset")
async def dataset(request: Request, pilot_run_id: str | None = None) -> dict[str, Any]:
    org = _org(request)
    scope = _scope(pilot_run_id, org)
    return {"pilot_run_id": scope, "rows": metrics.dataset(scope, org)}


@router.get("/pilot/stability")
async def stability(request: Request, pilot_run_id: str | None = None) -> dict[str, Any]:
    org = _org(request)
    return metrics.stability(_scope(pilot_run_id, org), org)


# --------------------------------------------------------------------------- #
#  Review
# --------------------------------------------------------------------------- #
def _current(session_id: str):
    state = store.try_load(session_id)
    if state is None:
        raise HTTPException(404, "No such session.")
    record = evaluations.current_for(session_id, ENGINE_VERSION)
    if record is None:
        raise HTTPException(404, "No evaluation has been requested for this session yet.")
    return state, record


@router.get("/sessions/{session_id}/review")
async def read_reviews(session_id: str) -> dict[str, Any]:
    _, record = _current(session_id)
    return {
        "session_id": session_id,
        "evaluation_id": record.evaluation_id,
        "reviews": [r.to_dict() for r in pilot.list_reviews(record.evaluation_id)],
        "verdicts": list(pilot.VERDICTS),
        "reasons": list(pilot.DISAGREEMENT_REASONS),
    }


@router.post("/sessions/{session_id}/review", status_code=201)
async def write_review(session_id: str, body: ReviewIn) -> dict[str, Any]:
    state, record = _current(session_id)
    if record.status != evaluations.COMPLETED:
        raise HTTPException(
            409, "There is no completed assessment to review for this session."
        )
    result = record.result or {}
    coverage = result.get("coverage") or {}
    try:
        review = pilot.save_review(pilot.Review(
            evaluation_id=record.evaluation_id,
            session_id=session_id,
            reviewer=body.reviewer.strip(),
            verdict=body.verdict,
            reasons=list(body.reasons),
            recommendation=body.recommendation.strip(),
            note=body.note.strip(),
            # Frozen alongside the verdict: a later re-evaluation must not be
            # able to change what the reviewer is recorded as having seen.
            ai_recommendation=result.get("recommendation", ""),
            ai_total_score=(result.get("candidate_details") or {}).get("total_score", 0),
            ai_percentage=result.get("percentage", 0.0),
            ai_coverage_percentage=coverage.get("coverage_percentage", 0.0),
            pilot_run_id=record.pilot_run_id or state.pilot_run_id,
        ))
    except pilot.ReviewError as exc:
        raise HTTPException(422, str(exc)) from exc
    return review.to_dict()
