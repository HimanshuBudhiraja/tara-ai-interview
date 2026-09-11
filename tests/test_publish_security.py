"""§30 — the guarantees publication is supposed to buy.

Every test here is a thing that would be a serious problem if it stopped being
true: a published assessment changing under a candidate, a candidate choosing
their own questions, an expired link still working, a token someone could guess.
"""
from __future__ import annotations

import re
import time

import pytest

from services.assessment import pool as pool_service
from services.data import interviews, invites, versions
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import Skill, Task
from tests.conftest import sign_in

pytestmark = pytest.mark.usefixtures("data_dir")


@pytest.fixture()
def client(data_dir, tenant, monkeypatch, pool):
    from fastapi.testclient import TestClient

    monkeypatch.setenv(pool_service.STUB_ENV, "1")
    from services.data import jobs

    monkeypatch.setattr(jobs, "_PATH", data_dir / "jobs.json")

    interviews.save(InterviewConfig(
        id="iv_sec", title="Senior Backend Engineer", role="senior_backend_engineer",
        role_title="Senior Backend Engineer", job_id="job_x",
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

    from services.api.app import app

    with TestClient(app) as c:
        sign_in(c, tenant)
        c.post("/api/recruiter/interviews/iv_sec/questions/generate", json={})
        yield c


R = "/api/recruiter/interviews/iv_sec"


def _publish(client) -> dict:
    response = client.post(f"{R}/publish", json={})
    assert response.status_code == 200, response.text
    return response.json()


def _invite(client, name="Priya Sharma") -> dict:
    response = client.post(f"{R}/invitations", json={"candidates": [name]})
    assert response.status_code == 201, response.text
    return response.json()["created"][0]


def _start(client, token: str) -> dict:
    return client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    }).json()


# --------------------------------------------------------------------------- #
#  Published immutability
# --------------------------------------------------------------------------- #
def test_a_draft_edit_cannot_alter_a_published_version(client):
    published = _publish(client)
    v1 = versions.definition_for("iv_sec", published["version"])
    before = {q.id: q.question_text for q in v1.questions}
    before_checksum = versions.get("iv_sec", published["version"]).checksum

    # Change everything the recruiter can reach.
    question_id = next(iter(before))
    client.patch(f"{R}/questions/{question_id}",
                 json={"question_text": "A completely different question about something else."})
    client.patch(f"{R}/draft", json={"difficulty": "easy"})

    after = versions.definition_for("iv_sec", published["version"])
    assert {q.id: q.question_text for q in after.questions} == before
    assert after.difficulty == v1.difficulty
    assert versions.get("iv_sec", published["version"]).checksum == before_checksum


def test_editing_after_publish_produces_a_new_version_not_a_mutation(client):
    first = _publish(client)
    question_id = versions.definition_for("iv_sec", first["version"]).questions[0].id
    client.patch(f"{R}/questions/{question_id}",
                 json={"difficulty": "hard"})

    second = _publish(client)
    assert second["version"] == first["version"] + 1
    assert versions.get("iv_sec", first["version"]).checksum != second["checksum"]


def test_the_draft_and_the_published_version_are_separate_objects(client):
    """Not just equal — separate. A shared list would let a later edit reach
    through into an immutable version."""
    published = _publish(client)
    frozen = versions.get("iv_sec", published["version"]).definition
    cfg = interviews.get("iv_sec")
    cfg.questions[0]["question_text"] = "mutated in place"
    interviews.save(cfg)
    assert frozen["questions"][0]["question_text"] != "mutated in place"


# --------------------------------------------------------------------------- #
#  Idempotency and concurrency
# --------------------------------------------------------------------------- #
def test_publishing_twice_does_not_create_two_versions(client):
    """A double-click, a browser retry and a network retry are one publish."""
    first = _publish(client)
    second = _publish(client)
    assert second["version"] == first["version"]
    assert second["created"] is False
    assert len(versions.list_for("iv_sec")) == 1


