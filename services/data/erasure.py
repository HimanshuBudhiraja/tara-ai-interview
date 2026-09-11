"""Erasing one candidate's data, everywhere it exists.

The hard part of erasure is not deleting a row. It is knowing every place a copy
went, deleting them in an order that survives a crash, and then *checking* —
because "we called delete on six things" and "nothing remains" are different
claims, and only the second one is worth making.

## Where a candidate's data actually is

Established by inspection, not by the architecture diagram (see
DATA_LIFECYCLE.md §1 for the full inventory):

    data/invites.json          candidate_name, candidate_id, recipient, note
    data/sessions/{sid}.json   verbatim transcript, every answer, the grant
    data/audit/{sid}.jsonl     the decision trail: classifier labels, word
                               counts, and Tara's probe text — which is
                               generated FROM an answer and can paraphrase it
    data/evaluations/{eid}.json  snapshot.turns is a FROZEN COPY of the whole
                               transcript; evidence[].quote is verbatim; result
                               carries the scores and candidate_details
    data/evaluations/.locks/{sid}.lock   the session id, nothing else
    data/pilot_reviews/{eid}__*.json     a reviewer's note about an evaluation
    data/audit/_product.jsonl  no names and no answers, but EVALUATION_COMPLETED
                               rows carry the score, rating and recommendation

The frozen snapshot is the one that would be missed. Deleting the session file
and stopping there leaves a complete second copy of the transcript inside every
evaluation of it.

## Order

Leaves first, anchor last:

    evaluations → pilot reviews → session state → session trail → lock
    → product-log redaction → invitation row

The invitation is marked `deleted` only after verification passes, so a crash
half way through leaves a record still in `deletion_requested` with its
dependents partly gone — which the next sweep finds and finishes. The opposite
order would mark it deleted and then lose the pointer to whatever was left.

## What is NOT deleted

* **Published interview versions.** A candidate sitting an interview does not
  make the interview theirs. Deleting v1 because one of forty candidates asked
  for erasure would destroy the definition the other thirty-nine were assessed
  against, and the evidence that the assessment was fair.
* **Organizations, users, jobs, interview drafts.** Recruiter data.
* **The product audit log's own events.** Every row survives, in place, in
  order; only the fields naming an outcome are replaced. The trail must be able
  to prove the deletion happened, and a trail with rows removed cannot prove
  anything. See `_OUTCOME_FIELDS`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from services import config
from services.data import audit, evaluations, invites, pilot, retention
from services.data import sessions as session_store
from services.data.invites import Invite

#: Fields on a product-log row that describe an assessment OUTCOME. Redacted
#: rather than removed: the event, its time, its actor and its ids stay, so the
#: trail still says "this session was evaluated and later erased", while the
#: score itself — which is candidate data — does not survive.
_OUTCOME_FIELDS = (
    "total_score",
    "maximum_possible_score",
    "overall_rating",
    "recommendation",
    "percentage",
    "skills",
    "evidence_items",
    "band",
    "band_label",
)

#: What a redacted field becomes. A marker rather than a removal, so a reader
#: can tell "erased" from "was never recorded".
REDACTED = "[erased]"


@dataclass
class Outcome:
    """What one erasure did. Carries no candidate content — this is what goes
    into the audit event, the API response and the operator's log."""

    token: str
    session_id: str
    interview_id: str
    ok: bool
    lifecycle: str
    #: location → how many objects were removed there.
    removed: dict[str, int] = field(default_factory=dict)
    #: Locations that still held something when verification ran.
    remaining: list[str] = field(default_factory=list)
    #: Operator-language reason, never an exception's payload.
    error: str = ""
    attempts: int = 0
    already_deleted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token[:8] + "…",
            "session_id": self.session_id,
            "interview_id": self.interview_id,
            "ok": self.ok,
            "lifecycle": self.lifecycle,
            "removed": self.removed,
            "remaining": self.remaining,
            "error": self.error,
            "attempts": self.attempts,
            "already_deleted": self.already_deleted,
        }


def _failure_reason(exc: BaseException) -> str:
    """An operator-readable reason, with nothing sensitive in it.

    Exception payloads are where candidate content leaks into logs: a
    `KeyError` naming a transcript key, a decode error quoting the line it
    choked on. The type and the location are enough to act on.
    """
    return f"{type(exc).__name__} while erasing"


# --------------------------------------------------------------------------- #
#  The individual removals. Each one is idempotent and returns a count.
# --------------------------------------------------------------------------- #
def _remove_evaluations(session_id: str) -> int:
    if not session_id:
        return 0
    removed = 0
    for record in evaluations.list_for_session(session_id):
        if evaluations.delete(record.evaluation_id):
            removed += 1
    return removed


