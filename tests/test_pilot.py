"""The pilot's measurement layer: attribution, review capture, and the numbers.

Three properties this file exists to hold:

  * **A review can never become a score.** Nothing a reviewer submits reaches the
    evaluation, the result, or the evaluator's inputs. That is the whole of §13,
    and it is the one thing about the pilot that would be expensive to get wrong.
  * **Every measurement is derived, not invented.** The metrics read sessions,
    audit trails and evaluation records — the same rows the product wrote for
    its own reasons — so a number here cannot disagree with the record.
  * **No candidate content leaves the reporting layer.** Summaries, alerts and
    dataset rows carry counts, ids and durations. Not names, not quotes.
"""
from __future__ import annotations

import json

import pytest

from packages.types.evaluation import ENGINE_VERSION
from services.ai.workloads.interview_designer import Skill, Task
from services.assessment import pool as pool_service
from services.data import audit, evaluations, interviews, pilot
from services.data import sessions as store
from services.data.interviews import InterviewConfig
from services.evaluation import stub as eval_stub
from services.pilot import metrics
from tests.conftest import sign_in

pytestmark = pytest.mark.usefixtures("data_dir")

IV = "iv_pilot"
R = f"/api/recruiter/interviews/{IV}"

ANSWER = (
    "I put an idempotency key on the capture call and store it alongside the charge "
    "row in the same transaction, because the retry has to find the original result "
    "rather than create a second one. The cost is a unique index on every write, "
    "which we took because a double charge is a refund plus a chargeback fee."
)
SECOND = (
    "For reconciliation I pull the settlement file each morning, match on the "
    "provider reference first and the amount second, and anything unmatched after "
    "two passes goes to a manual queue with the reason attached, because a silent "
    "write-off is how a ledger stops meaning anything."
)


@pytest.fixture()
def pilot_dir(data_dir, monkeypatch):
    """Pilot storage inside the throwaway data directory."""
    return data_dir


@pytest.fixture()
def client(data_dir, tenant, monkeypatch, pool):
    from fastapi.testclient import TestClient

    monkeypatch.setenv(pool_service.STUB_ENV, "1")
    monkeypatch.setenv(eval_stub.STUB_ENV, "1")
    monkeypatch.setattr("services.config.PILOT_RUN_ID", "", raising=False)
    from services.ai import gateway as ai_gateway
    from services.data import jobs as job_store

    monkeypatch.setattr(job_store, "_PATH", data_dir / "jobs.json")
    monkeypatch.setattr(ai_gateway.get_gateway(), "live", False)

    interviews.save(InterviewConfig(
        id=IV, title="Senior Backend Engineer", role="senior_backend_engineer",
        role_title="Senior Backend Engineer", job_id="job_pilot",
        jd_text="Own the payment services.", experience_from=5, experience_to=9,
        interview_type="medium", difficulty="medium", recommended_duration_min=20,
        skills=[
            Skill(name="Idempotent design", competency_id="skl_idem", priority="high",
                  evaluated=True, assessment_scope="Retry-safe capture paths."),
            Skill(name="Reconciliation", competency_id="skl_recon", priority="high",
                  evaluated=True, assessment_scope="Settlement mismatches."),
        ],
        tasks=[
            Task(id="tsk_capture", name="Design payment capture",
                 description="Design idempotent capture.", required_skills=["skl_idem"]),
            Task(id="tsk_recon", name="Reconcile settlements",
                 description="Reconcile the ledger.", required_skills=["skl_recon"]),
        ],
    ))

    from services.api.app import app

    with TestClient(app) as c:
        sign_in(c, tenant)
        c.post(f"{R}/questions/generate", json={})
        c.post(f"{R}/publish", json={})
        yield c


def _sit(client, name="Priya Sharma") -> str:
    token = client.post(
        f"{R}/invitations", json={"candidates": [name]}
    ).json()["created"][0]["token"]
    session_id = client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    }).json()["session_id"]
    invite_token = store.try_load(session_id).invite_token
    for n in range(40):
        # The candidate's own invitation token: one TestClient holds one cookie
        # jar, and these tests run several candidates at once.
        reply = client.post(
            f"/api/session/{session_id}/turn",
            params={"token": invite_token},
            json={"said": ANSWER if n % 2 == 0 else SECOND},
        ).json()["reply"]
        if reply["ends"]:
            break
    return session_id


