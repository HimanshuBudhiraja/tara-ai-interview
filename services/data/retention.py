"""How long candidate data lives, and when it becomes eligible to go.

One module, because a retention period that lives in two places is a retention
period that will eventually disagree with itself — and the first time it does,
some record survives because each service thought the other owned it.

Nothing here deletes anything. This module answers questions:

    lifecycle_of(invite)        which state this candidate's data is in
    deadline(invite)            the exact moment it becomes eligible
    is_expired(invite)          is that moment in the past
    eligible(...)               every candidate record that has passed it
    mark_eligible(invite)       record that we noticed

`erasure.py` does the deleting. Keeping the two apart means "what should go?"
can be asked, tested and reviewed without the risk of anything going.

**Every deadline is computed from a server-side timestamp.** The clock that
matters is `completed_at`, `revoked_at` or `created_at` on records the server
wrote itself. Nothing here reads a browser time, a candidate-supplied value, or
"when the recruiter last looked at it" — a candidate who could influence their
own retention deadline could keep their transcript alive indefinitely, or have
it deleted before a decision was made.

## The states

    ACTIVE              inside its retention period, data present
    RETENTION_ELIGIBLE  past the deadline, data still present
    DELETION_REQUESTED  an authorized human asked for erasure
    DELETED             erased, and verification confirmed nothing remained
    DELETION_FAILED     erasure ran and something is still there

`DELETION_FAILED` is the state that makes the rest trustworthy. Without it, a
partial deletion has to be reported as either success (a lie) or nothing (a
silent leak). See §16 of the phase brief and `erasure.erase`.

The invariant this module exists to protect:

> Data that is eligible for deletion must not remain indefinitely because no
> service knows it is eligible.

Which is why `eligible()` derives eligibility from the timestamps every time it
is asked, rather than from a flag someone had to remember to set. A sweep that
never ran cannot make a record look retained.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from services import config
from services.data import invites
from services.data.invites import Invite

DAY = 86_400.0

#: Lifecycle states. Strings, matching the repository's convention for
#: `Invite.status` and `EvaluationRecord.status`, so one serialisation style
#: covers all of them.
ACTIVE = "active"
RETENTION_ELIGIBLE = "retention_eligible"
DELETION_REQUESTED = "deletion_requested"
DELETED = "deleted"
DELETION_FAILED = "deletion_failed"

STATES = (ACTIVE, RETENTION_ELIGIBLE, DELETION_REQUESTED, DELETED, DELETION_FAILED)

#: States in which candidate data is still present and therefore still readable
#: by an authorized recruiter.
PRESENT = (ACTIVE, RETENTION_ELIGIBLE, DELETION_REQUESTED, DELETION_FAILED)

#: States a sweep should act on. `DELETION_FAILED` is included deliberately: a
#: failed erasure is work still outstanding, and a sweep that skipped it would
#: leave the one record that most needs attention untouched forever.
SWEEPABLE = (RETENTION_ELIGIBLE, DELETION_REQUESTED, DELETION_FAILED)


# --------------------------------------------------------------------------- #
#  Deadlines
# --------------------------------------------------------------------------- #
def retention_days() -> int:
    """The candidate record's period, in days. Read at call time so a test or a
    deployment can change it without reloading the module."""
    return config.CANDIDATE_DATA_RETENTION_DAYS


def anchor_time(invite: Invite) -> float:
    """The server-side timestamp the deadline is measured from.

    Latest-wins among the moments the server itself recorded, because the clock
    should start when the record stopped changing:

      * `completed_at` — the interview finished. The normal case.
      * `revoked_at`   — the recruiter withdrew it. Nothing more will happen.
      * `created_at`   — invited and never sat. Otherwise an invitation nobody
                         opened would never become eligible at all.

    Deliberately NOT used: any browser timestamp, any candidate-supplied value,
    and any "last viewed" time. A deadline that moves when a recruiter opens a
    report is a deadline that never arrives for a popular candidate.
    """
    return max(
        invite.created_at or 0.0,
        invite.completed_at or 0.0,
        invite.revoked_at or 0.0,
    )


def deadline(invite: Invite, *, days: int | None = None) -> float:
    """The exact moment this candidate's data becomes eligible for deletion.

    Deterministic: the same record and the same policy always give the same
    number, which is what makes it auditable. Nothing random, nothing derived
    from "now".
    """
    period = retention_days() if days is None else days
    return anchor_time(invite) + period * DAY


def is_expired(invite: Invite, *, now: float | None = None) -> bool:
    return (now if now is not None else time.time()) >= deadline(invite)


def days_remaining(invite: Invite, *, now: float | None = None) -> float:
    """Negative once the deadline has passed. For the operator's report."""
    at = now if now is not None else time.time()
    return round((deadline(invite) - at) / DAY, 2)


# --------------------------------------------------------------------------- #
#  State
# --------------------------------------------------------------------------- #
def lifecycle_of(invite: Invite, *, now: float | None = None) -> str:
    """The state this candidate's data is really in.

    Terminal states are read from the record; `ACTIVE` versus
    `RETENTION_ELIGIBLE` is computed from the clock. That asymmetry is the
    point: "deleted" is a fact somebody wrote down, while "eligible" is a fact
    about the current time, and computing it means a sweep that has not run
    cannot make an expired record look retained.
    """
    stored = (invite.lifecycle or ACTIVE).strip()
    if stored in (DELETED, DELETION_REQUESTED, DELETION_FAILED):
        return stored
    return RETENTION_ELIGIBLE if is_expired(invite, now=now) else ACTIVE


def data_present(invite: Invite) -> bool:
    """Should this candidate's transcript, evidence and report still be readable?"""
    return lifecycle_of(invite) in PRESENT