def _remove_pilot_reviews(evaluation_ids: list[str]) -> int:
    removed = 0
    for evaluation_id in evaluation_ids:
        removed += pilot.delete_reviews(evaluation_id)
    return removed


def _remove_session(session_id: str) -> int:
    if not session_id or not session_store.exists(session_id):
        return 0
    session_store.delete(session_id)
    return 1


def _remove_session_trail(session_id: str) -> int:
    if not session_id:
        return 0
    return 1 if audit.delete_session_trail(session_id) else 0


def _remove_lock(session_id: str) -> int:
    """The evaluation serialisation lock. Holds only the session id, but it is
    named after the candidate's session and there is no reason to keep it."""
    if not session_id:
        return 0
    path = config.DATA_DIR / "evaluations" / ".locks" / f"{session_id}.lock"
    if not path.exists():
        return 0
    path.unlink()
    return 1


def _redact_product_log(session_id: str) -> int:
    if not session_id:
        return 0
    return audit.redact_product_outcomes(session_id, _OUTCOME_FIELDS, REDACTED)


def _redact_invitation(invite: Invite) -> None:
    """Leave a contentless tombstone rather than removing the row.

    Three reasons the row stays: the audit trail references this token and a
    dangling reference proves nothing; a removed row could be re-created by a
    replayed request; and the token itself must stay revoked so the emailed link
    is dead. What goes is everything that identifies a person.
    """
    invite.candidate_name = ""
    invite.candidate_id = ""
    invite.recipient = ""
    invite.note = ""
    invite.session_id = None
    if invite.status != "revoked":
        invite.status = "revoked"
        invite.revoked_at = invite.revoked_at or time.time()


# --------------------------------------------------------------------------- #
#  Verification — the part that makes the claim honest
# --------------------------------------------------------------------------- #
def verify(token: str) -> list[str]:
    """Every location that still holds something for this candidate.

    Empty means erased. Run after every erasure, and available on its own so an
    operator can check a record without touching it.

    Deliberately re-reads from the stores rather than trusting the return values
    of the deletions: "delete said it worked" is exactly the claim under test.
    """
    invite = invites.get(token)
    if invite is None:
        return []                       # no row at all: nothing to hold data

    remaining: list[str] = []
    session_id = invite.session_id or ""

    if invite.candidate_name or invite.candidate_id or invite.recipient or invite.note:
        remaining.append("invitation")

    if session_id:
        if session_store.exists(session_id):
            remaining.append("session_state")
        if audit.session_trail_exists(session_id):
            remaining.append("session_trail")
        if evaluations.list_for_session(session_id):
            remaining.append("evaluations")
        if (config.DATA_DIR / "evaluations" / ".locks" / f"{session_id}.lock").exists():
            remaining.append("evaluation_lock")
        if audit.product_outcomes_present(session_id, _OUTCOME_FIELDS):
            remaining.append("product_log_outcomes")
        if pilot.reviews_for_session(session_id):
            remaining.append("pilot_reviews")
    return remaining