def _evaluate(client, session_id: str, force: bool = False) -> dict:
    response = client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={"force": force}
    )
    assert response.status_code == 200, response.text
    return response.json()


# =========================================================================== #
#  §4  Attribution
# =========================================================================== #
def test_a_session_started_outside_a_run_belongs_to_no_pilot(client):
    session_id = _sit(client)
    assert store.try_load(session_id).pilot_run_id == ""
    record = evaluations.current_for(session_id, ENGINE_VERSION)
    assert record.pilot_run_id == ""


def test_a_session_started_during_a_run_is_attributable_end_to_end(client):
    run = client.post("/api/recruiter/pilot/runs", json={"label": "week one"}).json()
    assert run["active"] is True and run["engine_version"] == ENGINE_VERSION

    session_id = _sit(client)
    body = _evaluate(client, session_id)

    assert store.try_load(session_id).pilot_run_id == run["pilot_run_id"]
    record = evaluations.get(body["evaluation_id"])
    assert record.pilot_run_id == run["pilot_run_id"]
    # The whole chain the phase asked for, from one row.
    assert (record.session_id, record.interview_version, record.engine_version) == (
        session_id, 1, ENGINE_VERSION
    )
    assert record.model_meta.get("configured_model")


def test_only_one_run_is_ever_open(client):
    first = client.post("/api/recruiter/pilot/runs", json={"label": "one"}).json()
    second = client.post("/api/recruiter/pilot/runs", json={"label": "two"}).json()
    runs = client.get("/api/recruiter/pilot/runs").json()
    assert runs["active"]["pilot_run_id"] == second["pilot_run_id"]
    assert [r["active"] for r in runs["runs"]] == [False, True]
    assert first["pilot_run_id"] != second["pilot_run_id"]


def test_stopping_a_run_stops_attributing_new_sessions_to_it(client):
    run = client.post("/api/recruiter/pilot/runs", json={}).json()
    during = _sit(client, "During")
    client.post(f"/api/recruiter/pilot/runs/{run['pilot_run_id']}/stop")
    after = _sit(client, "After")

    assert store.try_load(during).pilot_run_id == run["pilot_run_id"]
    assert store.try_load(after).pilot_run_id == ""


def test_the_pilot_id_changes_nothing_the_candidate_sees(client):
    """Attribution is a label, not a mode."""
    outside = _sit(client, "Outside")
    client.post("/api/recruiter/pilot/runs", json={"label": "inside"})
    inside = _sit(client, "Inside")

    a, b = store.try_load(outside), store.try_load(inside)
    assert a.asked_item_ids == b.asked_item_ids
    assert [u.kind for u in a.transcript] == [u.kind for u in b.transcript]
    assert "pilot" not in client.get(f"/api/session/{inside}").text.lower()


# =========================================================================== #
#  §3  Telemetry — the questions one completed interview must answer
# =========================================================================== #
def test_one_completed_interview_answers_every_telemetry_question(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "telemetry"})
    session_id = _sit(client)
    body = _evaluate(client, session_id)
    record = evaluations.get(body["evaluation_id"])
    row = next(
        r for r in metrics.dataset(record.pilot_run_id)
        if r["evaluation_id"] == record.evaluation_id
    )

    assert row["pilot_run_id"] and row["session_id"] and row["evaluation_id"]
    assert row["interview_version"] == 1 and row["engine_version"] == ENGINE_VERSION
    assert row["snapshot_checksum"]
    assert row["model"] or row["provider"] == "none"
    assert row["completed_at"]
    assert row["evaluation_latency_sec"] >= 0
    assert row["model_calls"] >= 0
    assert row["prompt_tokens"] >= 0 and row["completion_tokens"] >= 0
    assert row["recommendation"] and row["rating"]
    assert row["skills_total"] > 0
    assert row["evidence_count"] >= 0
    assert row["validation_errors"] == 0
    assert "stage_sec" in row and set(row["stage_sec"]) >= {
        "evidence_extraction", "skill_assessment"
    }


