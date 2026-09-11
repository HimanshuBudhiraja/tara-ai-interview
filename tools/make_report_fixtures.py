"""Generate the recruiter report's test fixtures from the real API serialisers.

The console's tests must not be written against a hand-typed guess at the
evaluation contract — that is exactly how a frontend drifts from its backend and
nobody notices until a recruiter sees an empty page. So the fixtures are
produced here, by running the real evaluation pipeline and serialising the
result through `services.api.evaluation`'s own `_envelope` and `_evidence_rows`.

The evidence is injected rather than extracted: no provider is called, and the
point is to pin the SHAPE of the contract and cover every state a report has to
render, not to demonstrate model quality.

    python tools/make_report_fixtures.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "apps" / "recruiter" / "src" / "test" / "fixtures"

#: Which dimension the injected extractor reports per skill, chosen so one
#: evaluation covers every case the report has to draw:
#:
#:   skl_idem   probed twice, conceptual evidence only  → deep_probed / direct
#:   skl_recon  answered once, production judgement      → direct / deep_probed
#:   skl_review answered in six words                    → mentioned
#:   skl_k8s    never asked                              → not_discussed
DIMENSION_BY_SKILL = {
    "skl_idem": "conceptual_understanding",
    "skl_recon": "production_judgment",
    "skl_review": "reasoning",
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "data"
    (tmp / "sessions").mkdir(parents=True)
    (tmp / "audit").mkdir(parents=True)

    from services import config

    config.DATA_DIR = tmp
    config.SESSION_DIR = tmp / "sessions"
    config.AUDIT_DIR = tmp / "audit"

    from services.data import evaluations, interviews, versions
    from services.data import sessions as store
    from services.data.interviews import InterviewConfig

    versions._PATH = tmp / "interview_versions.json"
    interviews._PATH = tmp / "interviews.json"
    store._STORE = store.FileSessionStore(tmp / "sessions")

    from packages.types.evaluation import EvidenceItem
    from services.api import evaluation as api
    from services.evaluation import evidence as EV
    from services.evaluation import jobs
    from tests import fixtures_candidates as F

    interviews.save(InterviewConfig(
        id="iv_fix", title="Senior Backend Engineer — payments screen",
        role="senior_backend_engineer", role_title="Senior Backend Engineer, Payments",
        job_id="job_fix", experience_from=4, experience_to=7,
    ))
    versions.publish("iv_fix", F.definition(7), validate=False)

    fixture = F.Fixture(
        "Priya Sharma", 7,
        answers={
            # Probed twice and the substance never moves past the definition.
            "q_idem": [
                "You make it idempotent, so the same request can't be applied twice. "
                "That's the standard way to handle retries on a payment path.",
                "Like I said, it's about making the operation idempotent so a retry is safe.",
                "Just idempotency really. That's the concept that covers it.",
            ],
            # One answer that already carries production judgement.
            "q_recon": [
                "We reconcile against the provider's settlement file every morning and match "
                "on their reference. Anything still unmatched after two cycles goes to a queue "
                "a person works, because auto-resolving a mismatch at our volume quietly hides "
                "the one case that actually cost a customer money.",
            ],
            # Six words: below the substantive threshold.
            "q_review": ["I'd read it and comment."],
        },
        probes={"q_idem": ["What made you choose that?", "How would you know it failed?"]},
        judgement={
            "skl_idem": F._v(2, 2, 3, 2, 3,
                             "The concept is named correctly and never developed. Two follow-ups "
                             "produced no mechanism, no example and no failure mode."),
            "skl_recon": F._v(4, 5, 4, 4, 4,
                              "A concrete daily process, a matching key, and a deliberate choice "
                              "to escalate unmatched rows rather than auto-resolve them."),
            "skl_review": F._v(1, 1, 1, 1, 1, "Barely answered."),
        },
    )

    state = fixture.session()
    state.candidate_name = "Priya Sharma"
    state.created_at = time.time() - 1400
    state.completed_at = time.time() - 20
    store.save(state)

    def extractor(question, skill, definition, *, session_id=""):
        dimension = DIMENSION_BY_SKILL.get(skill.id, "conceptual_understanding")
        items = []
        for turn in question.usable_turns():
            quote = turn.answer.split(".")[0].strip()
            if not quote:
                continue
            items.append(EvidenceItem(
                skill_id=skill.id, skill_name=skill.name,
                question_id=question.question_id, task_id=question.task_id,
                turn_id=turn.turn_id, depth_stage=turn.depth_stage,
                depth_dimension=dimension, candidate_quote=quote,
                evidence_type="supported",
                evidence_strength="strong" if len(turn.answer.split()) >= 30 else "moderate",
                supports_criterion="Depth",
                note="fixture",
            ))
        # One contradicted item, so the report's conflicting-evidence path is
        # covered by something the validator actually accepted.
        if skill.id == "skl_idem" and len(items) > 1:
            items[1].evidence_type = "contradicted"
            items[1].evidence_strength = "moderate"
        return items, EV.ExtractionReport(accepted=len(items))

    record = jobs.request_and_run(
        state, extractor=extractor, judge=fixture.judge(), requested_by="fixtures"
    )
    if record.status != evaluations.COMPLETED:
        print(f"generation failed: {record.error_kind}: {record.error}", file=sys.stderr)
        return 1

    # Deterministic timestamps: a fixture that changes every run makes a diff
    # unreadable and a snapshot test flaky.
    def stamp(payload: dict) -> dict:
        for key in ("created_at", "updated_at", "completed_at"):
            if key in payload:
                payload[key] = 1_760_000_000.0
        session = payload.get("session")
        if isinstance(session, dict):
            session["started_at"] = 1_759_998_600.0
            session["completed_at"] = 1_759_999_980.0
        return payload

    completed = stamp(api._envelope(record))
    evidence = api._evidence_rows(record)

    # The canonical assessment result, from the real assembler and through its
    # own validation — so the console's tests run against exactly what the
    # endpoint serves rather than a hand-typed guess at it.
    from services.evaluation import result as result_module

    assessment = result_module.build_validated(record)
    for key in ("created_at", "completed_at"):
        assessment["evaluation"][key] = 1_760_000_000.0
    assessment["session"]["started_at"] = 1_759_998_600.0
    assessment["session"]["completed_at"] = 1_759_999_980.0
    assessment["session"]["duration_sec"] = 1380
    write(OUT / "result.json", assessment)

    write(OUT / "evaluation.completed.json", completed)
    write(OUT / "evidence.json", {
        "evaluation_id": record.evaluation_id,
        "session_id": record.session_id,
        "interview_version": record.interview_version,
        "evaluation_engine_version": record.engine_version,
        "status": record.status,
        "evidence": evidence,
        "quarantined": [
            {"reason": "quote is not in the transcript", "skill_id": "skl_idem"},
        ],
    })

    # The other three statuses, from the same record so every field agrees.
    pending = evaluations.EvaluationRecord.from_dict(record.to_dict())
    pending.status = evaluations.PENDING
    pending.result, pending.evidence, pending.completed_at = {}, [], None
    write(OUT / "evaluation.pending.json", stamp(api._envelope(pending)))

    running = evaluations.EvaluationRecord.from_dict(pending.to_dict())
    running.status = evaluations.RUNNING
    write(OUT / "evaluation.running.json", stamp(api._envelope(running)))

    failed = evaluations.EvaluationRecord.from_dict(pending.to_dict())
    failed.status = evaluations.FAILED
    failed.error_kind = evaluations.MODEL_FAILURE
    failed.error = "evidence extraction failed on q_idem: provider returned 402"
    write(OUT / "evaluation.failed.json", stamp(api._envelope(failed)))

    shutil.rmtree(tmp.parent, ignore_errors=True)
    return 0


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    from services.data import evaluations  # noqa: E402  (after sys.path)

    raise SystemExit(main())
