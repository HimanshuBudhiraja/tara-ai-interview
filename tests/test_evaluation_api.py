"""§25/§26 — the evaluation over HTTP, and the whole flow end to end.

The integration test at the bottom is the one that matters: a job is created,
an interview designed, questions generated, a version published, a candidate
invited, the interview actually sat through the real orchestrator, and only then
is it evaluated — through the API, against the exact version the candidate sat.

No provider is called. `TARA_QUESTION_STUB` and `TARA_EVAL_STUB` make generation
and extraction deterministic; both are opt-in, so with them off and no key an
evaluation fails visibly instead of inventing a score.
"""
from __future__ import annotations

import pytest

from packages.types.evaluation import ENGINE_VERSION
from services.assessment import pool as pool_service
from services.data import evaluations, interviews, versions
from services.data import sessions as store
from services.data.interviews import InterviewConfig
from services.evaluation import jobs, stub
from services.ai.workloads.interview_designer import Skill, Task
from tests import fixtures_candidates as F
from tests.conftest import sign_in

pytestmark = pytest.mark.usefixtures("data_dir")

R = "/api/recruiter"


@pytest.fixture()
def client(data_dir, tenant, monkeypatch, pool):
    from fastapi.testclient import TestClient

    monkeypatch.setenv(pool_service.STUB_ENV, "1")
    monkeypatch.setenv(stub.STUB_ENV, "1")
    from services.data import jobs as job_rows

    monkeypatch.setattr(job_rows, "_PATH", data_dir / "jobs.json")

    # No provider, deliberately. The runtime falls back to its deterministic
    # brain exactly as it does on a machine with no key, so the interview these
    # tests sit through is reproducible and calls nothing. The gateway and brain
    # are singletons, so both are reset for the choice to take effect.
    from services import config
    from services.ai import brain as ai_brain
    from services.ai import gateway as ai_gateway

    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(ai_gateway, "_GATEWAY", None)
    monkeypatch.setattr(ai_brain, "_BRAIN", None)

    from services.api.app import app

    with TestClient(app) as c:
        sign_in(c, tenant)
        yield c


# --------------------------------------------------------------------------- #
#  A completed session on a published version, without the whole HTTP flow
# --------------------------------------------------------------------------- #
@pytest.fixture()
def evaluated(client):
    """A fixture candidate's completed interview, evaluated through the API."""
    interviews.save(InterviewConfig(
        id="iv_fix", title="Senior Backend Engineer", role="senior_backend_engineer",
        role_title="Senior Backend Engineer, Payments", job_id="job_x",
        experience_from=4, experience_to=7,
    ))
    versions.publish("iv_fix", F.definition(7), validate=False)
    fixture = F.strong_senior()
    state = fixture.session()
    store.save(state)
    response = client.post(f"{R}/sessions/{state.session_id}/evaluation", json={})
    assert response.status_code == 200, response.text
    return state, response.json()