def test_no_candidate_content_reaches_the_pilot_reporting_layer(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "privacy"})
    session_id = _sit(client, "Priya Sharma")
    _evaluate(client, session_id)

    blob = json.dumps({
        "summary": client.get("/api/recruiter/pilot/summary").json(),
        "dataset": client.get("/api/recruiter/pilot/dataset").json(),
    })
    assert "Priya" not in blob
    assert "idempotency key" not in blob
    assert store.try_load(session_id).invite_token not in blob
    for word in ("candidate_quote", "transcript", "remarks", "question_text"):
        assert word not in blob


def test_the_device_check_is_recorded_without_gating_anything(client):
    token = client.post(
        f"{R}/invitations", json={"candidates": ["Priya"]}
    ).json()["created"][0]["token"]

    ok = client.post(f"/api/invite/{token}/precheck",
                     json={"token": token, "outcome": "failed", "reason": "no_microphone"})
    assert ok.status_code == 200 and ok.json()["outcome"] == "failed"

    # It gates nothing: the same candidate can still start.
    started = client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    })
    assert started.status_code == 200

    reported = [
        row for row in audit.read_product()
        if row["event"] == audit.SYSTEM_CHECK_REPORTED
    ]
    assert reported and reported[-1]["outcome"] == "failed"
    assert reported[-1]["reason"] == "no_microphone"
    # And it carries no whole token.
    assert token not in json.dumps(reported)


def test_an_unknown_outcome_is_recorded_as_a_failure_rather_than_believed(client):
    token = client.post(
        f"{R}/invitations", json={"candidates": ["Priya"]}
    ).json()["created"][0]["token"]
    body = client.post(f"/api/invite/{token}/precheck", json={
        "token": token, "outcome": "everything_is_fine_trust_me", "reason": "x" * 500,
    }).json()
    assert body["outcome"] == "failed"
    row = [r for r in audit.read_product() if r["event"] == audit.SYSTEM_CHECK_REPORTED][-1]
    assert len(row["reason"]) <= 80


# =========================================================================== #
#  §2 / §14  The scorecard
# =========================================================================== #
def test_the_scorecard_counts_what_actually_happened(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "scorecard"})
    finished = _sit(client, "Finished")
    _evaluate(client, finished)

    # One interview left mid-flight.
    token = client.post(
        f"{R}/invitations", json={"candidates": ["Abandoned"]}
    ).json()["created"][0]["token"]
    client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    })

    summary = client.get("/api/recruiter/pilot/summary").json()
    experience = summary["candidate_experience"]
    quality = summary["evaluation_quality"]

    assert experience["attempted"] == 2
    assert experience["completed"] == 1
    assert experience["completion_rate"] == 0.5
    assert experience["dropped"] == 1
    assert experience["duration_sec"]["n"] == 1
    assert quality["completed"] == 1 and quality["failed"] == 0
    assert quality["evidence_traceability_rate"] == 1.0
    assert sum(quality["recommendation_distribution"].values()) == 1


def test_a_failed_evaluation_is_counted_and_attributed_to_its_stage(client, monkeypatch):
    client.post("/api/recruiter/pilot/runs", json={"label": "failures"})
    session_id = _sit(client)
    monkeypatch.setattr(
        eval_stub, "extract_for_question",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("extractor down")),
    )
    assert _evaluate(client, session_id)["status"] == "failed"

    quality = client.get("/api/recruiter/pilot/summary").json()["evaluation_quality"]
    assert quality["failed"] == 1
    assert quality["failures_by_stage"] == {"evidence_extraction": 1}
    assert quality["failures_by_kind"] == {"model": 1}
    assert quality["failure_rate"] == 1.0


