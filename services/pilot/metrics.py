"""What the pilot can measure, computed from what the product already records.

Nothing here instruments anything. Every figure is derived from three places
that were already being written for their own reasons:

    data/sessions/*.json        the interview: turns, records, phase, timings
    data/audit/<session>.jsonl  the decision trail: silences, repeats, skips,
                                clarifications, flags, and every model call
    data/evaluations/*.json     the assessment: scores, coverage, evidence,
                                quarantine, model, tokens, latency

That constraint is deliberate. A metric that needs its own event stream is a
metric that will disagree with the product the first time one of them changes,
and a pilot whose numbers disagree with the record is worse than a pilot with
fewer numbers.

Two things this module refuses to do:

  * **No candidate content leaves it.** Aggregates carry counts, ids and
    durations. Answers, quotes, names and emails do not appear in any summary,
    alert or dataset row (§3, §23).
  * **No opinion is turned into a fact.** A recruiter's disagreement is reported
    as a disagreement — never folded into a quality score, and never written
    back into an evaluation (§12, §13).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

from packages.types.evaluation import ENGINE_VERSION
from services.data import audit, evaluations, pilot
from services.data import sessions as store
from services.data.evaluations import EvaluationRecord

# --------------------------------------------------------------------------- #
#  Proposed thresholds
#
#  PROPOSED, not established. There is no production history to derive a bound
#  from, so each of these is a number chosen to be obviously wrong rather than
#  subtly wrong: they exist to make a bad run visible, and the pilot's own data
#  is what will replace them. Every alert says which threshold it fired on.
# --------------------------------------------------------------------------- #
THRESHOLDS: dict[str, float] = {
    # Technical
    "evaluation_failure_rate": 0.10,      # >10% of runs failing
    "interview_completion_rate": 0.80,    # <80% of started interviews finishing
    "evaluation_latency_p95_sec": 120.0,  # slower than the worst measured run
    "provider_error_rate": 0.05,          # non-success model calls
    # AI quality
    "evidence_quarantine_rate": 0.10,     # refused evidence, per evaluation
    "zero_evidence_rate": 0.10,           # completed evaluations with no evidence
    "recommendation_instability_rate": 0.0,   # any flip on identical input
    "reviewer_disagreement_rate": 0.30,   # >30% of reviewed assessments
}


def _owned_interviews(organization_id: str) -> set[str] | None:
    """The interviews one organization owns, or None for "no filter".

    None rather than an empty set for the unfiltered case: an empty set is a
    legitimate answer (an organization with no interviews) and conflating the
    two would turn a tenant with nothing into a tenant with everything.
    """
    if not organization_id:
        return None
    from services.data import interviews

    return {c.id for c in interviews.list_all() if c.organization_id == organization_id}


def _sessions(pilot_run_id: str = "", organization_id: str = "") -> list:
    owned = _owned_interviews(organization_id)
    out = []
    for session_id in store.list_ids():
        state = store.try_load(session_id)
        if state is None:
            continue
        if pilot_run_id and state.pilot_run_id != pilot_run_id:
            continue
        if owned is not None and state.interview_id not in owned:
            continue
        out.append(state)
    return out


def _records(pilot_run_id: str = "", organization_id: str = "") -> list[EvaluationRecord]:
    rows = evaluations.list_all()
    if pilot_run_id:
        rows = [r for r in rows if r.pilot_run_id == pilot_run_id]
    owned = _owned_interviews(organization_id)
    if owned is not None:
        rows = [r for r in rows if r.interview_id in owned]
    return rows


def _persona(state) -> str:
    """The rehearsal label on this session's invitation, if there is one.

    Read from the invitation's note rather than from anything the candidate
    supplied, and only the label: nothing else on that record — the name, the
    address it was sent to, the token — comes anywhere near a dataset row.
    """
    from services.data import invites

    invite = invites.get(state.invite_token) if state.invite_token else None
    note = (invite.note if invite else "") or ""
    return note.split("persona:", 1)[1].strip()[:32] if "persona:" in note else ""


def _stats(values: Iterable[float]) -> dict[str, float]:
    """Mean / median / p95 / max, with p95 withheld on a small sample.

    Below twenty points a "p95" is the second-largest value wearing a percentile
    for a hat. Reporting it anyway is how a pilot of six runs ends up in a
    capacity plan, so it is None until the sample can carry it (§16).
    """
    data = sorted(float(v) for v in values)
    if not data:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p95": None, "max": 0.0}
    p95: float | None = None
    if len(data) >= 20:
        p95 = statistics.quantiles(data, n=20)[-1]
    return {
        "n": len(data),
        "mean": round(statistics.fmean(data), 3),
        "median": round(statistics.median(data), 3),
        "p95": round(p95, 3) if p95 is not None else None,
        "max": round(data[-1], 3),
    }


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


# --------------------------------------------------------------------------- #
#  Candidate experience
# --------------------------------------------------------------------------- #
def candidate_experience(pilot_run_id: str = "", organization_id: str = "") -> dict[str, Any]:
    """The funnel and the friction, from sessions and their trails.

    "Attempted" counts sessions, so it starts at the moment a candidate consents
    — an invitation opened and abandoned before that shows up as the gap between
    `invitations_opened` and `attempted`, which is a different failure and worth
    keeping separate.
    """
    states = _sessions(pilot_run_id, organization_id)
    completed = [s for s in states if s.phase == "complete"]
    durations = [
        s.completed_at - s.created_at
        for s in completed
        if s.completed_at and s.created_at
    ]

    repeats = clarifies = skips = silences = rejoins = flagged = errors = 0
    answered_items = 0
    for state in states:
        for row in audit.read(state.session_id):
            event = row.get("event")
            if event == "repeated":
                repeats += 1
            elif event == "clarified":
                clarifies += 1
            elif event == "item_skipped":
                skips += 1
            elif event == "silence":
                silences += 1
            elif event == "resumed":
                rejoins += 1
            elif event == "candidate_turn_flagged":
                flagged += 1
            elif event in ("turn_failed", "llm_read_failed", "probe_generation_failed"):
                errors += 1
        answered_items += sum(1 for r in state.records.values() if r.closed_at)

    product = audit.read_product(limit=100_000)
    prechecks = [
        row for row in product
        if row.get("event") == audit.SYSTEM_CHECK_REPORTED
        and (not pilot_run_id or row.get("pilot_run") == pilot_run_id)
    ]
    precheck_failed = sum(1 for row in prechecks if row.get("outcome") == "failed")
    precheck_text = sum(1 for row in prechecks if row.get("outcome") == "text")

    return {
        "attempted": len(states),
        "completed": len(completed),
        "completion_rate": _rate(len(completed), len(states)),
        # Started and never finished. Not the same as "gave up": a session still
        # inside its rejoin window may yet come back, so this is a snapshot.
        "dropped": len(states) - len(completed),
        "drop_off_rate": _rate(len(states) - len(completed), len(states)),
        "system_check_reported": len(prechecks),
        "system_check_failed": precheck_failed,
        "system_check_failure_rate": _rate(precheck_failed, len(prechecks)),
        "system_check_chose_text": precheck_text,
        "rejoins": rejoins,
        "rejoin_rate": _rate(rejoins, len(states)),
        "repeats": repeats,
        "clarifications": clarifies,
        "skips": skips,
        "silences": silences,
        "answered_items": answered_items,
        "repeat_rate_per_item": _rate(repeats, answered_items),
        "clarify_rate_per_item": _rate(clarifies, answered_items),
        "skip_rate_per_item": _rate(skips, answered_items),
        "flagged_turns": flagged,
        "runtime_errors": errors,
        "duration_sec": _stats(durations),
    }


# --------------------------------------------------------------------------- #
#  Evaluation quality
# --------------------------------------------------------------------------- #
def evaluation_quality(pilot_run_id: str = "", organization_id: str = "") -> dict[str, Any]:
    rows = _records(pilot_run_id, organization_id)
    current = [r for r in rows if not r.superseded]
    completed = [r for r in rows if r.status == evaluations.COMPLETED]
    failed = [r for r in rows if r.status == evaluations.FAILED]

    by_stage: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for row in failed:
        by_stage[row.failed_stage or "unknown"] = by_stage.get(row.failed_stage or "unknown", 0) + 1
        by_kind[row.error_kind or "unknown"] = by_kind.get(row.error_kind or "unknown", 0) + 1

    evidence_counts = [len(r.evidence) for r in completed]
    quarantined = [len(r.quarantined) for r in completed]
    repairs = [len(r.repairs) for r in completed]
    constraints = [len(r.adjustments) for r in completed]
    coverage = [
        (r.result.get("coverage") or {}).get("coverage_percentage", 0.0) for r in completed
    ]
    percentages = [r.result.get("percentage", 0.0) for r in completed]

    recommendations: dict[str, int] = {}
    for row in completed:
        name = row.result.get("recommendation", "")
        recommendations[name] = recommendations.get(name, 0) + 1

    # Traceability: every quote on a completed evaluation must be findable in the
    # frozen snapshot it was taken from. Re-running it here is how the pilot
    # notices a gate that stopped working rather than trusting that it did — but
    # it runs the GATE'S OWN rule, `quote_is_real`, rather than a second one.
    # Written first as a plain substring test, it reported seven fabrications
    # that were all the same faithful quote missing a trailing comma; two
    # definitions of "verbatim" produce false alarms, and a false alarm on the
    # fabrication check is the most expensive kind.
    from services.evaluation.evidence import quote_is_real

    untraceable = 0
    checked = 0
    for row in completed:
        answers = " \n".join(t.get("answer", "") for t in row.snapshot.get("turns", []))
        for item in row.evidence:
            checked += 1
            if not quote_is_real(item.get("candidate_quote", ""), answers):
                untraceable += 1

    return {
        "requested": len(rows),
        "current": len(current),
        "completed": len(completed),
        "failed": len(failed),
        "completion_rate": _rate(len(completed), len(rows)),
        "failure_rate": _rate(len(failed), len(rows)),
        "failures_by_stage": by_stage,
        "failures_by_kind": by_kind,
        "evidence_items": _stats(evidence_counts),
        "evidence_untraceable": untraceable,
        "evidence_checked": checked,
        "evidence_traceability_rate": _rate(checked - untraceable, checked),
        "quarantined": _stats(quarantined),
        "quarantine_rate": _rate(sum(1 for q in quarantined if q), len(completed)),
        "zero_evidence_evaluations": sum(1 for c in evidence_counts if c == 0),
        "repairs": _stats(repairs),
        "constraints_applied": _stats(constraints),
        "coverage_percentage": _stats(coverage),
        "score_percentage": _stats(percentages),
        "recommendation_distribution": recommendations,
    }


# --------------------------------------------------------------------------- #
#  Recommendation stability
# --------------------------------------------------------------------------- #
def stability(pilot_run_id: str = "", organization_id: str = "") -> dict[str, Any]:
    """Where the same frozen input was evaluated more than once, did it agree?

    Grouped by `(session, snapshot_checksum, engine_version)` — the exact
    definition of "the same evaluation twice". Two runs that read different
    transcripts are not evidence of instability, and lumping them together would
    manufacture some.
    """
    groups: dict[tuple[str, str, str], list[EvaluationRecord]] = {}
    for row in _records(pilot_run_id, organization_id):
        if row.status != evaluations.COMPLETED:
            continue
        groups.setdefault(
            (row.session_id, row.snapshot_checksum, row.engine_version), []
        ).append(row)

    repeated = {k: v for k, v in groups.items() if len(v) > 1}
    flips: list[dict[str, Any]] = []
    score_spreads: list[float] = []
    for (session_id, checksum, engine), runs in repeated.items():
        recommendations = {r.result.get("recommendation", "") for r in runs}
        scores = [r.result.get("percentage", 0.0) for r in runs]
        spread = max(scores) - min(scores)
        score_spreads.append(spread)
        if len(recommendations) > 1:
            flips.append({
                "session_id": session_id,
                "snapshot_checksum": checksum,
                "engine_version": engine,
                "runs": len(runs),
                "recommendations": sorted(recommendations),
                "score_percentages": scores,
                "score_spread_pp": round(spread, 2),
                # The classification from §8, decided by the numbers rather than
                # by preference: a flip that comes with a large score move is a
                # different verdict about a different reading; a flip on a
                # near-identical score is the threshold, not the candidate.
                "class": "boundary_instability" if spread <= 2.0 else "scoring_variance",
            })

    return {
        "sessions_evaluated_more_than_once": len(repeated),
        "recommendation_flips": len(flips),
        "instability_rate": _rate(len(flips), len(repeated)),
        "score_spread_pp": _stats(score_spreads),
        "flips": flips,
    }


# --------------------------------------------------------------------------- #
#  Human review
# --------------------------------------------------------------------------- #
def human_review(pilot_run_id: str = "", organization_id: str = "") -> dict[str, Any]:
    reviews = pilot.list_reviews()
    if pilot_run_id:
        reviews = [r for r in reviews if r.pilot_run_id == pilot_run_id]
    if organization_id:
        # A review is owned by the evaluation it is about, which is owned by an
        # interview. Reviewers from another organization are not in this number.
        mine = {r.evaluation_id for r in _records(pilot_run_id, organization_id)}
        reviews = [r for r in reviews if r.evaluation_id in mine]
    if not reviews:
        return {
            "reviewed": 0, "agree": 0, "disagree": 0, "needs_review": 0,
            "agreement_rate": 0.0, "disagreement_rate": 0.0,
            "recommendation_stated": 0, "recommendation_agreement_rate": 0.0,
            "reasons": {}, "reviewers": 0,
        }

    agree = [r for r in reviews if r.verdict == "agree"]
    disagree = [r for r in reviews if r.verdict == "disagree"]
    needs = [r for r in reviews if r.verdict == "needs_review"]

    reasons: dict[str, int] = {}
    for row in disagree:
        for reason in row.reasons:
            reasons[reason] = reasons.get(reason, 0) + 1

    # Recommendation agreement is counted separately from overall agreement: a
    # reviewer can accept the assessment and still have called the decision
    # differently, and those two disagreements need different fixes.
    stated = [r for r in reviews if r.recommendation]
    same = [r for r in stated if r.recommendation == r.ai_recommendation]

    return {
        "reviewed": len(reviews),
        "agree": len(agree),
        "disagree": len(disagree),
        "needs_review": len(needs),
        "agreement_rate": _rate(len(agree), len(reviews)),
        "disagreement_rate": _rate(len(disagree), len(reviews)),
        "recommendation_stated": len(stated),
        "recommendation_agreement_rate": _rate(len(same), len(stated)),
        # Which part of the assessment the disagreements were about. This is the
        # next calibration dataset's index.
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "reviewers": len({r.reviewer for r in reviews}),
    }


# --------------------------------------------------------------------------- #
#  Cost and latency
# --------------------------------------------------------------------------- #
def _price(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD for one call's tokens, from `evals/models.yaml`.

    Priced from the same file the model comparison used, so a pilot cost and a
    benchmark cost are the same arithmetic. A model with no pricing row costs
    0.0 and is reported as unpriced rather than guessed at.
    """
    try:
        from evals import config as eval_config

        return eval_config.load().spec(model).cost_usd(prompt_tokens, completion_tokens)
    except Exception:  # noqa: BLE001 — pricing is reporting, never a hard failure
        return 0.0