# --------------------------------------------------------------------------- #
#  Status responses
# --------------------------------------------------------------------------- #
def test_a_session_with_no_evaluation_yet_is_a_404(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    response = client.get(f"{R}/sessions/{state.session_id}/evaluation")
    assert response.status_code == 404


def test_a_pending_evaluation_reports_pending_and_carries_no_scores(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    jobs.request(state)

    body = client.get(f"{R}/sessions/{state.session_id}/evaluation").json()
    assert body["status"] == "pending"
    assert body["evaluation_engine_version"] == ENGINE_VERSION
    # No empty scaffold: a candidate with no evaluation yet must not read as a
    # candidate who scored nothing.
    assert "candidate_details" not in body
    assert "skill_assessment" not in body


def test_a_running_evaluation_reports_running(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    record, _ = jobs.request(state)
    record.status = evaluations.RUNNING
    evaluations.save(record)

    body = client.get(f"{R}/sessions/{state.session_id}/evaluation").json()
    assert body["status"] == "running"
    assert "skill_assessment" not in body


def test_a_failed_evaluation_reports_the_failure_and_no_score(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    record, _ = jobs.request(state)
    record.status = evaluations.FAILED
    record.error_kind = evaluations.MODEL_FAILURE
    record.error = "provider returned 402"
    evaluations.save(record)

    body = client.get(f"{R}/sessions/{state.session_id}/evaluation").json()
    assert body["status"] == "failed"
    assert body["error_kind"] == "model"
    assert "candidate_details" not in body


def test_an_incomplete_interview_cannot_be_evaluated_over_http(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    state.phase = "asking"
    store.save(state)
    response = client.post(f"{R}/sessions/{state.session_id}/evaluation", json={})
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
#  The completed contract
# --------------------------------------------------------------------------- #
def test_the_completed_response_matches_the_agreed_contract(evaluated):
    _, body = evaluated
    assert body["status"] == "completed"
    for key in (
        "evaluation_id", "session_id", "interview_version",
        "evaluation_engine_version", "candidate_details", "skill_assessment",
        "strengths_and_improvement_areas", "recommendation",
        "recommendation_explaination",
    ):
        assert key in body, key

    details = body["candidate_details"]
    assert set(details) == {
        "name", "job_role", "experience_level", "total_score", "overall_rating"
    }
    assert details["experience_level"] == "Senior"


def test_every_skill_row_carries_the_five_criteria_and_its_depth(evaluated):
    _, body = evaluated
    published = versions.definition_for("iv_fix", 1)
    assert [r["skill_name"] for r in body["skill_assessment"]] == [
        s.name for s in published.skills
    ]
    for row in body["skill_assessment"]:
        for criterion in ("Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication"):
            assert 0 <= row[criterion] <= 5
        assert row["score"] == sum(
            row[c] for c in
            ("Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication")
        )
        depth = row["depth_evaluation"]
        assert depth["depth_reached"] in ("direct", "probed", "deep_probed")
        assert depth["depth_demonstrated"] in ("direct", "probed", "deep_probed")
        assert depth["evidence_confidence"] in ("high", "medium", "low", "insufficient")


def test_an_undiscussed_skill_is_reported_as_a_gap_not_a_failing(evaluated):
    _, body = evaluated
    k8s = next(r for r in body["skill_assessment"] if r["skill_name"] == "Kubernetes")
    assert k8s["discussion_status"] == "not_discussed"
    assert k8s["remarks"] == "Not discussed in interview"
    assert all(k8s[c] == 0 for c in
               ("Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication"))


def test_the_api_never_returns_a_prompt_or_model_reasoning(evaluated):
    _, body = evaluated
    serialised = repr(body).lower()
    for leak in ("system", "you assess one skill", "strict json", "untrusted",
                 "candidate_text_start", "note", "reasoning_tokens", "snapshot\":"):
        assert leak not in serialised, leak


def test_requesting_twice_over_http_returns_the_same_evaluation(client, evaluated):
    state, body = evaluated
    again = client.post(f"{R}/sessions/{state.session_id}/evaluation", json={}).json()
    assert again["evaluation_id"] == body["evaluation_id"]
    assert len(evaluations.list_for_session(state.session_id)) == 1


def test_a_forced_re_evaluation_is_a_new_run_and_the_old_one_survives(client, evaluated):
    state, body = evaluated
    forced = client.post(
        f"{R}/sessions/{state.session_id}/evaluation", json={"force": True}
    ).json()
    assert forced["evaluation_id"] != body["evaluation_id"]
    assert forced["attempt"] == 2

    history = client.get(f"{R}/sessions/{state.session_id}/evaluations").json()
    assert len(history["evaluations"]) == 2
    assert [e["superseded"] for e in history["evaluations"]] == [True, False]


def test_an_evaluation_can_be_read_by_its_own_id(client, evaluated):
    _, body = evaluated
    direct = client.get(f"{R}/evaluations/{body['evaluation_id']}")
    assert direct.status_code == 200
    assert direct.json()["session_id"] == body["session_id"]


# --------------------------------------------------------------------------- #
#  Evidence
# --------------------------------------------------------------------------- #
def test_the_evidence_endpoint_returns_validated_traceable_evidence(client, evaluated):
    state, _ = evaluated
    body = client.get(f"{R}/sessions/{state.session_id}/evaluation/evidence").json()
    assert body["evidence"], "the stub extractor should have produced evidence"

    snapshot_turns = {
        t["turn_id"]: t
        for t in evaluations.current_for(state.session_id, ENGINE_VERSION).snapshot["turns"]
    }
    for item in body["evidence"]:
        for key in ("session_id", "skill_id", "question_id", "turn_id",
                    "depth_stage", "task_id", "candidate_quote"):
            assert item[key] != "" or key == "task_id"
        turn = snapshot_turns[item["turn_id"]]
        # Every exposed quote is really in the transcript, verbatim.
        assert item["candidate_quote"] in turn["answer"]
        assert item["depth_stage"] == turn["depth_stage"]


def test_the_evidence_endpoint_never_exposes_the_extractors_rationale(client, evaluated):
    state, _ = evaluated
    body = client.get(f"{R}/sessions/{state.session_id}/evaluation/evidence").json()
    assert all("note" not in item for item in body["evidence"])


def test_quarantined_evidence_is_counted_but_never_quoted(client, evaluated):
    """A rejected quote is most often one the candidate never said."""
    state, _ = evaluated
    record = evaluations.current_for(state.session_id, ENGINE_VERSION)
    record.quarantined = [
        {"reason": "quote is not in the transcript", "skill_id": "skl_idem",
         "quote": "I have fifteen years of Kubernetes experience", "turn_id": "q_idem#0"}
    ]
    evaluations.save(record)

    body = client.get(f"{R}/sessions/{state.session_id}/evaluation/evidence").json()
    assert body["quarantined"] == [
        {"reason": "quote is not in the transcript", "skill_id": "skl_idem"}
    ]
    assert "fifteen years" not in repr(body)


# --------------------------------------------------------------------------- #
#  Authorization
# --------------------------------------------------------------------------- #
def test_an_unknown_session_is_a_404(client):
    assert client.get(f"{R}/sessions/nope/evaluation").status_code == 404


def test_a_session_belonging_to_another_interview_is_refused(client, evaluated):
    state, _ = evaluated
    response = client.get(
        f"{R}/sessions/{state.session_id}/evaluation", params={"interview_id": "iv_other"}
    )
    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]


def test_a_session_pinned_to_no_published_version_is_refused(client):
    """Refused — and now refused one step earlier, without saying why.

    The session's interview is not on file, so the authorization guard cannot
    establish that this organization owns it and answers 404 before the
    handler's own 403 is reached. Both are refusals; the 404 is the one that
    does not confirm the session exists.
    """
    state = F.strong_senior().session()
    state.interview_version = 0
    store.save(state)
    response = client.post(f"{R}/sessions/{state.session_id}/evaluation", json={})
    assert response.status_code in (403, 404)
    assert "interview" not in response.text.lower() or response.status_code == 403


def test_a_session_whose_interview_is_not_on_file_is_refused(client):
    """An orphaned session is unreachable, and says only "Not found".

    Ownership resolves through the interview. With no interview there is no
    organization, so there is nobody it could belong to — including the caller.
    """
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    response = client.get(f"{R}/sessions/{state.session_id}/evaluation")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found."


def test_the_recruiter_namespace_requires_a_signed_in_principal(client, evaluated):
    """What used to be a 503 stop is now a 401 door.

    The flag it replaced meant "refuse to serve this namespace at all, because
    there is no authentication". There is authentication now, so the control is
    the session: a client without one is refused, and the same client with one
    is served.
    """
    from fastapi.testclient import TestClient

    from services.api.app import app

    state, _ = evaluated
    assert client.get(f"{R}/sessions/{state.session_id}/evaluation").status_code == 200
    with TestClient(app) as anonymous:
        refused = anonymous.get(f"{R}/sessions/{state.session_id}/evaluation")
        assert refused.status_code == 401
        assert refused.headers.get("www-authenticate") == "Bearer"


def test_the_namespace_still_closes_when_a_deployment_has_no_accounts(
    client, evaluated, monkeypatch
):
    """The narrower job the old flag keeps: a deployment that requires
    authentication and has nobody who can authenticate serves nothing rather
    than relying on the absence of a login as its access control."""
    from fastapi.testclient import TestClient

    from services import config
    from services.api.app import app
    from services.data import accounts

    state, _ = evaluated
    monkeypatch.setattr(config, "RECRUITER_AUTH_REQUIRED", True)
    monkeypatch.setattr(accounts, "any_user_exists", lambda: False)
    with TestClient(app) as fresh:
        assert fresh.get(f"{R}/sessions/{state.session_id}/evaluation").status_code == 503


# --------------------------------------------------------------------------- #
#  The old scorer is untouched and still reachable
# --------------------------------------------------------------------------- #
def test_the_cue_coverage_scorer_still_serves_the_session_review(client):
    """Two systems, side by side, on the authored interview the old one scores."""
    # The authored CSR interview the boot sequence publishes, invited freshly
    # rather than reusing whatever invitation happens to be lying around.
    default = interviews.list_all()[0]
    invite = client.post(
        f"{R}/interviews/{default.id}/invitations",
        json={"candidates": ["Regression Candidate"]},
    ).json()["created"][0]
    session_id = _run_interview(client, invite["token"])

    review = client.get(f"{R}/sessions/{session_id}")
    assert review.status_code == 200
    assert "coverage" in review.json() and "items" in review.json()

    old = client.get(f"{R}/sessions/{session_id}/score")
    assert old.status_code == 200
    body = old.json()
    assert "band" in body and "skills" in body
    # The two contracts do not overlap: neither surface has drifted into the
    # other's shape, and neither engine calls the other.
    assert "skill_assessment" not in body
    assert "recommendation_explaination" not in body


def _imported_names(module) -> set[str]:
    import ast
    import inspect

    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names |= {f"{base}.{alias.name}" for alias in node.names}
    return names


def test_the_two_scorers_do_not_depend_on_each_other():
    """§24, checked by the import graph rather than asserted in prose."""
    from services.evaluation import (
        evaluator, evidence, integrity, jobs, scoring, snapshot,
    )

    for module in (evaluator, evidence, integrity, jobs, snapshot):
        assert "services.evaluation.scoring" not in _imported_names(module), (
            f"{module.__name__} reaches into the old cue-coverage scorer"
        )
    new_modules = {
        f"services.evaluation.{name}"
        for name in ("evaluator", "evidence", "integrity", "jobs", "snapshot", "stub")
    }
    assert not (_imported_names(scoring) & new_modules)


# --------------------------------------------------------------------------- #
#  §26 — the whole flow
# --------------------------------------------------------------------------- #
ANSWERS = [
    "We key the capture by the provider's idempotency reference and store it "
    "before we call out, so a retry finds the existing row and returns the same "
    "result instead of charging the customer a second time.",
    "Because the failure we actually hit in production was a timeout where the "
    "charge had gone through but our write had not, so the retry looked new to us "
    "and not to the provider.",
    "We reconcile against the settlement file every morning, match on the "
    "provider reference, and anything unmatched after two cycles goes to a queue "
    "a person works through rather than being auto-resolved.",
    "The trade-off is latency: writing the intent first costs us a round trip, "
    "and we took that over the risk of a duplicate charge because a double charge "
    "costs a refund, an apology and a support ticket.",
]


def _run_interview(client, token: str) -> str:
    """Sit the interview through the real orchestrator until it ends."""
    started = client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    }).json()
    session_id = started["session_id"]

    for turn in range(60):
        reply = client.post(f"/api/session/{session_id}/turn", json={
            "said": ANSWERS[turn % len(ANSWERS)],
        }).json()["reply"]
        if reply["ends"]:
            break
    return session_id


def test_the_whole_flow_from_job_to_evaluation(client):
    # ---- design ---------------------------------------------------------- #
    interviews.save(InterviewConfig(
        id="iv_e2e", title="Senior Backend Engineer", role="senior_backend_engineer",
        role_title="Senior Backend Engineer", job_id="job_e2e",
        jd_text="Own payment services.", experience_from=5, experience_to=9,
        interview_type="medium", difficulty="medium", recommended_duration_min=20,
        skills=[
            Skill(name="Idempotent design", competency_id="skl_idem", priority="high",
                  evaluated=True, assessment_scope="Retry-safe capture paths."),
            Skill(name="Reconciliation", competency_id="skl_recon", priority="high",
                  evaluated=True, assessment_scope="Settlement mismatches."),
            Skill(name="Incident response", competency_id="skl_inc", priority="low",
                  evaluated=True, assessment_scope="Live triage."),
        ],
        tasks=[
            Task(id="tsk_capture", name="Design payment capture",
                 description="Design idempotent capture.", required_skills=["skl_idem"]),
            Task(id="tsk_recon", name="Reconcile settlements",
                 description="Reconcile the ledger.", required_skills=["skl_recon"]),
            Task(id="tsk_oncall", name="Take payment on-call",
                 description="Handle an incident.", required_skills=["skl_inc"]),
        ],
    ))

    # ---- questions, publication, invitation -------------------------------- #
    assert client.post(
        f"{R}/interviews/iv_e2e/questions/generate", json={}
    ).status_code == 200
    published = client.post(f"{R}/interviews/iv_e2e/publish", json={})
    assert published.status_code == 200, published.text
    version = published.json()["version"]

    invite = client.post(
        f"{R}/interviews/iv_e2e/invitations", json={"candidates": ["Priya Sharma"]}
    ).json()["created"][0]

    # ---- the interview ----------------------------------------------------- #
    session_id = _run_interview(client, invite["token"])
    state = store.load(session_id)
    assert state.phase == "complete"
    assert state.interview_version == version

    # Completing the interview queues the evaluation without running it: the
    # candidate's last turn does not wait for the recruiter's pipeline.
    queued = evaluations.current_for(session_id, ENGINE_VERSION)
    assert queued is not None and queued.status == evaluations.PENDING

    # ---- evaluation -------------------------------------------------------- #
    body = client.post(f"{R}/sessions/{session_id}/evaluation", json={}).json()
    assert body["status"] == "completed", body
    assert body["evaluation_id"] == queued.evaluation_id      # the queued one ran

    # The evaluation is against the version the candidate actually sat.
    assert body["interview_version"] == version
    record = evaluations.get(body["evaluation_id"])
    published_definition = versions.definition_for("iv_e2e", version)
    assert [q["id"] for q in record.snapshot["questions"]] == [
        q.id for q in published_definition.questions
    ]
    assert record.snapshot_checksum

    # Every published skill is reported, and the numbers are internally consistent.
    assert [r["skill_name"] for r in body["skill_assessment"]] == [
        s.name for s in published_definition.skills
    ]
    assert body["candidate_details"]["total_score"] == sum(
        r["score"] for r in body["skill_assessment"]
    )
    assert body["maximum_possible_score"] == 25 * len(published_definition.skills)
    assert body["recommendation"] in (
        "Not suitable for this role", "Needs further evaluation", "Proceed to next round"
    )

    # ---- evidence traces back to the conversation -------------------------- #
    evidence = client.get(
        f"{R}/sessions/{session_id}/evaluation/evidence"
    ).json()["evidence"]
    assert evidence
    asked = {q_id for q_id in state.asked_item_ids}
    for item in evidence:
        assert item["question_id"] in asked
        assert item["turn_id"].startswith(item["question_id"])

    # ---- the audit trail --------------------------------------------------- #
    from services.data import audit

    events = [
        e for e in audit.read_product()
        if e.get("session") == session_id and e["event"].startswith("EVALUATION_")
    ]
    assert [e["event"] for e in events] == [
        "EVALUATION_REQUESTED", "EVALUATION_STARTED", "EVALUATION_COMPLETED"
    ]
    assert all("answer" not in e and "transcript" not in e for e in events)


def test_the_candidate_never_sees_an_evaluation(client):
    """The whole subsystem lives behind the recruiter namespace."""
    paths = client.get("/openapi.json").json()["paths"]
    candidate_paths = [
        path for path in paths
        if path.startswith(("/api/session", "/api/invite"))
    ]
    assert candidate_paths, "the candidate routes should be mounted"
    assert not any("evaluation" in path for path in candidate_paths)
    # And every evaluation route is behind the recruiter namespace.
    assert all(
        path.startswith("/api/recruiter") for path in paths if "evaluation" in path
    )


# --------------------------------------------------------------------------- #
#  What the recruiter report needs from the contract
# --------------------------------------------------------------------------- #
def test_the_envelope_names_the_interview_at_every_status(client):
    """A report has to say who and which interview before it can say a score."""
    interviews.save(InterviewConfig(
        id="iv_fix", title="Senior Backend Engineer — payments screen",
        role="senior_backend_engineer", role_title="Senior Backend Engineer, Payments",
    ))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    jobs.request(state)

    body = client.get(f"{R}/sessions/{state.session_id}/evaluation").json()
    assert body["status"] == "pending"
    assert body["interview"]["title"] == "Senior Backend Engineer — payments screen"
    assert body["interview"]["version"] == 1
    assert body["session"]["candidate_name"] == "Strong Senior"
    assert body["session"]["phase"] == "complete"
    assert body["session"]["questions_asked"] == 3


def test_interview_depth_is_the_configured_scope_not_the_probe_ladder(client, evaluated):
    """§13 — two different ideas, and the API must not hand over one for the other."""
    _, body = evaluated
    assert body["interview"]["interview_depth"] in ("short", "medium", "deep")
    assert body["interview"]["interview_depth"] not in ("direct", "probed", "deep_probed")
    for row in body["skill_assessment"]:
        assert row["depth_evaluation"]["depth_reached"] in (
            "direct", "probed", "deep_probed"
        )


def test_the_interview_title_is_frozen_at_the_moment_it_was_sat(client, evaluated):
    """A report that renames itself is a report about a different interview."""
    state, body = evaluated
    original = body["interview"]["title"]

    cfg = interviews.get("iv_fix")
    cfg.title = "Renamed after the fact"
    interviews.save(cfg)

    again = client.get(f"{R}/sessions/{state.session_id}/evaluation").json()
    assert again["interview"]["title"] == original


def test_evidence_carries_the_question_it_came_from(client, evaluated):
    """So the report can show what was asked without a request per item."""
    state, _ = evaluated
    body = client.get(f"{R}/sessions/{state.session_id}/evaluation/evidence").json()
    published = {q.id: q.question_text for q in versions.definition_for("iv_fix", 1).questions}
    assert body["evidence"]
    for item in body["evidence"]:
        assert item["question_text"] == published[item["question_id"]]


def test_the_whole_report_is_two_requests(client, evaluated):
    """§26 — never one request per skill, and never one per evidence item."""
    state, body = evaluated
    evidence = client.get(f"{R}/sessions/{state.session_id}/evaluation/evidence").json()
    assert len(body["skill_assessment"]) > 1
    assert len(evidence["evidence"]) > 1


# --------------------------------------------------------------------------- #
#  The console's fixtures are generated from these serialisers (§25)
# --------------------------------------------------------------------------- #
def test_the_recruiter_report_fixtures_still_match_the_contract():
    """The console's tests run against committed JSON. This is what stops that
    JSON quietly describing a contract the backend no longer serves.

    Compares SHAPE, not content: the keys the report reads, at every status.
    If this fails, re-run `python tools/make_report_fixtures.py`.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    directory = root / "apps" / "recruiter" / "src" / "test" / "fixtures"

    completed = json.loads((directory / "evaluation.completed.json").read_text())
    assert completed["status"] == "completed"
    assert set(completed["interview"]) == {
        "interview_id", "version", "title", "role_title", "interview_depth",
        "difficulty", "recommended_duration_min", "experience_from", "experience_to",
    }
    assert set(completed["session"]) == {
        "candidate_name", "phase", "channel", "started_at", "completed_at",
        "questions_asked",
    }
    assert set(completed["candidate_details"]) == {
        "name", "job_role", "experience_level", "total_score", "overall_rating",
    }
    for row in completed["skill_assessment"]:
        assert set(row) == {
            "skill_name", "discussion_status", "score", "remarks",
            "Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication",
            "depth_evaluation",
        }
        assert set(row["depth_evaluation"]) == {
            "depth_reached", "depth_demonstrated", "dimensions_demonstrated",
            "dimensions_missing", "evidence_confidence",
        }

    # Every state the report renders is covered by a fixture.
    for name, status in (
        ("evaluation.pending.json", "pending"),
        ("evaluation.running.json", "running"),
        ("evaluation.failed.json", "failed"),
    ):
        payload = json.loads((directory / name).read_text())
        assert payload["status"] == status
        # Nothing scored is present at any status but completed.
        assert "candidate_details" not in payload
        assert "skill_assessment" not in payload

    evidence = json.loads((directory / "evidence.json").read_text())
    for item in evidence["evidence"]:
        assert set(item) == {
            "session_id", "skill_id", "skill_name", "task_id", "question_id",
            "question_text", "turn_id", "depth_stage", "depth_dimension",
            "evidence_type", "evidence_strength", "supports_criterion",
            "candidate_quote",
        }
    # Refused evidence is a reason, never the text it was refused for.
    for row in evidence["quarantined"]:
        assert set(row) == {"reason", "skill_id"}


def test_the_report_fixtures_cover_both_depth_cases_that_matter():
    """direct → deep_probed and deep_probed → direct, in one payload.

    The console asserts against these; if a regenerated fixture lost either
    case, the UI tests would still pass while proving less.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    completed = json.loads(
        (root / "apps" / "recruiter" / "src" / "test" / "fixtures"
         / "evaluation.completed.json").read_text()
    )
    pairs = {
        (r["depth_evaluation"]["depth_reached"], r["depth_evaluation"]["depth_demonstrated"])
        for r in completed["skill_assessment"]
    }
    assert ("direct", "deep_probed") in pairs
    assert ("deep_probed", "direct") in pairs

    statuses = {r["discussion_status"] for r in completed["skill_assessment"]}
    assert {"discussed", "mentioned", "not_discussed"} <= statuses


# --------------------------------------------------------------------------- #
#  The assessment result over HTTP (§17, §20)
# --------------------------------------------------------------------------- #
def test_the_result_endpoint_returns_the_whole_assessment(client, evaluated):
    state, _ = evaluated
    response = client.get(f"{R}/sessions/{state.session_id}/evaluation/result")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "result_contract_version", "evaluation", "interview", "session", "overall",
        "coverage", "skills", "questions", "evidence", "summary", "scales", "integrity",
    }
    # One request is enough: nothing a report renders is missing from it.
    overall = payload["overall"]
    assert overall["max_score"] == 25 * sum(
        1 for s in payload["skills"] if s["discussion_status"] == "discussed")
    assert payload["skills"] and payload["questions"] and payload["evidence"]


def test_the_result_is_addressable_by_evaluation_id_too(client, evaluated):
    state, body = evaluated
    by_session = client.get(f"{R}/sessions/{state.session_id}/evaluation/result").json()
    by_id = client.get(f"{R}/evaluations/{body['evaluation_id']}/result").json()
    assert by_session == by_id


def test_the_result_says_which_contract_version_it_is(client, evaluated):
    from packages.types.evaluation import RESULT_CONTRACT_VERSION

    state, _ = evaluated
    payload = client.get(f"{R}/sessions/{state.session_id}/evaluation/result").json()
    assert payload["result_contract_version"] == RESULT_CONTRACT_VERSION


def test_a_pending_evaluation_has_no_result_over_http(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    jobs.request(state)

    response = client.get(f"{R}/sessions/{state.session_id}/evaluation/result")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "pending" in detail["message"]
    # The status envelope is returned so a report can render the waiting state
    # without a second request — and it carries no scores.
    assert detail["evaluation"]["status"] == "pending"
    assert "candidate_details" not in detail["evaluation"]


def test_a_session_with_no_evaluation_has_no_result(client):
    interviews.save(InterviewConfig(id="iv_fix", title="X", role="senior_backend_engineer"))
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    assert client.get(
        f"{R}/sessions/{state.session_id}/evaluation/result").status_code == 404


def test_an_unauthorized_session_has_no_result(client, evaluated):
    state, _ = evaluated
    response = client.get(f"{R}/sessions/{state.session_id}/evaluation/result",
                          params={"interview_id": "iv_other"})
    assert response.status_code == 403


def test_a_contradictory_result_is_refused_rather_than_served(client, evaluated):
    """§22 — a page that quietly says two things is worse than an error."""
    state, body = evaluated
    record = evaluations.get(body["evaluation_id"])
    record.result["candidate_details"]["total_score"] += 13
    evaluations.save(record)

    response = client.get(f"{R}/sessions/{state.session_id}/evaluation/result")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "does not agree with itself" in detail["message"]
    assert any("total_score" in v for v in detail["violations"])


def test_the_result_never_carries_a_credential_or_a_prompt(client, evaluated):
    from services import config

    state, _ = evaluated
    text = client.get(f"{R}/sessions/{state.session_id}/evaluation/result").text.lower()
    if config.OPENROUTER_API_KEY:
        assert config.OPENROUTER_API_KEY.lower() not in text
    for leak in ("sk-or", "authorization", "you assess one skill", "strict json",
                 "candidate_text_start"):
        assert leak not in text