def test_a_run_is_measured_separately_from_everything_before_it(client):
    before = _sit(client, "Before the pilot")
    _evaluate(client, before)
    run = client.post("/api/recruiter/pilot/runs", json={"label": "second"}).json()
    inside = _sit(client, "Inside the pilot")
    _evaluate(client, inside)

    scoped = client.get(
        "/api/recruiter/pilot/summary", params={"pilot_run_id": run["pilot_run_id"]}
    ).json()
    everything = client.get("/api/recruiter/pilot/summary").json()
    assert scoped["candidate_experience"]["attempted"] == 1
    assert everything["candidate_experience"]["attempted"] == 1  # the active run
    assert len(client.get(
        "/api/recruiter/pilot/dataset", params={"pilot_run_id": run["pilot_run_id"]}
    ).json()["rows"]) == 1


# =========================================================================== #
#  §15  Alerts
# =========================================================================== #
def test_a_clean_run_raises_nothing(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "clean"})
    session_id = _sit(client)
    _evaluate(client, session_id)
    assert client.get("/api/recruiter/pilot/summary").json()["alerts"] == []


def test_failures_raise_a_technical_alert_naming_the_threshold(client, monkeypatch):
    client.post("/api/recruiter/pilot/runs", json={"label": "alerting"})
    session_id = _sit(client)
    monkeypatch.setattr(
        eval_stub, "extract_for_question",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("extractor down")),
    )
    _evaluate(client, session_id)

    alerts = {a["key"]: a for a in client.get("/api/recruiter/pilot/summary").json()["alerts"]}
    assert "evaluation_failure_rate" in alerts
    fired = alerts["evaluation_failure_rate"]
    assert fired["severity"] == "technical"
    assert fired["observed"] > fired["threshold"]
    assert "evidence_extraction" in fired["detail"]


def test_an_untraceable_quote_is_the_alert_that_matters_most(client):
    """The fabrication check, run over the persisted record rather than trusted.

    It uses the gate's own `quote_is_real`, so this cannot fire on a faithful
    quote that lost a trailing comma — and it does fire the moment a quote is
    not in the transcript at all.
    """
    client.post("/api/recruiter/pilot/runs", json={"label": "integrity"})
    session_id = _sit(client)
    body = _evaluate(client, session_id)

    assert client.get("/api/recruiter/pilot/summary").json()["alerts"] == []

    record = evaluations.get(body["evaluation_id"])
    record.evidence[0]["candidate_quote"] = "a sentence nobody in this interview said"
    evaluations.save(record)

    summary = client.get("/api/recruiter/pilot/summary").json()
    assert summary["evaluation_quality"]["evidence_untraceable"] == 1
    keys = [a["key"] for a in summary["alerts"]]
    assert "untraceable_evidence" in keys


# =========================================================================== #
#  §6–§8  Stability
# =========================================================================== #
def test_two_runs_of_one_snapshot_that_agree_are_not_instability(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "stability"})
    session_id = _sit(client)
    first = _evaluate(client, session_id)
    second = _evaluate(client, session_id, force=True)
    assert first["evaluation_id"] != second["evaluation_id"]

    stability = client.get("/api/recruiter/pilot/stability").json()
    assert stability["sessions_evaluated_more_than_once"] == 1
    assert stability["recommendation_flips"] == 0
    assert stability["score_spread_pp"]["max"] == 0.0


def test_a_flip_on_an_identical_snapshot_is_classified_and_named(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "flip"})
    session_id = _sit(client)
    _evaluate(client, session_id)
    second = _evaluate(client, session_id, force=True)

    # Stand in for what the real model did: one point of movement, enough to
    # cross a boundary, with the snapshot untouched.
    record = evaluations.get(second["evaluation_id"])
    record.result["recommendation"] = "Not suitable for this role"
    record.result["percentage"] = round(record.result["percentage"] - 1.0, 1)
    evaluations.save(record)

    stability = client.get("/api/recruiter/pilot/stability").json()
    assert stability["recommendation_flips"] == 1
    flip = stability["flips"][0]
    assert flip["session_id"] == session_id
    assert flip["runs"] == 2
    assert flip["score_spread_pp"] <= 2.0
    assert flip["class"] == "boundary_instability"
    assert len(flip["recommendations"]) == 2