def cost_and_latency(pilot_run_id: str = "", organization_id: str = "") -> dict[str, Any]:
    rows = [r for r in _records(pilot_run_id, organization_id) if r.status == evaluations.COMPLETED]
    calls, prompt, completion, latency, costs = [], [], [], [], []
    stage_totals: dict[str, list[float]] = {}
    unpriced: set[str] = set()

    for row in rows:
        meta = row.model_meta or {}
        model = meta.get("resolved_model", "") or meta.get("configured_model", "")
        calls.append(meta.get("calls", 0))
        prompt.append(meta.get("prompt_tokens", 0))
        completion.append(meta.get("completion_tokens", 0))
        latency.append((meta.get("latency_ms", 0) or 0) / 1000)
        cost = _price(model, meta.get("prompt_tokens", 0), meta.get("completion_tokens", 0))
        if model and cost == 0.0 and meta.get("prompt_tokens"):
            unpriced.add(model)
        costs.append(cost)
        for stage, value in (meta.get("stage_ms") or {}).items():
            stage_totals.setdefault(stage, []).append(value / 1000)

    # The runtime half: what the interview itself spent while the candidate was
    # waiting. Read from the same telemetry, filtered to the workloads that run
    # inside a turn.
    runtime_latency, runtime_cost, runtime_calls = [], [], []
    for state in _sessions(pilot_run_id, organization_id):
        turns = [
            row for row in audit.read(state.session_id)
            if row.get("event") == "ai_request" and row.get("workload") != "scoring"
        ]
        if not turns:
            continue
        runtime_calls.append(len(turns))
        runtime_latency.append(sum(int(r.get("latency_ms") or 0) for r in turns) / 1000)
        runtime_cost.append(_price(
            turns[0].get("model", ""),
            sum(int(r.get("prompt_tokens") or 0) for r in turns),
            sum(int(r.get("completion_tokens") or 0) for r in turns),
        ))

    slowest_call = 0
    provider_errors = total_calls = 0
    for state in _sessions(pilot_run_id, organization_id):
        for row in audit.read(state.session_id):
            if row.get("event") != "ai_request":
                continue
            total_calls += 1
            slowest_call = max(slowest_call, int(row.get("latency_ms") or 0))
            if not row.get("success", True):
                provider_errors += 1

    return {
        "evaluations": len(rows),
        "model_calls": _stats(calls),
        "prompt_tokens": _stats(prompt),
        "completion_tokens": _stats(completion),
        "evaluation_latency_sec": _stats(latency),
        "evaluation_cost_usd": _stats(costs),
        "evaluation_stage_sec": {k: _stats(v) for k, v in sorted(stage_totals.items())},
        "runtime_calls_per_interview": _stats(runtime_calls),
        "runtime_latency_sec": _stats(runtime_latency),
        "runtime_cost_usd": _stats(runtime_cost),
        "interview_total_cost_usd": round(
            (_stats(costs)["mean"] or 0) + (_stats(runtime_cost)["mean"] or 0), 4
        ),
        "slowest_single_call_ms": slowest_call,
        "provider_error_rate": _rate(provider_errors, total_calls),
        "unpriced_models": sorted(unpriced),
    }