# --------------------------------------------------------------------------- #
#  The workflow
# --------------------------------------------------------------------------- #
def erase(token: str, *, actor: str = "", organization_id: str = "") -> Outcome:
    """Erase one candidate's data. Idempotent, verified, audited, retryable.

    Idempotent in the way that matters: running it on an already-erased record
    does not fail, does not recreate anything, and does not write a second
    "erased" audit event. Running it on a partially-erased record finishes the
    job.

    Never reports success it cannot verify. If anything remains, the record
    lands in `DELETION_FAILED` with the locations named, and the same call can
    be made again.
    """
    invite = invites.get(token)
    if invite is None:
        return Outcome(token=token, session_id="", interview_id="", ok=False,
                       lifecycle="unknown", error="no such invitation")

    session_id = invite.session_id or ""
    interview_id = invite.interview_id

    if invite.lifecycle == retention.DELETED:
        # Already done. Verify anyway — cheap, and the one way to notice that a
        # record marked deleted is not actually empty.
        remaining = verify(token)
        if not remaining:
            return Outcome(
                token=token, session_id=session_id, interview_id=interview_id,
                ok=True, lifecycle=retention.DELETED,
                attempts=invite.deletion_attempts, already_deleted=True,
            )
        # It said deleted and it is not. Reopen it rather than repeat the lie.
        invite.lifecycle = retention.DELETION_FAILED

    invite.deletion_attempts += 1
    invite.deletion_requested_at = invite.deletion_requested_at or time.time()
    invites.update(invite)

    removed: dict[str, int] = {}
    try:
        # Leaves first. Evaluation ids are captured before the records go, so
        # their pilot reviews can still be found.
        evaluation_ids = [r.evaluation_id for r in evaluations.list_for_session(session_id)] if session_id else []
        removed["evaluations"] = _remove_evaluations(session_id)
        removed["pilot_reviews"] = _remove_pilot_reviews(evaluation_ids)
        removed["session_state"] = _remove_session(session_id)
        removed["session_trail"] = _remove_session_trail(session_id)
        removed["evaluation_lock"] = _remove_lock(session_id)
        removed["product_log_outcomes"] = _redact_product_log(session_id)

        # The anchor last, so a crash above leaves a findable record.
        _redact_invitation(invite)
        invites.update(invite)
    except Exception as exc:  # noqa: BLE001 — every failure must be reportable
        return _fail(invite, removed, _failure_reason(exc), actor, organization_id,
                     session_id, interview_id)

    remaining = verify(token)
    if remaining:
        return _fail(invite, removed, "verification found data still present",
                     actor, organization_id, session_id, interview_id, remaining)

    invite.lifecycle = retention.DELETED
    invite.deleted_at = time.time()
    invite.deletion_error = ""
    invite.deletion_remaining = []
    invites.update(invite)

    audit.product(
        audit.CANDIDATE_DATA_ERASED,
        actor=actor or "system",
        org_id=organization_id,
        subject_type="invitation",
        # Truncated: the audit trail must identify the record without carrying a
        # usable credential.
        subject_id=token[:8] + "…",
        session=session_id,
        interview_id=interview_id,
        removed=removed,
        attempts=invite.deletion_attempts,
    )
    return Outcome(
        token=token, session_id=session_id, interview_id=interview_id, ok=True,
        lifecycle=retention.DELETED, removed=removed,
        attempts=invite.deletion_attempts,
    )


def _fail(
    invite: Invite,
    removed: dict[str, int],
    reason: str,
    actor: str,
    organization_id: str,
    session_id: str,
    interview_id: str,
    remaining: list[str] | None = None,
) -> Outcome:
    """Record a partial erasure as a failure, loudly.

    The alternative — reporting success because most of it worked — is the
    single worst outcome available to this module, so the failure path writes
    state, writes an audit event, and returns `ok=False`.
    """
    still = remaining if remaining is not None else verify(invite.token)
    invite.lifecycle = retention.DELETION_FAILED
    invite.deletion_error = reason
    invite.deletion_remaining = still
    invites.update(invite)

    audit.product(
        audit.CANDIDATE_DATA_ERASURE_FAILED,
        actor=actor or "system",
        org_id=organization_id,
        subject_type="invitation",
        subject_id=invite.token[:8] + "…",
        session=session_id,
        interview_id=interview_id,
        removed=removed,
        remaining=still,
        reason=reason,
        attempts=invite.deletion_attempts,
    )
    return Outcome(
        token=invite.token, session_id=session_id, interview_id=interview_id,
        ok=False, lifecycle=retention.DELETION_FAILED, removed=removed,
        remaining=still, error=reason, attempts=invite.deletion_attempts,
    )


# --------------------------------------------------------------------------- #
#  The sweep
# --------------------------------------------------------------------------- #
def sweep(*, limit: int = 0, actor: str = "retention_sweep", dry_run: bool = False) -> dict[str, Any]:
    """Find expired candidate data and erase it. Safe to run twice, and safe to
    run while another copy is running — each erasure is idempotent and the
    stores serialise their own writes.

    Returns a report rather than printing one, so the same function backs the
    CLI, the API and the tests.
    """
    started = time.time()
    due = retention.eligible(limit=limit)
    results: list[dict[str, Any]] = []
    erased = failed = 0

    for candidate in due:
        token = candidate.token          # the full token; `to_dict` truncates
        if dry_run:
            results.append({"token": candidate.token, "would_erase": True,
                            "lifecycle": candidate.lifecycle})
            continue
        outcome = erase(token, actor=actor, organization_id=candidate.organization_id)
        results.append(outcome.to_dict())
        if outcome.ok:
            erased += 1
        else:
            failed += 1

    report = {
        "started_at": started,
        "duration_sec": round(time.time() - started, 3),
        "dry_run": dry_run,
        "eligible": len(due),
        "erased": erased,
        "failed": failed,
        "results": results,
    }
    if not dry_run and due:
        audit.product(
            audit.RETENTION_SWEEP_COMPLETED,
            actor=actor,
            subject_type="retention",
            subject_id="sweep",
            eligible=len(due), erased=erased, failed=failed,
            duration_sec=report["duration_sec"],
        )
    return report