def test_a_large_score_move_is_variance_rather_than_a_boundary(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "variance"})
    session_id = _sit(client)
    _evaluate(client, session_id)
    second = _evaluate(client, session_id, force=True)

    record = evaluations.get(second["evaluation_id"])
    record.result["recommendation"] = "Not suitable for this role"
    record.result["percentage"] = round(record.result["percentage"] - 25.0, 1)
    evaluations.save(record)

    flip = client.get("/api/recruiter/pilot/stability").json()["flips"][0]
    assert flip["class"] == "scoring_variance"


def test_two_different_transcripts_are_never_counted_as_instability(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "distinct"})
    a, b = _sit(client, "A"), _sit(client, "B")
    _evaluate(client, a)
    _evaluate(client, b)
    stability = client.get("/api/recruiter/pilot/stability").json()
    assert stability["sessions_evaluated_more_than_once"] == 0
    assert stability["recommendation_flips"] == 0


# =========================================================================== #
#  §11 / §12 / §13  Review capture
# =========================================================================== #
def _review(client, session_id: str, **body) -> dict:
    payload = {"reviewer": "asha", "verdict": "agree", "reasons": [], **body}
    return client.post(f"/api/recruiter/sessions/{session_id}/review", json=payload)


def test_a_reviewer_can_record_agreement(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "review"})
    session_id = _sit(client)
    body = _evaluate(client, session_id)

    response = _review(client, session_id)
    assert response.status_code == 201
    saved = response.json()
    assert saved["verdict"] == "agree"
    assert saved["evaluation_id"] == body["evaluation_id"]
    # What the human was looking at, frozen with the verdict.
    assert saved["ai_recommendation"] == body["recommendation"]
    assert saved["ai_total_score"] == body["candidate_details"]["total_score"]
    assert saved["pilot_run_id"]


def test_a_disagreement_needs_a_category(client):
    session_id = _sit(client)
    _evaluate(client, session_id)
    assert _review(client, session_id, verdict="disagree").status_code == 422
    assert _review(
        client, session_id, verdict="disagree", reasons=["wrong_skill"]
    ).status_code == 201


def test_an_unknown_verdict_or_reason_is_refused(client):
    session_id = _sit(client)
    _evaluate(client, session_id)
    assert _review(client, session_id, verdict="looks_fine").status_code == 422
    assert _review(
        client, session_id, verdict="disagree", reasons=["the_vibes"]
    ).status_code == 422


def test_a_review_cannot_change_the_assessment(client):
    """The whole point of §13, as an assertion."""
    session_id = _sit(client)
    body = _evaluate(client, session_id)
    before = json.dumps(evaluations.get(body["evaluation_id"]).to_dict(), sort_keys=True)
    before_result = client.get(
        f"/api/recruiter/sessions/{session_id}/evaluation/result"
    ).json()

    _review(client, session_id, verdict="disagree", reasons=["wrong_score"],
            recommendation="Not suitable for this role",
            note="I would not take this forward.")

    after = json.dumps(evaluations.get(body["evaluation_id"]).to_dict(), sort_keys=True)
    after_result = client.get(
        f"/api/recruiter/sessions/{session_id}/evaluation/result"
    ).json()
    assert after == before
    assert after_result == before_result
    # And the reviewer's own recommendation is stored as theirs, next to Tara's.
    review = client.get(f"/api/recruiter/sessions/{session_id}/review").json()["reviews"][0]
    assert review["recommendation"] == "Not suitable for this role"
    assert review["ai_recommendation"] == before_result["overall"]["recommendation"]
    assert review["recommendation"] != review["ai_recommendation"]


def test_a_second_verdict_from_the_same_reviewer_replaces_their_first(client):
    session_id = _sit(client)
    _evaluate(client, session_id)
    _review(client, session_id, verdict="needs_review")
    _review(client, session_id, verdict="disagree", reasons=["wrong_evidence"])
    reviews = client.get(f"/api/recruiter/sessions/{session_id}/review").json()["reviews"]
    assert len(reviews) == 1 and reviews[0]["verdict"] == "disagree"