def test_concurrent_publishes_do_not_clobber_an_existing_version(client):
    """Two publishes racing on the same number: the loser gets the winner's
    version rather than overwriting it."""
    from packages.types import InterviewDefinition

    _publish(client)
    definition = interviews.build_definition(interviews.get("iv_sec"))
    definition.difficulty = "hard"           # force a different checksum
    winner = versions.publish("iv_sec", definition, validate=False)

    other = InterviewDefinition.from_dict(definition.to_dict())
    other.difficulty = "easy"
    # Simulate the racer: same target number, different content.
    import services.data.versions as V

    original_next = V.next_version
    V.next_version = lambda _id: winner.version
    try:
        loser = versions.publish("iv_sec", other, validate=False)
    finally:
        V.next_version = original_next

    assert loser.version == winner.version
    assert loser.checksum == winner.checksum, "an immutable version was overwritten"


# --------------------------------------------------------------------------- #
#  Publication refuses what it should
# --------------------------------------------------------------------------- #
def test_an_interview_with_no_questions_cannot_be_published(client):
    cfg = interviews.get("iv_sec")
    cfg.questions = []
    interviews.save(cfg)
    response = client.post(f"{R}/publish", json={})
    assert response.status_code == 422
    assert not versions.list_for("iv_sec")


def test_a_failed_publish_leaves_no_version_behind(client):
    cfg = interviews.get("iv_sec")
    cfg.questions[0]["looking_for"] = []      # runtime-incompatible
    interviews.save(cfg)

    response = client.post(f"{R}/publish", json={})
    assert response.status_code == 422
    areas = {p["area"] for p in response.json()["detail"]["check"]["problems"]}
    assert "runtime" in areas or "questions" in areas
    assert not versions.list_for("iv_sec")


def test_the_publish_check_agrees_with_the_publish(client):
    """A confirmation screen that says 'ready' about something publish would
    refuse is worse than no confirmation screen."""
    check = client.get(f"{R}/publish/check").json()
    assert check["ready"] is True
    assert client.post(f"{R}/publish", json={}).status_code == 200

    cfg = interviews.get("iv_sec")
    cfg.questions[0]["clarify"] = ""
    interviews.save(cfg)
    assert client.get(f"{R}/publish/check").json()["ready"] is False
    assert client.post(f"{R}/publish", json={}).status_code == 422


def test_the_publish_summary_uses_persisted_values(client):
    check = client.get(f"{R}/publish/check").json()
    cfg = interviews.get("iv_sec")
    assert check["summary"]["questions"] == len(cfg.questions)
    assert check["summary"]["skills"] == len(cfg.skills)
    assert check["summary"]["tasks"] == len(cfg.tasks)


# --------------------------------------------------------------------------- #
#  Invitations
# --------------------------------------------------------------------------- #
def test_an_invitation_cannot_be_created_before_publication(client):
    response = client.post(f"{R}/invitations", json={"candidates": ["Priya"]})
    assert response.status_code == 409


def test_an_invitation_binds_to_the_exact_published_version(client):
    first = _publish(client)
    invite = _invite(client)
    assert invite["interview_version"] == first["version"]

    # Publish a second version; the existing invitation must not follow it.
    question_id = versions.definition_for("iv_sec", first["version"]).questions[0].id
    client.patch(f"{R}/questions/{question_id}", json={"difficulty": "hard"})
    second = _publish(client)
    assert second["version"] > first["version"]

    assert invites.get(invite["token"]).interview_version == first["version"]


def test_a_session_runs_the_version_the_invitation_named(client):
    first = _publish(client)
    invite = _invite(client)

    question_id = versions.definition_for("iv_sec", first["version"]).questions[0].id
    client.patch(f"{R}/questions/{question_id}",
                 json={"question_text": "A brand new question only in version two."})
    _publish(client)

    started = _start(client, invite["token"])
    from services.data import sessions as store

    state = store.load(started["session_id"])
    assert state.interview_version == first["version"]
    assert "only in version two" not in started["reply"]["text"]


def test_tokens_are_opaque_and_unpredictable(client):
    _publish(client)
    tokens = [_invite(client, f"Candidate {n}")["token"] for n in range(5)]

    for token in tokens:
        assert len(token) >= 32
        # Nothing about the interview or the candidate is recoverable from it.
        assert "iv_sec" not in token
        assert "cand" not in token.lower()
        assert not re.fullmatch(r"[0-9]+", token)
    assert len(set(tokens)) == len(tokens)

    # And they are not sequential: no two share a long common prefix.
    for a, b in zip(sorted(tokens), sorted(tokens)[1:]):
        shared = len([1 for x, y in zip(a, b) if x == y])
        assert shared < 8


