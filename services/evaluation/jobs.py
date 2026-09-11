"""Running an evaluation: the service boundary between an interview and a score.

    interview completes  ─►  request()  ─►  a pending record
                                              │
                          worker / recruiter  ▼
                                            run()  ─►  completed | failed

`request` is cheap and never calls a model: it freezes the snapshot and writes a
row. That is what lets the candidate's completion path call it — a candidate
finishing their interview must not wait on, or be affected by, the recruiter's
scoring pipeline. `run` is the expensive half and is called by whatever executes
work: an HTTP request from the recruiter console today, a worker process draining
`run_pending()` tomorrow. Nothing in here knows which, and no queue was invented
for a repository that does not have one.

Failure is deliberately visible. If the model cannot extract evidence, the record
is `failed` with `error_kind: "model"`; if the output does not survive the
integrity gate it is `failed` with `error_kind: "validation"`. Neither ever
becomes a `completed` record with a partial score, because a hiring document that
half-worked is indistinguishable from one that worked.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Any, Callable, Iterator

from packages.types.evaluation import ENGINE_VERSION, EvidenceItem
from services import config
from services.data import audit, evaluations
from services.data import sessions as store
from services.data.evaluations import EvaluationRecord
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV
from services.evaluation import integrity, snapshot, stub
from services.orchestrator.state import SessionState


class NotEvaluatable(RuntimeError):
    """This session cannot be evaluated at all — not a run failure."""


#: How long a `running` evaluation may stay running before a new request treats
#: it as dead. Deliberately far above the slowest real run observed (142s on
#: gpt-4.1-mini, of which one call took 102s), so a slow provider is waited for
#: and a killed process is recovered from.
STALE_RUN_SEC = 900


# --------------------------------------------------------------------------- #
#  One evaluation at a time, per session
#
#  `request` reads the current record and then writes a new one, which is a
#  read-modify-write over a shared store. Measured: four simultaneous requests
#  for one session produced four records, all of them "current" — so which one a
#  recruiter read was decided by a filesystem glob, and each of the four would
#  have called the model and billed for it.
#
#  Both halves take the lock, so two requests arriving together resolve to one
#  logical evaluation and one run. Threads contend through the in-process lock;
#  separate processes on the same host contend through `flock` on the same file.
#  Across HOSTS it guarantees nothing — that needs the durable store this build
#  does not have yet, and pretending otherwise here would be worse than saying
#  so.
# --------------------------------------------------------------------------- #
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _thread_lock(session_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(session_id, threading.Lock())


@contextlib.contextmanager
def _one_at_a_time(session_id: str) -> Iterator[None]:
    with _thread_lock(session_id):
        path = config.DATA_DIR / "evaluations" / ".locks"
        handle = None
        try:
            path.mkdir(parents=True, exist_ok=True)
            handle = (path / f"{session_id}.lock").open("a+")
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            # No flock (or nowhere to put the file): the in-process lock still
            # holds, which is what a single-process deployment needs.
            handle = None
        try:
            yield
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()


# --------------------------------------------------------------------------- #
#  Requesting
# --------------------------------------------------------------------------- #
def request(
    state: SessionState,
    *,
    requested_by: str = "system",
    force: bool = False,
) -> tuple[EvaluationRecord, bool]:
    """The current evaluation for this session, creating one if needed.

    Returns `(record, created)`. **Idempotent**: two requests for the same
    completed session under the same engine version resolve to one record rather
    than two competing answers, which is the whole point — a double-clicked
    button and a retried webhook are the same evaluation.

    A new record is minted only when something genuinely differs: the snapshot
    changed, the previous run failed, or a caller explicitly asked for a re-run.
    Re-evaluation is a NEW record that supersedes the old one; the old one stays
    on disk, because "we changed our mind" is a thing a hiring file has to be
    able to show.
    """
    if state.phase != "complete":
        raise NotEvaluatable(
            "This interview has not been completed, so there is nothing to evaluate."
        )

    with _one_at_a_time(state.session_id):
        return _request_locked(state, requested_by=requested_by, force=force)


def _request_locked(
    state: SessionState, *, requested_by: str, force: bool
) -> tuple[EvaluationRecord, bool]:
    snap = snapshot.build(state)          # raises SnapshotError if unpinned
    checksum = snapshot.checksum(snap)
    existing = evaluations.current_for(state.session_id, ENGINE_VERSION)

    if existing is not None and not force:
        unchanged = existing.snapshot_checksum == checksum
        # A run whose process died leaves the record in `running` for ever:
        # `run_pending` only drains `pending`, and a plain re-request returns the
        # stuck row unchanged. Past the ceiling — far beyond the slowest run
        # measured, 142s — it is treated as a failed attempt and retried, which
        # is the only way a recruiter recovers one without `force`.
        stale = (
            existing.status == evaluations.RUNNING
            and (time.time() - existing.updated_at) > STALE_RUN_SEC
        )
        if unchanged and existing.status != evaluations.FAILED and not stale:
            return existing, False
        reason = "stale_run" if stale else ("retry" if unchanged else "snapshot_changed")
    elif existing is not None:
        reason = "forced"
    else:
        reason = "first"

    attempt = 1
    if existing is not None:
        attempt = existing.attempt + 1
        evaluations.supersede(existing)
        if existing.status == evaluations.COMPLETED:
            # A completed evaluation being replaced is worth its own line: it is
            # the one case where what a recruiter previously read is no longer
            # the current answer.
            audit.product(
                audit.EVALUATION_INVALIDATED,
                actor=requested_by, subject_type="evaluation",
                subject_id=existing.evaluation_id,
                session=state.session_id, reason=reason,
            )
        elif existing.status == evaluations.FAILED:
            audit.product(
                audit.EVALUATION_RETRIED,
                actor=requested_by, subject_type="evaluation",
                subject_id=existing.evaluation_id,
                session=state.session_id, attempt=attempt,
            )

    record = evaluations.create(
        session_id=state.session_id,
        interview_id=state.interview_id,
        interview_version=state.interview_version,
        engine_version=ENGINE_VERSION,
        snapshot=snap,
        snapshot_checksum=checksum,
        requested_by=requested_by,
        attempt=attempt,
        pilot_run_id=state.pilot_run_id,
    )
    audit.product(
        audit.EVALUATION_REQUESTED,
        actor=requested_by, subject_type="evaluation",
        subject_id=record.evaluation_id,
        session=state.session_id,
        interview_id=state.interview_id,
        interview_version=state.interview_version,
        engine_version=ENGINE_VERSION,
        snapshot_checksum=checksum,
        attempt=attempt, reason=reason,
        pilot_run=state.pilot_run_id,
    )
    return record, True


def on_interview_completed(state: SessionState, *, requested_by: str = "runtime") -> None:
    """Queue an evaluation when the orchestrator ends an interview.

    Best-effort by design. This is called from the candidate's completion path,
    and a candidate must never see an error, a delay, or a different ending
    because the recruiter-side pipeline had a problem. Anything that goes wrong
    here leaves no evaluation, which the recruiter console can then request
    explicitly.
    """
    try:
        request(state, requested_by=requested_by)
    except Exception as exc:  # noqa: BLE001 — never propagates into the interview
        store.audit(
            state.session_id, "evaluation_request_failed", error=str(exc)[:200]
        )


# --------------------------------------------------------------------------- #
#  Running
# --------------------------------------------------------------------------- #
def _kind_for(exc: BaseException) -> str:
    """Model failure, or infrastructure failure?

    The gateway already knows the difference and raises distinct exceptions for
    it; this used to be thrown away, and every provider outage was recorded as
    `error_kind: "model"`. An operator reading a wall of "model" failures goes
    looking at prompts and calibration, when the actual cause is that the
    account is out of credit — which is a five-minute fix they never get to.
    """
    from services.ai.gateway import OutputTruncated, ProviderUnavailable, RateLimited

    if isinstance(exc, (ProviderUnavailable, RateLimited)):
        return evaluations.PROVIDER_FAILURE
    # A timeout or a transport failure surfaces as a plain AIError from some
    # call sites; the message is the only distinguisher available there.
    text = str(exc).lower()
    if any(marker in text for marker in (
        "key limit", "quota", "insufficient", "credit", "billing",
        "timed out", "timeout", "connection", "temporarily unavailable",
        "429", "401", "402", "403",
        # An unconfigured or offline gateway is infrastructure too. This one was
        # found the same way as the rest: an evaluation against a gateway with
        # no key recorded `error_kind: "model"`, which is the least useful thing
        # it could have said.
        "no provider configured", "provider unavailable",
    )):
        return evaluations.PROVIDER_FAILURE
    if isinstance(exc, OutputTruncated):
        # Not a quality failure: the model was not given room to finish. It is
        # still the model's stage, but it is a ceiling problem, not a reasoning
        # one — kept as MODEL_FAILURE so nothing downstream changes shape.
        return evaluations.MODEL_FAILURE
    return evaluations.MODEL_FAILURE


def _fail(
    record: EvaluationRecord, kind: str, message: str, *, stage: str = ""
) -> EvaluationRecord:
    record.status = evaluations.FAILED
    record.error_kind = kind
    record.error = message[:500]
    record.completed_at = time.time()
    record.failed_stage = stage
    # A failed evaluation carries no result. The result-assembly gate runs after
    # the payload has been built, and leaving the refused payload on the record
    # would mean anything that reads `record.result` without checking the status
    # could show an evaluation that was explicitly refused.
    record.result = {}
    # Which model was in play when it failed. Read the same way a successful run
    # reads it — from the gateway's telemetry — because "the extraction failed"
    # is not an answerable question without it.
    record.model_meta = _model_meta(record)
    evaluations.save(record)
    # Enough to answer, from this one line: which interview, which session,
    # which evaluation, at which stage, why, on which attempt, and how long it
    # ran before it gave up. Anything less and a failure in a week's log is a
    # thing you have to go and reconstruct by hand.
    audit.product(
        audit.EVALUATION_FAILED,
        subject_type="evaluation", subject_id=record.evaluation_id,
        session=record.session_id,
        interview_id=record.interview_id,
        interview_version=record.interview_version,
        engine_version=record.engine_version,
        attempt=record.attempt,
        stage=stage,
        error_kind=kind, error=record.error,
        provider=record.model_meta.get("provider", ""),
        model=record.model_meta.get("resolved_model", ""),
        calls=record.model_meta.get("calls", 0),
        pilot_run=record.pilot_run_id,
        duration_ms=int((record.completed_at - record.created_at) * 1000),
    )
    return record


def _model_meta(record: EvaluationRecord) -> dict[str, Any]:
    """Provider, model and cost for this run, from the gateway's telemetry.

    The gateway already writes one `ai_request` line per call onto the session's
    trail, carrying the model, latency and token counts and never the key. This
    reads back the ones belonging to this run and totals them, so the record can
    answer "which model produced this" without the evaluator having to pass
    metadata up through three call signatures.
    """
    from services.ai.gateway import Workload, workload_config

    cfg = workload_config(Workload.SCORING)
    calls = [
        row for row in audit.read(record.session_id)
        if row.get("event") == "ai_request"
        and row.get("workload") == Workload.SCORING.value
        and float(row.get("at") or 0) >= record.created_at
    ]
    resolved = sorted({row.get("model", "") for row in calls if row.get("model")})
    return {
        "provider": "openrouter" if calls else "none",
        "configured_model": cfg.model,
        # Normally one. A list makes a silent per-call switch visible instead of
        # hiding it behind whichever call happened to be last.
        "resolved_model": resolved[0] if len(resolved) == 1 else ", ".join(resolved),
        "calls": len(calls),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in calls),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in calls),
        "latency_ms": sum(int(row.get("latency_ms") or 0) for row in calls),
        "structured_modes": sorted(
            {row.get("structured_mode", "") for row in calls if row.get("structured_mode")}
        ),
    }


def _extractor() -> Callable[..., Any]:
    return stub.extract_for_question if stub.enabled() else EV.extract_for_question


def _judge() -> Callable[..., Any] | None:
    return stub.judge if stub.enabled() else None


def run(
    record: EvaluationRecord | str,
    *,
    extractor: Callable[..., Any] | None = None,
    judge: Callable[..., Any] | None = None,
) -> EvaluationRecord:
    """Execute one evaluation record. Safe to call twice.

    A record that already completed is returned untouched — re-running it would
    replace a document someone may already have read with a differently-worded
    one, and if a re-evaluation is actually wanted it goes through `request(...,
    force=True)` where it becomes a visible new run.
    """
    if isinstance(record, str):
        loaded = evaluations.get(record)
        if loaded is None:
            raise NotEvaluatable(f"No evaluation {record}.")
        record = loaded

    with _one_at_a_time(record.session_id):
        # Re-read inside the lock. The caller may be holding a copy taken before
        # another request ran this same record to completion, and re-running it
        # from that copy would spend a second model call to overwrite a document
        # someone may already have read.
        fresh = evaluations.get(record.evaluation_id)
        if fresh is not None:
            record = fresh
        if record.status == evaluations.COMPLETED:
            return record
        return _run_locked(record, extractor=extractor, judge=judge)


def _run_locked(
    record: EvaluationRecord,
    *,
    extractor: Callable[..., Any] | None = None,
    judge: Callable[..., Any] | None = None,
) -> EvaluationRecord:
    record.status = evaluations.RUNNING
    evaluations.save(record)
    audit.product(
        audit.EVALUATION_STARTED,
        subject_type="evaluation", subject_id=record.evaluation_id,
        session=record.session_id, engine_version=record.engine_version,
        attempt=record.attempt,
    )

    snap = record.snapshot
    transcript = snapshot.transcript_from(snap)
    definition = snapshot.definition_from(snap)
    extract_one = extractor or _extractor()
    judge_one = judge or _judge()
    # Wall-clock per stage. Total latency alone cannot answer the only question
    # worth asking about a slow evaluation — whether it was the extractions, the
    # judgements, or the code around them.
    stage_ms: dict[str, int] = {}
    stage_started = time.perf_counter()

    # ---- evidence ---------------------------------------------------------- #
    skills = {s.id: s for s in definition.skills}
    items: list[EvidenceItem] = []
    # What the extractor proposed and code refused, and what code corrected on
    # the way through. Both were being discarded here, which meant a fabricated
    # quote left no trace on the record it was kept out of.
    refused: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for question in transcript.questions:
        skill = skills.get(question.skill_id)
        if skill is None:
            continue  # a question whose skill this version does not list
        if not question.usable_turns():
            continue
        try:
            found, sub = extract_one(
                question, skill, definition, session_id=record.session_id
            )
        except EV.ExtractionError as exc:  # noqa: PERF203
            # Stricter than the library on purpose. `evidence.extract` tolerates
            # one question failing because a live interview should not lose a
            # skill to a hiccup; a PERSISTED evaluation that quietly dropped a
            # question would understate the candidate on it with nothing on the
            # page to say so. Nobody is waiting, so the honest move is to fail
            # and let it be re-run.
            return _fail(record, _kind_for(exc),
                         f"evidence extraction failed on {question.question_id}: {exc}",
                         stage="evidence_extraction")
        except Exception as exc:  # noqa: BLE001
            return _fail(record, _kind_for(exc),
                         f"evidence extraction failed on {question.question_id}: "
                         f"{type(exc).__name__}: {exc}",
                         stage="evidence_extraction")
        items.extend(found)
        for row in getattr(sub, "rejected", []):
            refused.append({**row, "stage": "extraction", "skill_id": skill.id})
        for row in getattr(sub, "repairs", []):
            repairs.append({**row, "skill_id": skill.id})

    stage_ms["evidence_extraction"] = int((time.perf_counter() - stage_started) * 1000)
    stage_started = time.perf_counter()

    kept, gated = integrity.check_evidence(items, snap)
    quarantined = refused + [{**row, "stage": "persistence"} for row in gated]

    # ---- assessment -------------------------------------------------------- #
    try:
        evaluation, report = E.evaluate(
            transcript, definition, kept,
            session_id=record.session_id, judge=judge_one,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(record, _kind_for(exc),
                     f"{type(exc).__name__}: {exc}", stage="skill_assessment")

    if report.failures:
        # A skill whose judge call failed would otherwise be persisted with
        # floor-constrained scores that no model produced.
        # `report.failures` are the judge calls that did not come back. They
        # carry the provider's own message, so the same classification applies.
        joined = "; ".join(report.failures)[:400]
        return _fail(record, _kind_for(RuntimeError(joined)), joined,
                     stage="skill_assessment")

    stage_ms["skill_assessment"] = int((time.perf_counter() - stage_started) * 1000)
    stage_started = time.perf_counter()

    # ---- the gate ---------------------------------------------------------- #
    try:
        integrity.require_persistable(evaluation, kept, snap)
    except integrity.IntegrityError as exc:
        record.quarantined = quarantined
        return _fail(record, evaluations.VALIDATION_FAILURE, "; ".join(exc.violations),
                     stage="integrity_gate")

    record.result = {
        **evaluation.to_dict(),
        "maximum_possible_score": evaluation.maximum_possible_score,
        "percentage": evaluation.percentage,
    }
    record.evidence = [item.to_dict() for item in kept]
    record.quarantined = quarantined
    record.adjustments = list(report.adjustments)
    record.repairs = repairs
    # Which model actually produced this, read back from the gateway's own
    # telemetry rather than threaded through every call signature. Without it,
    # "why does this evaluation look like that" is unanswerable a month later.
    record.model_meta = {**_model_meta(record), "stage_ms": stage_ms}
    record.status = evaluations.COMPLETED
    record.completed_at = time.time()

    # Assemble the recruiter-facing result BEFORE this record lands on disk as
    # `completed`. The integrity gate above checks the evaluation against its
    # evidence; this checks that the thing a recruiter will actually be shown
    # can be built from it and agrees with itself. A record that survives the
    # first and fails the second would sit in the console as a completed
    # assessment whose report is a 409 — which is exactly the half-worked
    # hiring document this module exists to refuse.
    from services.evaluation import result as R

    try:
        R.build_validated(record)
        stage_ms["result_assembly"] = int((time.perf_counter() - stage_started) * 1000)
    except R.ResultError as exc:
        return _fail(record, evaluations.VALIDATION_FAILURE,
                     "; ".join(exc.violations), stage="result_assembly")
    except Exception as exc:  # noqa: BLE001
        return _fail(record, evaluations.VALIDATION_FAILURE,
                     f"{type(exc).__name__}: {exc}", stage="result_assembly")

    evaluations.save(record)

    audit.product(
        audit.EVALUATION_COMPLETED,
        subject_type="evaluation", subject_id=record.evaluation_id,
        session=record.session_id,
        interview_id=record.interview_id,
        interview_version=record.interview_version,
        engine_version=record.engine_version,
        # Counts and outcomes only. The candidate's words stay in the session
        # transcript and the snapshot; there is no reason to copy them into a
        # log an auditor reads.
        skills=len(evaluation.skill_assessment),
        evidence_items=len(kept),
        quarantined=len(quarantined),
        adjustments=len(report.adjustments),
        repairs=len(repairs),
        model=record.model_meta.get("resolved_model", ""),
        provider=record.model_meta.get("provider", ""),
        attempt=record.attempt,
        pilot_run=record.pilot_run_id,
        stage_ms=record.model_meta.get("stage_ms", {}),
        model_latency_ms=record.model_meta.get("latency_ms", 0),
        duration_ms=int(((record.completed_at or time.time()) - record.created_at) * 1000),
        total_score=evaluation.candidate_details.total_score,
        maximum_possible_score=evaluation.maximum_possible_score,
        overall_rating=evaluation.candidate_details.overall_rating,
        recommendation=evaluation.recommendation,
    )
    return record


def request_and_run(
    state: SessionState,
    *,
    requested_by: str = "recruiter",
    force: bool = False,
    extractor: Callable[..., Any] | None = None,
    judge: Callable[..., Any] | None = None,
) -> EvaluationRecord:
    """What the recruiter console calls: get the record, run it if it is new."""
    record, _ = request(state, requested_by=requested_by, force=force)
    if record.status in (evaluations.PENDING, evaluations.RUNNING):
        record = run(record, extractor=extractor, judge=judge)
    return record


def run_pending(limit: int = 25, **kwargs: Any) -> list[EvaluationRecord]:
    """Drain queued evaluations. The entry point a worker or cron would call."""
    return [run(record, **kwargs) for record in evaluations.list_pending(limit)]