@dataclass
class Candidate:
    """One candidate's lifecycle position, in the form an operator reads.

    Carries no candidate content — not the name, not the transcript. A retention
    report is written to be pasted into a ticket.
    """

    token: str
    interview_id: str
    session_id: str
    organization_id: str
    lifecycle: str
    anchor_at: float
    deadline_at: float
    days_remaining: float
    attempts: int
    error: str
    remaining: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            # Truncated: enough to find the row, not enough to use as a
            # credential if this ends up in a log or a ticket.
            "token": self.token[:8] + "…",
            "interview_id": self.interview_id,
            "session_id": self.session_id,
            "organization_id": self.organization_id,
            "lifecycle": self.lifecycle,
            "anchor_at": self.anchor_at,
            "deadline_at": self.deadline_at,
            "days_remaining": self.days_remaining,
            "deletion_attempts": self.attempts,
            "deletion_error": self.error,
            "deletion_remaining": self.remaining,
        }


def describe(
    invite: Invite, *, owner: str | None = None, now: float | None = None
) -> Candidate:
    if owner is None:
        from services.security import authz

        owner = authz.owning_organization_of_interview(invite.interview_id) or ""
    return Candidate(
        token=invite.token,
        interview_id=invite.interview_id,
        session_id=invite.session_id or "",
        organization_id=owner,
        lifecycle=lifecycle_of(invite, now=now),
        anchor_at=anchor_time(invite),
        deadline_at=deadline(invite),
        days_remaining=days_remaining(invite, now=now),
        attempts=invite.deletion_attempts,
        error=invite.deletion_error,
        remaining=list(invite.deletion_remaining),
    )


# --------------------------------------------------------------------------- #
#  Finding work
# --------------------------------------------------------------------------- #
def candidates(
    *, organization_id: str = "", now: float | None = None
) -> list[Candidate]:
    """Every candidate record, with its lifecycle position.

    `organization_id` filters to one tenant, for the recruiter-facing view. The
    sweep passes nothing, because retention is a property of the deployment
    rather than of whoever happens to be signed in.
    """
    # One read of the invitation file, and one read of the interview file —
    # rather than one of each per candidate. The sweep runs over every
    # invitation the deployment has ever issued, so this is the difference
    # between a second and several minutes.
    owners = _owner_index()
    out: list[Candidate] = []
    for invite in invites.all_invites():
        owner = owners.get(invite.interview_id, "")
        if organization_id and owner != organization_id:
            continue
        out.append(describe(invite, owner=owner, now=now))
    out.sort(key=lambda c: c.deadline_at)
    return out


def _owner_index() -> dict[str, str]:
    """interview_id → organization_id, read once."""
    from services.data import interviews

    return {row.id: row.organization_id for row in interviews.list_all()}


def eligible(*, now: float | None = None, limit: int = 0) -> list[Candidate]:
    """Everything a sweep should act on, oldest deadline first.

    Oldest first so a limited sweep makes progress on the most overdue records
    rather than whichever the filesystem happened to list first.
    """
    rows = [c for c in candidates(now=now) if c.lifecycle in SWEEPABLE]
    return rows[:limit] if limit else rows


def summary(*, organization_id: str = "", now: float | None = None) -> dict[str, Any]:
    """Counts by state, plus what is overdue. The operator's one-line answer."""
    rows = candidates(organization_id=organization_id, now=now)
    counts = {state: 0 for state in STATES}
    for row in rows:
        counts[row.lifecycle] = counts.get(row.lifecycle, 0) + 1
    overdue = [r for r in rows if r.lifecycle in SWEEPABLE]
    return {
        "policy": {
            "candidate_data_retention_days": config.CANDIDATE_DATA_RETENTION_DAYS,
            "transcript_retention_days": config.TRANSCRIPT_RETENTION_DAYS,
            "evaluation_retention_days": config.EVALUATION_RETENTION_DAYS,
            "audit_retention_days": config.AUDIT_RETENTION_DAYS,
            "sweep_interval_hours": config.RETENTION_SWEEP_INTERVAL_HOURS,
        },
        "candidates": len(rows),
        "by_lifecycle": counts,
        "awaiting_cleanup": len(overdue),
        "failed": counts.get(DELETION_FAILED, 0),
        "oldest_overdue_days": (
            abs(min((r.days_remaining for r in overdue), default=0.0))
            if overdue else 0.0
        ),
    }


# --------------------------------------------------------------------------- #
#  Transitions
#
#  Writing state is deliberately narrow: three functions, each one transition.
#  Anything that wants to move a record moves it through here, so the set of
#  legal transitions is readable in one screen.
# --------------------------------------------------------------------------- #
def request_deletion(token: str, *, actor: str = "") -> Invite | None:
    """Mark a candidate's data for erasure. Idempotent.

    Separate from performing it, because the two can fail independently: a
    request that was recorded and not yet carried out is recoverable, and a
    request that was never recorded is invisible.
    """
    invite = invites.get(token)
    if invite is None:
        return None
    if invite.lifecycle == DELETED:
        return invite                       # already gone; nothing to request
    if invite.lifecycle != DELETION_REQUESTED:
        invite.lifecycle = DELETION_REQUESTED
        invite.deletion_requested_at = time.time()
        invites.update(invite)
    return invite


def mark_eligible(invite: Invite) -> Invite:
    """Persist what `lifecycle_of` already computes.

    Only useful as a record that the sweep saw it — eligibility itself is never
    read from this field, so a missed write cannot make an expired record look
    retained.
    """
    if lifecycle_of(invite) == RETENTION_ELIGIBLE and invite.lifecycle == ACTIVE:
        invite.lifecycle = RETENTION_ELIGIBLE
        invites.update(invite)
    return invite