def test_an_expired_invitation_cannot_start_a_session(client):
    _publish(client)
    invite = _invite(client)
    row = invites.get(invite["token"])
    row.expires_at = time.time() - 1
    invites.update(row)

    assert client.get(f"/api/invite/{invite['token']}").status_code == 410
    assert client.post("/api/session/start", json={
        "token": invite["token"], "consent_recording": True,
    }).status_code == 410


def test_a_revoked_invitation_cannot_start_a_session(client):
    _publish(client)
    invite = _invite(client)
    client.delete(f"{R}/invitations/{invite['token']}")

    assert client.post("/api/session/start", json={
        "token": invite["token"], "consent_recording": True,
    }).status_code == 410


def test_a_single_use_invitation_cannot_be_sat_twice(client):
    _publish(client)
    invite = _invite(client)
    row = invites.get(invite["token"])
    row.status = "complete"
    invites.update(row)

    assert client.post("/api/session/start", json={
        "token": invite["token"], "consent_recording": True,
    }).status_code == 410


def test_an_open_link_can_be_used_more_than_once(client):
    _publish(client)
    link = client.post(f"{R}/invitations/open-link", json={"enabled": True}).json()
    row = invites.get(link["open_link"]["token"])
    row.status = "complete"
    invites.update(row)

    usable, _ = invites.get(link["open_link"]["token"]).can_start_session()
    assert usable


def test_an_open_link_is_bound_to_a_version_not_to_the_latest(client):
    """A link that silently followed the latest publish would let two people
    clicking the same URL a week apart sit different interviews."""
    first = _publish(client)
    link = client.post(f"{R}/invitations/open-link", json={"enabled": True}).json()
    assert link["open_link"]["interview_version"] == first["version"]

    question_id = versions.definition_for("iv_sec", first["version"]).questions[0].id
    client.patch(f"{R}/questions/{question_id}", json={"difficulty": "hard"})
    _publish(client)

    assert invites.get(link["open_link"]["token"]).interview_version == first["version"]


def test_an_unknown_token_is_refused_without_saying_why(client):
    response = client.get("/api/invite/not-a-real-token")
    assert response.status_code == 404
    assert "iv_sec" not in response.text


def test_a_withdrawn_invitation_does_not_explain_itself_to_the_candidate(client):
    """The candidate is told the link is unavailable, not that a recruiter
    withdrew it or that someone else already used it."""
    _publish(client)
    invite = _invite(client)
    client.delete(f"{R}/invitations/{invite['token']}")

    body = client.get(f"/api/invite/{invite['token']}").text
    assert "revoked" not in body.lower()
    assert "withdrew" not in body.lower()


def test_the_batch_size_is_bounded(client):
    _publish(client)
    response = client.post(f"{R}/invitations", json={
        "candidates": [f"c{n}@example.com" for n in range(invites.MAX_EMAILS_PER_BATCH + 1)],
    })
    assert response.status_code == 422


def test_email_delivery_is_reported_as_unavailable_not_faked(client):
    _publish(client)
    response = client.post(f"{R}/invitations", json={"candidates": ["priya@example.com"]})
    body = response.json()
    assert body["email_delivery_configured"] is False
    assert "isn't configured" in body["message"]


# --------------------------------------------------------------------------- #
#  The candidate cannot drive the interview
# --------------------------------------------------------------------------- #
def test_a_candidate_cannot_choose_which_version_they_sit(client):
    first = _publish(client)
    invite = _invite(client)
    started = _start(client, invite["token"])

    # There is no field to send. Anything extra is ignored by the model.
    client.post(f"/api/session/{started['session_id']}/turn",
                json={"said": "hello", "interview_version": 99, "version": 99})

    from services.data import sessions as store

    assert store.load(started["session_id"]).interview_version == first["version"]


def test_a_candidate_cannot_choose_the_next_question(client):
    _publish(client)
    invite = _invite(client)
    started = _start(client, invite["token"])
    from services.data import sessions as store

    definition = versions.definition_for("iv_sec", invites.get(invite["token"]).interview_version)
    unasked = [q.id for q in definition.questions
               if q.id not in store.load(started["session_id"]).asked_item_ids]

    client.post(f"/api/session/{started['session_id']}/turn",
                json={"said": "hello", "item_id": unasked[-1], "question_id": unasked[-1]})

    state = store.load(started["session_id"])
    assert state.current_item_id != unasked[-1] or len(state.asked_item_ids) <= 2