def test_two_reviewers_are_two_reviews(client):
    session_id = _sit(client)
    _evaluate(client, session_id)
    _review(client, session_id, reviewer="asha")
    _review(client, session_id, reviewer="dev", verdict="disagree", reasons=["other"])
    reviews = client.get(f"/api/recruiter/sessions/{session_id}/review").json()["reviews"]
    assert {r["reviewer"] for r in reviews} == {"asha", "dev"}


def test_an_unevaluated_session_cannot_be_reviewed(client):
    token = client.post(
        f"{R}/invitations", json={"candidates": ["Priya"]}
    ).json()["created"][0]["token"]
    session_id = client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    }).json()["session_id"]
    assert _review(client, session_id).status_code == 404


def test_agreement_is_reported_without_being_turned_into_a_quality_score(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "agreement"})
    a, b, c = _sit(client, "A"), _sit(client, "B"), _sit(client, "C")
    for session_id in (a, b, c):
        _evaluate(client, session_id)
    _review(client, a, verdict="agree")
    _review(client, b, verdict="disagree", reasons=["wrong_score", "wrong_evidence"],
            recommendation="Not suitable for this role")
    _review(client, c, verdict="needs_review")

    review = client.get("/api/recruiter/pilot/summary").json()["human_review"]
    assert review["reviewed"] == 3
    assert (review["agree"], review["disagree"], review["needs_review"]) == (1, 1, 1)
    assert review["agreement_rate"] == 0.333
    assert review["reasons"] == {"wrong_score": 1, "wrong_evidence": 1}
    assert review["recommendation_stated"] == 1
    assert review["recommendation_agreement_rate"] == 0.0
    # Nothing about the evaluator's own numbers moved because a human disagreed.
    quality = client.get("/api/recruiter/pilot/summary").json()["evaluation_quality"]
    assert quality["completed"] == 3 and quality["failed"] == 0


def test_disagreement_past_the_threshold_is_a_quality_alert_not_a_verdict(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "disagreement"})
    session_id = _sit(client)
    _evaluate(client, session_id)
    _review(client, session_id, verdict="disagree", reasons=["wrong_recommendation"])

    alerts = {a["key"]: a for a in client.get("/api/recruiter/pilot/summary").json()["alerts"]}
    assert "reviewer_disagreement_rate" in alerts
    assert alerts["reviewer_disagreement_rate"]["severity"] == "quality"
    assert "wrong_recommendation" in alerts["reviewer_disagreement_rate"]["detail"]


# =========================================================================== #
#  §16 / §17  Cost and latency
# =========================================================================== #
def test_cost_and_latency_are_measured_per_stage(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "cost"})
    session_id = _sit(client)
    _evaluate(client, session_id)

    money = client.get("/api/recruiter/pilot/summary").json()["cost_and_latency"]
    assert money["evaluations"] == 1
    assert set(money["evaluation_stage_sec"]) >= {"evidence_extraction", "skill_assessment"}
    assert money["evaluation_latency_sec"]["n"] == 1
    # The stub calls nothing, so the honest cost is zero rather than an estimate.
    assert money["evaluation_cost_usd"]["mean"] == 0.0
    assert money["unpriced_models"] == []


def test_a_p95_is_withheld_until_the_sample_can_carry_one(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "p95"})
    session_id = _sit(client)
    _evaluate(client, session_id)
    money = client.get("/api/recruiter/pilot/summary").json()["cost_and_latency"]
    assert money["evaluation_latency_sec"]["n"] < 20
    assert money["evaluation_latency_sec"]["p95"] is None
    assert money["evaluation_latency_sec"]["max"] >= 0


