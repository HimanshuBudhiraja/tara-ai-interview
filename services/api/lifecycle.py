"""Retention and erasure, over HTTP.

    GET    /candidates/{token}/data     this candidate's lifecycle position
    DELETE /candidates/{token}/data     erase it
    GET    /retention                   the organization's retention summary
    POST   /retention/sweep             run the cleanup now

Mounted under the recruiter prefix, so every route inherits
`security.recruiter_scope` — and `{token}` is one of the path parameters
`authz.RESOLVERS` knows, which means tenant ownership is resolved before any
handler runs. A recruiter in another organization gets the same `404` they get
for an invitation that does not exist; there is no separate check here to
forget.

**Who may delete.** The guard maps `DELETE` to the `delete` capability, which
only `admin` carries. That is deliberate and it is not a new role: erasure is
irreversible, and the repository already draws the line between "can run the
hiring process" and "can destroy its records" in exactly this place — a
recruiter can publish and invite, an administrator can delete.

Nothing here decides anything. Policy lives in `data/retention.py`, the workflow
in `data/erasure.py`, and this module translates HTTP into calls on them.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.data import erasure, invites, retention
from services.security import authz

router = APIRouter(tags=["lifecycle"])


def _principal(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None:  # pragma: no cover — the router guard runs first
        raise HTTPException(401, "Sign in to use the recruiter API.")
    return principal


@router.get("/retention")
async def read_retention(request: Request) -> dict[str, Any]:
    """The policy, and how much of this organization's data is past it.

    Counts and dates only — never a candidate name. A retention report is
    written to be pasted into a ticket.
    """
    who = _principal(request)
    return retention.summary(organization_id=who.organization_id)


@router.get("/retention/candidates")
async def list_retention_candidates(
    request: Request, lifecycle: str | None = None
) -> dict[str, Any]:
    """Every candidate record's lifecycle position, soonest deadline first."""
    who = _principal(request)
    rows = retention.candidates(organization_id=who.organization_id)
    if lifecycle:
        rows = [r for r in rows if r.lifecycle == lifecycle]
    return {"candidates": [r.to_dict() for r in rows], "total": len(rows)}


@router.get("/candidates/{token}/data")
async def read_candidate_data_state(token: str) -> dict[str, Any]:
    """Where one candidate sits in the lifecycle, and what still exists.

    `verify()` is run live rather than reported from the stored state, so this
    answers "what is actually on disk" rather than "what did we last write
    down" — which is the question worth asking of a record that claims to be
    deleted.
    """
    invite = invites.get(token)
    if invite is None:                      # ownership already checked by the guard
        raise HTTPException(404, authz.NOT_FOUND)
    return {
        **retention.describe(invite).to_dict(),
        "data_present": retention.data_present(invite),
        "locations_holding_data": erasure.verify(token),
    }


@router.delete("/candidates/{token}/data")
async def erase_candidate_data(request: Request, token: str) -> dict[str, Any]:
    """Erase this candidate's data. Irreversible, idempotent, verified.

    Answers `200` when nothing remains — including when nothing remained before
    the call, which is what makes a retry safe. Answers `409` when erasure ran
    and something is still there, with the locations named: a partial deletion
    reported as success would be the worst possible outcome of this endpoint.
    """
    who = _principal(request)
    invite = invites.get(token)
    if invite is None:
        raise HTTPException(404, authz.NOT_FOUND)

    retention.request_deletion(token, actor=who.user_id)
    outcome = erasure.erase(
        token, actor=who.user_id, organization_id=who.organization_id
    )
    if not outcome.ok:
        raise HTTPException(409, {
            "message": "Erasure did not complete. The record is marked "
                       "deletion_failed and the request can be retried.",
            **outcome.to_dict(),
        })
    return outcome.to_dict()


@router.post("/retention/sweep")
async def run_sweep(request: Request, dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """Run the retention cleanup now.

    The same function the CLI runs, exposed so an operator with a browser can
    do it without shell access. `dry_run=true` reports what would go without
    touching anything.

    Not organization-scoped, and that is on purpose: retention is a property of
    the deployment. The `admin` capability is what gates it.
    """
    who = _principal(request)
    if not who.can("delete"):
        raise HTTPException(403, "Your role does not allow this action (delete).")
    return erasure.sweep(actor=who.user_id, dry_run=dry_run, limit=limit)