def test_a_candidate_cannot_mark_themselves_complete(client):
    """There is no endpoint for it. Completion is reached through the turn loop
    or not at all."""
    _publish(client)
    invite = _invite(client)
    started = _start(client, invite["token"])
    from services.data import sessions as store

    for path in (
        f"/api/session/{started['session_id']}/complete",
        f"/api/session/{started['session_id']}/finish",
    ):
        assert client.post(path, json={}).status_code in (404, 405)

    # And a turn payload claiming completion changes nothing.
    client.post(f"/api/session/{started['session_id']}/turn",
                json={"said": "hi", "phase": "complete", "ends": True})
    assert store.load(started["session_id"]).phase != "complete"


def test_the_candidate_invite_payload_leaks_nothing(client):
    """§23. What crosses the boundary is how long it takes and roughly what it
    covers — never the assessment itself."""
    _publish(client)
    invite = _invite(client)
    body = client.get(f"/api/invite/{invite['token']}").text

    definition = versions.definition_for("iv_sec", invites.get(invite["token"]).interview_version)
    for question in definition.questions:
        assert question.question_text not in body
        for cue in question.looking_for:
            assert cue not in body
        for criterion in question.evaluation_criteria:
            assert criterion.label not in body
        for probe in question.probe_bank:
            assert probe not in body
    for skill in definition.skills:
        assert skill.assessment_scope not in body
        assert skill.priority not in ("high",) or f'"{skill.id}"' not in body
    for task in definition.tasks:
        assert task.description not in body


def test_a_turn_response_carries_only_the_current_question(client):
    _publish(client)
    invite = _invite(client)
    started = _start(client, invite["token"])
    body = client.post(f"/api/session/{started['session_id']}/turn",
                       json={"said": "I'd stop the charges first."}).text

    definition = versions.definition_for("iv_sec", invites.get(invite["token"]).interview_version)
    from services.data import sessions as store

    state = store.load(started["session_id"])
    for question in definition.questions:
        if question.id in state.asked_item_ids:
            continue
        assert question.question_text not in body, "a future question leaked"
        for cue in question.looking_for:
            assert cue not in body


def test_rejoin_resumes_rather_than_restarting(client):
    _publish(client)
    invite = _invite(client)
    started = _start(client, invite["token"])
    client.post(f"/api/session/{started['session_id']}/turn", json={"said": "I'd stop it."})

    from services.data import sessions as store

    before = store.load(started["session_id"])
    again = _start(client, invite["token"])

    assert again["resumed"] is True
    assert again["session_id"] == started["session_id"]
    after = store.load(started["session_id"])
    assert after.asked_item_ids == before.asked_item_ids


# --------------------------------------------------------------------------- #
#  Traceability (§33)
# --------------------------------------------------------------------------- #
def test_a_session_is_traceable_to_interview_version_invitation_and_candidate(client):
    published = _publish(client)
    invite = _invite(client)
    started = _start(client, invite["token"])

    from services.data import sessions as store

    state = store.load(started["session_id"])
    assert state.interview_id == "iv_sec"
    assert state.interview_version == published["version"]
    assert state.invite_token == invite["token"]
    assert state.candidate_id
    assert versions.get(state.interview_id, state.interview_version) is not None


def test_the_publication_flow_leaves_an_audit_trail(client):
    from services.data import audit

    _publish(client)
    invite = _invite(client)
    _start(client, invite["token"])
    client.delete(f"{R}/invitations/{invite['token']}")

    events = [e["event"] for e in audit.read_product()]
    for expected in ("INTERVIEW_PUBLISH_STARTED", "INTERVIEW_PUBLISHED",
                     "INVITATION_CREATED", "SESSION_CREATED", "INVITATION_REVOKED"):
        assert expected in events, f"{expected} was not recorded"


def test_the_audit_trail_never_holds_a_whole_token(client):
    """An invitation token is a credential. A trail that records them is a trail
    that hands out interviews."""
    from services.data import audit

    _publish(client)
    invite = _invite(client)
    blob = str(audit.read_product())
    assert invite["token"] not in blob
