"""Append-only audit — the record of what happened and why.

Two streams, because they answer different questions and have different
readers:

  `audit/<session_id>.jsonl`   one candidate's decision trail. Which question
                               was selected and why, every generated probe with
                               its guardrail verdict, every fallback, silence,
                               and unanswered item. This is what the reviewer
                               reads next to the transcript.

  `audit/_product.jsonl`       everything else: interviews created, generated,
                               edited, published, invitations minted, reports
                               produced. This is what an auditor reads.

Nothing is ever rewritten or deleted from either. An audit log you can edit is
a log nobody has to believe.

Secrets never enter this module. AI telemetry records the model, the latency,
the token counts and whether it worked — never the key, and never the prompt.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from services import config

# --------------------------------------------------------------------------- #
#  The canonical vocabulary (§17)
#
#  Runtime events keep the lower-case names the orchestrator has always written,
#  because the reviewer's decision trail renders them and a rename would break
#  every session already on disk. `CANONICAL` maps each to the product-level
#  name, so the two vocabularies are one vocabulary with two spellings rather
#  than two competing records.
# --------------------------------------------------------------------------- #
INTERVIEW_CREATED = "INTERVIEW_CREATED"
INTERVIEW_GENERATION_STARTED = "INTERVIEW_GENERATION_STARTED"
INTERVIEW_GENERATED = "INTERVIEW_GENERATED"
INTERVIEW_GENERATION_FAILED = "INTERVIEW_GENERATION_FAILED"
INTERVIEW_REGENERATED = "INTERVIEW_REGENERATED"
QUESTION_GENERATION_STARTED = "QUESTION_GENERATION_STARTED"
QUESTION_GENERATED = "QUESTION_GENERATED"
QUESTION_GENERATION_FAILED = "QUESTION_GENERATION_FAILED"
QUESTION_EDITED = "QUESTION_EDITED"
QUESTION_ADDED = "QUESTION_ADDED"
QUESTION_REMOVED = "QUESTION_REMOVED"
QUESTION_REGENERATED = "QUESTION_REGENERATED"
QUESTION_POOL_VALIDATED = "QUESTION_POOL_VALIDATED"
JOB_CREATED = "JOB_CREATED"
INTERVIEW_EDITED = "INTERVIEW_EDITED"
INTERVIEW_PUBLISH_STARTED = "INTERVIEW_PUBLISH_STARTED"
INTERVIEW_PUBLISHED = "INTERVIEW_PUBLISHED"
INTERVIEW_PUBLISH_FAILED = "INTERVIEW_PUBLISH_FAILED"
INVITATION_OPENED = "INVITATION_OPENED"
INVITATION_REVOKED = "INVITATION_REVOKED"
INVITATION_EXPIRED = "INVITATION_EXPIRED"
#: Authentication. The actor is the user id on success and `anonymous` on
#: failure, and neither line ever carries a password, a token or a header.
LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
LOGIN_FAILED = "LOGIN_FAILED"
LOGOUT = "LOGOUT"
ACCESS_DENIED = "ACCESS_DENIED"

SESSION_CREATED = "SESSION_CREATED"

# Data lifecycle. Deliberately on the PRODUCT log rather than the session's own
# trail: the session's trail is one of the things erasure deletes, so recording
# the erasure there would delete the proof along with the data.
CANDIDATE_DATA_ERASED = "CANDIDATE_DATA_ERASED"
CANDIDATE_DATA_ERASURE_FAILED = "CANDIDATE_DATA_ERASURE_FAILED"
RETENTION_SWEEP_COMPLETED = "RETENTION_SWEEP_COMPLETED"
#: The candidate's device check, reported by the browser before a session
#: exists. Advisory: it gates nothing, and the server never trusts it.
SYSTEM_CHECK_REPORTED = "SYSTEM_CHECK_REPORTED"
INTERVIEW_VERSION_CREATED = "INTERVIEW_VERSION_CREATED"
INVITATION_CREATED = "INVITATION_CREATED"
SESSION_STARTED = "SESSION_STARTED"
QUESTION_SELECTED = "QUESTION_SELECTED"
QUESTION_DELIVERED = "QUESTION_DELIVERED"
CANDIDATE_TURN_RECEIVED = "CANDIDATE_TURN_RECEIVED"
ANSWER_CLASSIFIED = "ANSWER_CLASSIFIED"
PROBE_GENERATED = "PROBE_GENERATED"
PROBE_ACCEPTED = "PROBE_ACCEPTED"
PROBE_REJECTED = "PROBE_REJECTED"
QUESTION_ADVANCED = "QUESTION_ADVANCED"
INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
SCORE_GENERATED = "SCORE_GENERATED"
REPORT_GENERATED = "REPORT_GENERATED"
AI_REQUEST = "AI_REQUEST"

# Evaluation lifecycle. `SCORE_GENERATED` above belongs to the cue-coverage
# scorer and is left alone: the two systems produce different artefacts and an
# auditor has to be able to tell which one wrote a line.
EVALUATION_REQUESTED = "EVALUATION_REQUESTED"
EVALUATION_STARTED = "EVALUATION_STARTED"
EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
EVALUATION_FAILED = "EVALUATION_FAILED"
EVALUATION_INVALIDATED = "EVALUATION_INVALIDATED"
EVALUATION_RETRIED = "EVALUATION_RETRIED"

#: runtime spelling → canonical name
CANONICAL: dict[str, str] = {
    "session_started": SESSION_STARTED,
    "item_selected": QUESTION_SELECTED,
    "answer_read": ANSWER_CLASSIFIED,
    "probe_generated": PROBE_GENERATED,
    "probe_fallback": PROBE_ACCEPTED,
    "probe_exhausted": PROBE_REJECTED,
    "session_complete": INTERVIEW_COMPLETED,
    "score_generated": SCORE_GENERATED,
    "report_generated": REPORT_GENERATED,
}


def _session_path(session_id: str):
    return config.AUDIT_DIR / f"{session_id}.jsonl"


_PRODUCT_LOG = "_product"


def _append(session_id: str, record: dict[str, Any]) -> None:
    path = _session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
#  Writing
# --------------------------------------------------------------------------- #
#: The pre-authentication placeholder for "a recruiter did this".
_LEGACY_RECRUITER_ACTOR = "recruiter"


def emit(
    event: str,
    *,
    session_id: str = _PRODUCT_LOG,
    actor: str = "",
    actor_type: str = "",
    subject_type: str = "",
    subject_id: str = "",
    org_id: str = "",
    **data: Any,
) -> dict[str, Any]:
    """Record one fact. Returns the record so a caller can echo it back.

    `actor`, `actor_type` and `org_id` fall back to the authenticated principal
    of the current request when the caller does not name them, so every
    recruiter-side line identifies who did it and which organization they did it
    in without forty call sites having to remember. An explicit value always
    wins — the candidate paths name their own actor, and a background job names
    none and is recorded as `system`.

    Nothing that reaches here may be a credential. There is no code path that
    puts a password, a session token, an invitation token or an Authorization
    header into an audit record, and `tests/test_security.py` asserts it.
    """
    context_type, context_id, context_org = _security_context()
    # `actor="recruiter"` predates authentication: it named a role because there
    # was no user to name. Treated as unset so the real principal is recorded
    # instead — an audit line that says "recruiter" answers no question anyone
    # asks of an audit log. Kept as a translation rather than edited into forty
    # call sites, so a missed one cannot quietly go back to being anonymous.
    if actor == _LEGACY_RECRUITER_ACTOR and context_id:
        actor = ""
    record = {
        "id": "evt_" + uuid.uuid4().hex[:12],
        "at": time.time(),
        "event": event,
        "canonical": CANONICAL.get(event, event),
        "actor": actor or context_id or "system",
        "actor_type": actor_type or (context_type if not actor else "") or _guess_type(actor),
        **({"org_id": org_id or context_org} if (org_id or context_org) else {}),
        **({"subject_type": subject_type} if subject_type else {}),
        **({"subject_id": subject_id} if subject_id else {}),
        **data,
    }
    _append(session_id, record)
    return record


def _security_context() -> tuple[str, str, str]:
    """The current principal, if this is a request from a signed-in user."""
    try:
        from services.security import context

        return context.actor()
    except Exception:  # noqa: BLE001 — the trail must never fail on its own metadata
        return "", "", ""


def _guess_type(actor: str) -> str:
    """What kind of actor a caller-supplied name refers to."""
    if actor.startswith("usr_"):
        return "user"
    if actor in ("candidate", "runtime") or actor.startswith("cand_"):
        return "candidate"
    return "system"


def session(session_id: str, event: str, **fields: Any) -> None:
    """The runtime's entry point — one line on one candidate's trail.

    Signature-compatible with the orchestrator's original `store.audit`, so the
    turn loop and the reviewer's decision trail both keep working unchanged.
    """
    _append(
        session_id,
        {"at": time.time(), "event": event, "canonical": CANONICAL.get(event, event), **fields},
    )


def product(event: str, **fields: Any) -> dict[str, Any]:
    """A recruiter-side or system fact, on the product-wide log."""
    return emit(event, session_id=_PRODUCT_LOG, **fields)


def ai_call(session_id: str, **meta: Any) -> None:
    """Gateway telemetry: workload, model, latency, tokens, success/failure.

    Written to the session's own trail when a session is in play, so "that turn
    took nine seconds" and "that turn selected question csr-esc-02" are the same
    story rather than two logs someone has to join by hand.
    """
    _append(
        session_id or _PRODUCT_LOG,
        {"at": time.time(), "event": "ai_request", "canonical": AI_REQUEST, **meta},
    )


# --------------------------------------------------------------------------- #
#  Reading
# --------------------------------------------------------------------------- #
def read(session_id: str) -> list[dict[str, Any]]:
    path = _session_path(session_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn final line never hides the rest of the trail
    return out


def read_product(limit: int = 500) -> list[dict[str, Any]]:
    return read(_PRODUCT_LOG)[-limit:]


# --------------------------------------------------------------------------- #
#  Erasure support
#
#  The trail is append-only everywhere else in this module, and these three
#  functions are the exception. They exist because an audit log that outlives
#  the data it describes must not itself become the surviving copy of that data.
#  The rule they follow: a row is never removed and never reordered — only the
#  named fields inside it are replaced.
# --------------------------------------------------------------------------- #
def session_trail_exists(session_id: str) -> bool:
    return _session_path(session_id).exists()


def delete_session_trail(session_id: str) -> bool:
    """Remove one candidate's decision trail.

    It holds no verbatim answers — the classifier writes labels and a word count
    — but it does hold Tara's generated probes, which are written FROM an answer
    and can paraphrase it closely. It goes with the transcript.
    """
    path = _session_path(session_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def _product_rows_mentioning(session_id: str) -> list[dict[str, Any]]:
    return [r for r in read_product(limit=1_000_000)
            if r.get("session") == session_id or r.get("subject_id") == session_id]


def product_outcomes_present(session_id: str, fields: tuple[str, ...]) -> bool:
    """Does the product log still carry an assessment outcome for this session?"""
    for row in _product_rows_mentioning(session_id):
        for name in fields:
            if name in row and row[name] != "[erased]":
                return True
    return False


def redact_product_outcomes(
    session_id: str, fields: tuple[str, ...], marker: str = "[erased]"
) -> int:
    """Replace outcome fields in place, keeping every row.

    The product log is the security trail: it must be able to prove that a
    deletion happened, which a log with rows cut out of it cannot do. So the
    event, its timestamp, its actor and its identifiers all survive — what goes
    is the score, the rating and the recommendation, which are the candidate's
    data rather than the deployment's.

    Rewritten atomically through a temporary file, because a torn rewrite of the
    audit log would be worse than the leak it was fixing.
    """
    path = _session_path(_PRODUCT_LOG)
    if not path.exists():
        return 0

    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    redacted = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)          # a torn line is left exactly as found
            continue
        if row.get("session") == session_id or row.get("subject_id") == session_id:
            touched = False
            for name in fields:
                if name in row and row[name] != marker:
                    row[name] = marker
                    touched = True
            if touched:
                redacted += 1
        out.append(json.dumps(row, ensure_ascii=False))

    if redacted:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(path)
    return redacted