# =========================================================================== #
#  §23  The dataset
# =========================================================================== #
def test_the_dataset_carries_the_review_next_to_the_result(client):
    client.post("/api/recruiter/pilot/runs", json={"label": "dataset"})
    session_id = _sit(client)
    _evaluate(client, session_id)
    _review(client, session_id, verdict="disagree", reasons=["wrong_evidence"],
            recommendation="Proceed to next round")

    row = client.get("/api/recruiter/pilot/dataset").json()["rows"][0]
    assert row["human_review"] == "disagree"
    assert row["human_disagreement_reason"] == "wrong_evidence"
    assert row["human_recommendation"] == "Proceed to next round"
    assert row["recommendation"] and row["recommendation"] != "" 
    assert "candidate_name" not in row and "persona" in row


def test_the_dataset_only_holds_completed_evaluations(client, monkeypatch):
    client.post("/api/recruiter/pilot/runs", json={"label": "completed only"})
    good = _sit(client, "Good")
    _evaluate(client, good)
    bad = _sit(client, "Bad")
    monkeypatch.setattr(
        eval_stub, "extract_for_question",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("extractor down")),
    )
    _evaluate(client, bad)

    rows = client.get("/api/recruiter/pilot/dataset").json()["rows"]
    assert [r["session_id"] for r in rows] == [good]


# =========================================================================== #
#  §20 / §21  Concurrency, on the one host the pilot runs on
# =========================================================================== #
def test_three_concurrent_interviews_do_not_contaminate_each_other(client):
    import threading

    client.post("/api/recruiter/pilot/runs", json={"label": "concurrency"})
    sessions: dict[str, str] = {}
    errors: list[Exception] = []
    barrier = threading.Barrier(3)

    def sit(name: str):
        try:
            barrier.wait()
            sessions[name] = _sit(client, name)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=sit, args=(n,)) for n in ("Ada", "Bo", "Cai")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(set(sessions.values())) == 3

    for name, session_id in sessions.items():
        state = store.try_load(session_id)
        assert state.candidate_name == name
        assert state.interview_version == 1
        assert state.pilot_run_id
        # Every candidate turn on this session was said by this candidate.
        said = [u.text for u in state.transcript if u.speaker == "candidate"]
        assert said and all(text in (ANSWER, SECOND) for text in said)
        # And every other session's turns are its own.
        for other, other_id in sessions.items():
            if other == name:
                continue
            assert store.try_load(other_id).candidate_name == other


def test_concurrent_sessions_do_not_lose_each_others_invitations(client):
    """The whole-file JSON stores are read-modify-write, and three interviews at
    once is exactly the shape that used to drop a row."""
    import threading

    from services.data import invites

    client.post("/api/recruiter/pilot/runs", json={"label": "invitations"})
    names = ("Ada", "Bo", "Cai", "Dee")
    done: list[str] = []
    barrier = threading.Barrier(len(names))

    def sit(name: str):
        barrier.wait()
        done.append(_sit(client, name))

    threads = [threading.Thread(target=sit, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = invites.list_all()
    assert len(done) == len(names)
    # Every invitation survived, and every one of them is marked complete with
    # its own session.
    for session_id in done:
        token = store.try_load(session_id).invite_token
        invite = invites.get(token)
        assert invite is not None, "an invitation was lost"
        assert invite.status == "complete"
        assert invite.session_id == session_id
    assert len([r for r in rows if r["status"] == "complete"]) >= len(names)


def test_concurrent_evaluations_stay_one_per_session(client):
    import threading

    client.post("/api/recruiter/pilot/runs", json={"label": "evaluation concurrency"})
    sessions = [_sit(client, name) for name in ("Ada", "Bo", "Cai")]
    barrier = threading.Barrier(len(sessions))

    def evaluate(session_id: str):
        barrier.wait()
        _evaluate(client, session_id)

    threads = [threading.Thread(target=evaluate, args=(s,)) for s in sessions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for session_id in sessions:
        rows = evaluations.list_for_session(session_id)
        assert len(rows) == 1, [r.evaluation_id for r in rows]
        assert rows[0].status == evaluations.COMPLETED
        assert rows[0].session_id == session_id
    quality = client.get("/api/recruiter/pilot/summary").json()["evaluation_quality"]
    assert quality["completed"] == 3 and quality["failed"] == 0