# --------------------------------------------------------------------------- #
#  Alerts
# --------------------------------------------------------------------------- #
@dataclass
class Alert:
    key: str
    severity: str          # technical | quality
    observed: float
    threshold: float
    detail: str = ""
    subjects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "severity": self.severity, "observed": self.observed,
            "threshold": self.threshold, "detail": self.detail, "subjects": self.subjects,
        }


def alerts(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Which proposed thresholds this run crossed.

    Deliberately a list in a report rather than a paging system: the pilot is
    three candidates on one host, and the person who would be paged is the
    person running it.
    """
    out: list[Alert] = []
    experience = summary["candidate_experience"]
    quality = summary["evaluation_quality"]
    money = summary["cost_and_latency"]
    stab = summary["stability"]
    review = summary["human_review"]

    if quality["requested"] and quality["failure_rate"] > THRESHOLDS["evaluation_failure_rate"]:
        out.append(Alert(
            "evaluation_failure_rate", "technical",
            quality["failure_rate"], THRESHOLDS["evaluation_failure_rate"],
            f"{quality['failed']} of {quality['requested']} runs failed: "
            + ", ".join(f"{k}×{v}" for k, v in quality["failures_by_stage"].items()),
        ))
    if experience["attempted"] and \
            experience["completion_rate"] < THRESHOLDS["interview_completion_rate"]:
        out.append(Alert(
            "interview_completion_rate", "technical",
            experience["completion_rate"], THRESHOLDS["interview_completion_rate"],
            f"{experience['dropped']} of {experience['attempted']} interviews did not finish",
        ))
    if experience["runtime_errors"]:
        out.append(Alert(
            "runtime_errors", "technical", experience["runtime_errors"], 0,
            "turn or model failures were recorded during interviews",
        ))
    p95 = money["evaluation_latency_sec"]["p95"]
    worst = money["evaluation_latency_sec"]["max"]
    if (p95 or worst) > THRESHOLDS["evaluation_latency_p95_sec"]:
        out.append(Alert(
            "evaluation_latency", "technical", p95 or worst,
            THRESHOLDS["evaluation_latency_p95_sec"],
            f"slowest single model call {money['slowest_single_call_ms']} ms"
            + ("" if p95 else " (p95 withheld: fewer than 20 runs, max reported)"),
        ))
    if money["provider_error_rate"] > THRESHOLDS["provider_error_rate"]:
        out.append(Alert(
            "provider_error_rate", "technical",
            money["provider_error_rate"], THRESHOLDS["provider_error_rate"],
            "model calls are failing",
        ))

    if quality["evidence_checked"] and quality["evidence_untraceable"]:
        out.append(Alert(
            "untraceable_evidence", "quality",
            quality["evidence_untraceable"], 0,
            "a persisted quote could not be found in its own frozen transcript — "
            "this is the fabrication check, and it is a stop condition",
        ))
    if quality["completed"] and quality["quarantine_rate"] > THRESHOLDS["evidence_quarantine_rate"]:
        out.append(Alert(
            "evidence_quarantine_rate", "quality",
            quality["quarantine_rate"], THRESHOLDS["evidence_quarantine_rate"],
            "evidence is being refused at the gate more often than expected",
        ))
    if quality["completed"]:
        zero_rate = _rate(quality["zero_evidence_evaluations"], quality["completed"])
        if zero_rate > THRESHOLDS["zero_evidence_rate"]:
            out.append(Alert(
                "zero_evidence_rate", "quality", zero_rate,
                THRESHOLDS["zero_evidence_rate"],
                "completed evaluations with no evidence at all",
            ))
    if stab["recommendation_flips"] > THRESHOLDS["recommendation_instability_rate"]:
        out.append(Alert(
            "recommendation_instability", "quality",
            stab["recommendation_flips"], THRESHOLDS["recommendation_instability_rate"],
            "identical frozen input produced different recommendations",
            [f["session_id"] for f in stab["flips"]],
        ))
    if review["reviewed"] and review["disagreement_rate"] > THRESHOLDS["reviewer_disagreement_rate"]:
        out.append(Alert(
            "reviewer_disagreement_rate", "quality",
            review["disagreement_rate"], THRESHOLDS["reviewer_disagreement_rate"],
            "reviewers disagreed with: "
            + ", ".join(f"{k}×{v}" for k, v in review["reasons"].items()),
        ))
    return [a.to_dict() for a in out]


# --------------------------------------------------------------------------- #
#  The dataset
# --------------------------------------------------------------------------- #
def dataset(pilot_run_id: str = "", organization_id: str = "") -> list[dict[str, Any]]:
    """One row per completed interview: what was run, what came out, what a
    human thought of it.

    No candidate name, no email, no transcript, no quote. The session id is the
    key back into the full record for anyone who is entitled to read it; the
    persona label is the only description of the candidate, and it is a category
    somebody chose, not an inference about a person.
    """
    reviews = {r.evaluation_id: r for r in pilot.list_reviews()}
    rows: list[dict[str, Any]] = []
    for record in _records(pilot_run_id, organization_id):
        if record.status != evaluations.COMPLETED:
            continue
        result = record.result or {}
        details = result.get("candidate_details", {})
        coverage = result.get("coverage", {})
        meta = record.model_meta or {}
        state = store.try_load(record.session_id)
        review = reviews.get(record.evaluation_id)
        rows.append({
            "pilot_run_id": record.pilot_run_id,
            "session_id": record.session_id,
            "evaluation_id": record.evaluation_id,
            "interview_id": record.interview_id,
            "interview_version": record.interview_version,
            "engine_version": record.engine_version,
            "snapshot_checksum": record.snapshot_checksum,
            "attempt": record.attempt,
            "superseded": record.superseded,
            # A label the pilot operator put on the INVITATION — "persona:thin"
            # — not a judgement about a person and not something the candidate
            # ever sees. Empty for a real candidate, which is the normal case
            # outside a rehearsal.
            "persona": _persona(state) if state else "",
            "channel": state.channel if state else "",
            "provider": meta.get("provider", ""),
            "model": meta.get("resolved_model", "") or meta.get("configured_model", ""),
            "score": details.get("total_score", 0),
            "max_score": result.get("maximum_possible_score", 0),
            "percentage": result.get("percentage", 0.0),
            "rating": details.get("overall_rating", ""),
            "coverage_percentage": coverage.get("coverage_percentage", 0.0),
            "skills_discussed": coverage.get("skills_discussed", 0),
            "skills_total": coverage.get("skills_total", 0),
            "recommendation": result.get("recommendation", ""),
            "evidence_count": len(record.evidence),
            "quarantined_count": len(record.quarantined),
            "repairs_count": len(record.repairs),
            "validation_errors": 0 if record.status == evaluations.COMPLETED else 1,
            "model_calls": meta.get("calls", 0),
            "prompt_tokens": meta.get("prompt_tokens", 0),
            "completion_tokens": meta.get("completion_tokens", 0),
            "evaluation_latency_sec": round((meta.get("latency_ms", 0) or 0) / 1000, 2),
            "stage_sec": {k: round(v / 1000, 2) for k, v in (meta.get("stage_ms") or {}).items()},
            "cost_usd": round(_price(
                meta.get("resolved_model", ""),
                meta.get("prompt_tokens", 0), meta.get("completion_tokens", 0),
            ), 5),
            "completed_at": record.completed_at,
            "human_review": review.verdict if review else "",
            "human_disagreement_reason": ",".join(review.reasons) if review else "",
            "human_recommendation": review.recommendation if review else "",
        })
    return rows


# --------------------------------------------------------------------------- #
#  Everything, in one object
# --------------------------------------------------------------------------- #
def summary(pilot_run_id: str = "", organization_id: str = "") -> dict[str, Any]:
    # A named run is resolved and then checked: a report must not describe, or
    # even name, another organization's run in its header.
    run = pilot.get_run(pilot_run_id) if pilot_run_id else pilot.active_run(
        organization_id or None)
    if run is not None and organization_id and run.organization_id != organization_id:
        run = None
    body = {
        "pilot_run": run.to_dict() if run else None,
        "pilot_run_id": pilot_run_id or (run.pilot_run_id if run else ""),
        "organization_id": organization_id,
        "engine_version": ENGINE_VERSION,
        "candidate_experience": candidate_experience(pilot_run_id, organization_id),
        "evaluation_quality": evaluation_quality(pilot_run_id, organization_id),
        "stability": stability(pilot_run_id, organization_id),
        "human_review": human_review(pilot_run_id, organization_id),
        "cost_and_latency": cost_and_latency(pilot_run_id, organization_id),
        "thresholds": THRESHOLDS,
    }
    body["alerts"] = alerts(body)
    return body
