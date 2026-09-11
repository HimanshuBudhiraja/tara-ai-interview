"""One place that decides whether a request may touch a resource.

Five checks, named because the phase's own security model names them:

    AUTHENTICATED               there is a verified principal at all
    ORGANIZATION_MEMBER         the principal's organization is active
    RESOURCE_OWNER              the resource belongs to that organization
    ROLE_ALLOWED                the principal's role carries the capability
    CANDIDATE_INVITATION_SCOPE  a candidate reaching only their own session

Ownership is derived through the parent relation rather than copied onto every
row. There is exactly one authoritative anchor — `InterviewConfig.organization_id`
— and everything else resolves to it:

    Invitation ─┐
    Session ────┼─► interview_id ─► InterviewConfig.organization_id
    Evaluation ─┘
    InterviewVersion ─► interview_id ─► …
    Job ─► its own org_id, checked directly

That is deliberate. A duplicated tenant id is a second source of truth, and the
first time the two disagree the disagreement is a data breach.

**Cross-tenant reads answer 404, not 403.** Confirming that
`iv_someone_elses_interview` exists tells an attacker which ids are real, and
the product has no legitimate reason to. A 403 is reserved for the case where
the principal can legitimately see a resource but their ROLE does not carry the
action — there, hiding the reason would just be confusing.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services.data import accounts, evaluations, interviews, invites, versions
from services.data import sessions as session_store
from services.security.principal import AuthenticatedPrincipal

#: The message every non-disclosing refusal uses. One string, so a caller
#: cannot tell "does not exist" from "not yours" by comparing wording.
NOT_FOUND = "Not found."


def _refuse_missing() -> HTTPException:
    return HTTPException(404, NOT_FOUND)


def _refuse_role(capability: str) -> HTTPException:
    return HTTPException(
        403, f"Your role does not allow this action ({capability})."
    )


# --------------------------------------------------------------------------- #
#  ORGANIZATION_MEMBER / ROLE_ALLOWED
# --------------------------------------------------------------------------- #
def organization_member(principal: AuthenticatedPrincipal) -> None:
    org = accounts.get_organization(principal.organization_id)
    if org is None or org.status != accounts.ACTIVE:
        raise HTTPException(403, "This organization is not active.")


def role_allowed(principal: AuthenticatedPrincipal, capability: str) -> None:
    if not principal.can(capability):
        raise _refuse_role(capability)


# --------------------------------------------------------------------------- #
#  RESOURCE_OWNER — one resolver per resource, all reaching the same anchor
# --------------------------------------------------------------------------- #
def owning_organization_of_interview(interview_id: str) -> str | None:
    """The organization an interview belongs to, or None if there is no such
    interview. Both answers are turned into the same 404 by the callers."""
    row = interviews.get(interview_id)
    return row.organization_id if row is not None else None


def interview(principal: AuthenticatedPrincipal, interview_id: str):
    row = interviews.get(interview_id)
    if row is None or row.organization_id != principal.organization_id:
        raise _refuse_missing()
    return row


def session(principal: AuthenticatedPrincipal, session_id: str):
    state = session_store.try_load(session_id)
    if state is None:
        raise _refuse_missing()
    owner = owning_organization_of_interview(state.interview_id)
    if owner is None or owner != principal.organization_id:
        raise _refuse_missing()
    return state


def evaluation(principal: AuthenticatedPrincipal, evaluation_id: str):
    record = evaluations.get(evaluation_id)
    if record is None:
        raise _refuse_missing()
    owner = owning_organization_of_interview(record.interview_id)
    if owner is None or owner != principal.organization_id:
        raise _refuse_missing()
    return record


def invitation(principal: AuthenticatedPrincipal, token: str):
    invite = invites.get(token)
    if invite is None:
        raise _refuse_missing()
    owner = owning_organization_of_interview(invite.interview_id)
    if owner is None or owner != principal.organization_id:
        raise _refuse_missing()
    return invite


def interview_version(principal: AuthenticatedPrincipal, interview_id: str, version: int):
    interview(principal, interview_id)          # ownership first
    row = versions.get(interview_id, version)
    if row is None:
        raise _refuse_missing()
    return row


#: Path parameter name → resolver. The recruiter dependency walks this, so a new
#: route with one of these names is scoped the moment it is added, and a route
#: with a NEW kind of id fails the inventory test until it is listed here.
RESOLVERS: dict[str, Any] = {
    "interview_id": interview,
    "session_id": session,
    "evaluation_id": evaluation,
    "token": invitation,
}


def enforce_path_ownership(
    principal: AuthenticatedPrincipal, path_params: dict[str, Any]
) -> None:
    """Every recognised id in the path must belong to the principal's org."""
    for name, resolver in RESOLVERS.items():
        value = path_params.get(name)
        if value:
            resolver(principal, str(value))


# --------------------------------------------------------------------------- #
#  Filtering — the other half of tenant isolation
#
#  A list endpoint that returns every row is as much a leak as a detail endpoint
#  that does not check ownership, and it is the half that is easy to forget.
# --------------------------------------------------------------------------- #
def visible_interviews(principal: AuthenticatedPrincipal) -> list:
    return [
        row for row in interviews.list_all()
        if row.organization_id == principal.organization_id
    ]


def visible_interview_ids(principal: AuthenticatedPrincipal) -> set[str]:
    return {row.id for row in visible_interviews(principal)}


def visible_sessions(principal: AuthenticatedPrincipal) -> list:
    allowed = visible_interview_ids(principal)
    out = []
    for session_id in session_store.list_ids():
        state = session_store.try_load(session_id)
        if state is not None and state.interview_id in allowed:
            out.append(state)
    return out


def owns_session(principal: AuthenticatedPrincipal, session_id: str) -> bool:
    state = session_store.try_load(session_id)
    if state is None:
        return False
    return owning_organization_of_interview(state.interview_id) == principal.organization_id
