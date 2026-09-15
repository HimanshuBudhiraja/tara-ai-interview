"""Access control: who may reach what, and what happens when they may not.

Every test here is a thing that would be a breach if it stopped being true. The
file is organised by the property under attack rather than by module, because
that is how the questions get asked — "can another organization read this
report?" is one question whatever code answers it.

Two policies it pins, because they are decisions rather than accidents:

  * **401 for "we do not know who you are", 403 for "we know, and no".** A
    missing session is an authentication failure; an insufficient role is an
    authorization one, and telling them apart is what lets a client know
    whether logging in would help.
  * **404 for another tenant's resources.** Confirming that
    `iv_someone_elses` exists tells an attacker which ids are real. A foreign
    resource and a nonexistent one return the same status and the same body.
"""
from __future__ import annotations

import json
import time

import pytest

from packages.types.evaluation import ENGINE_VERSION
from services.ai.workloads.interview_designer import Skill, Task
from services.assessment import pool as pool_service
from services.data import accounts, audit, evaluations, interviews, invites, versions
from services.data import sessions as store
from services.data.interviews import InterviewConfig
from services.evaluation import stub as eval_stub
from services.security import authz
from services.security import principal as security
from services.security import ratelimit
from tests.conftest import TEST_PASSWORD, sign_in

pytestmark = pytest.mark.usefixtures("data_dir")

ANSWER = (
    "I put an idempotency key on the capture call and store it with the charge row in "
    "the same transaction, because the retry has to find the original result rather "
    "than create a second one."
)


# --------------------------------------------------------------------------- #
#  Scaffolding — two organizations, and an interview in each
# --------------------------------------------------------------------------- #
def _interview(interview_id: str, organization_id: str, title: str = "Backend") -> InterviewConfig:
    return interviews.save(InterviewConfig(
        id=interview_id, title=title, role="senior_backend_engineer",
        role_title=title, jd_text="Own the payment services.",
        experience_from=5, experience_to=9, interview_type="medium",
        difficulty="medium", recommended_duration_min=20,
        organization_id=organization_id,
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


@pytest.fixture()
def world(data_dir, monkeypatch, pool):
    """Two tenants, each with an interview, and a client for each.

    Deliberately concrete: tenant isolation is only meaningfully testable with a
    second organization that actually owns something.
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    monkeypatch.setenv(pool_service.STUB_ENV, "1")
    monkeypatch.setenv(eval_stub.STUB_ENV, "1")
    from services.ai import gateway as ai_gateway
    from services.data import jobs as job_store

    monkeypatch.setattr(job_store, "_PATH", data_dir / "jobs.json")
    monkeypatch.setattr(ai_gateway.get_gateway(), "live", False)

    acme = accounts.create_organization("Acme Hiring", organization_id="org_acme")
    rival = accounts.create_organization("Rival Corp", organization_id="org_rival")
    acme_admin = accounts.create_user(
        acme.organization_id, "admin@acme.test", TEST_PASSWORD, role=accounts.ADMIN)
    acme_recruiter = accounts.create_user(
        acme.organization_id, "rec@acme.test", TEST_PASSWORD, role=accounts.RECRUITER)
    acme_viewer = accounts.create_user(
        acme.organization_id, "view@acme.test", TEST_PASSWORD, role=accounts.VIEWER)
    rival_admin = accounts.create_user(
        rival.organization_id, "admin@rival.test", TEST_PASSWORD, role=accounts.ADMIN)

    _interview("iv_acme", acme.organization_id, "Acme Backend")
    _interview("iv_rival", rival.organization_id, "Rival Backend")

    from services.api.app import app

    acme_client = TestClient(app)
    acme_client.__enter__()
    acme_client.post("/api/auth/login", json={
        "email": acme_admin.email, "password": TEST_PASSWORD})

    rival_client = TestClient(app)
    rival_client.__enter__()
    rival_client.post("/api/auth/login", json={
        "email": rival_admin.email, "password": TEST_PASSWORD})

    # Each organization generates its own questions. Notably the acme client
    # *cannot* do this for iv_rival, which is the property under test — so the
    # fixture would silently produce an unpublishable interview if it tried.
    for client, interview_id in ((acme_client, "iv_acme"), (rival_client, "iv_rival")):
        ready = client.post(
            f"/api/recruiter/interviews/{interview_id}/questions/generate", json={})
        assert ready.status_code == 200, ready.text

    anonymous = TestClient(app)
    anonymous.__enter__()

    yield SimpleNamespace(
        acme=acme, rival=rival,
        admin=acme_admin, recruiter=acme_recruiter, viewer=acme_viewer,
        rival_admin=rival_admin,
        client=acme_client, rival_client=rival_client, anonymous=anonymous,
    )
    for c in (acme_client, rival_client, anonymous):
        c.__exit__(None, None, None)


def _publish(client, interview_id: str) -> int:
    response = client.post(f"/api/recruiter/interviews/{interview_id}/publish", json={})
    assert response.status_code == 200, response.text
    return response.json()["version"]


def _invite(client, interview_id: str, name: str = "Priya") -> dict:
    response = client.post(
        f"/api/recruiter/interviews/{interview_id}/invitations",
        json={"candidates": [name]},
    )
    assert response.status_code == 201, response.text
    return response.json()["created"][0]


def _sit(client, token: str) -> str:
    """One candidate, start to finish, over the candidate API only."""
    started = client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text"})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    for _ in range(40):
        reply = client.post(
            f"/api/session/{session_id}/turn",
            params={"token": token}, json={"said": ANSWER},
        ).json()["reply"]
        if reply["ends"]:
            break
    return session_id


# =========================================================================== #
#  Authentication
# =========================================================================== #
def test_an_unauthenticated_recruiter_request_is_401(world):
    for path in (
        "/api/recruiter/interviews",
        "/api/recruiter/overview",
        "/api/recruiter/candidates",
        "/api/recruiter/interviews/iv_acme",
        "/api/recruiter/interviews/iv_acme/questions",
        "/api/recruiter/pilot/summary",
        "/api/admin/interviews",
    ):
        response = world.anonymous.get(path)
        assert response.status_code == 401, path
        assert response.headers.get("www-authenticate") == "Bearer"


def test_an_unauthenticated_write_is_401_before_it_reaches_anything(world):
    before = interviews.get("iv_acme").title
    response = world.anonymous.patch(
        "/api/recruiter/interviews/iv_acme", json={"title": "Renamed by a stranger"})
    assert response.status_code == 401
    assert interviews.get("iv_acme").title == before


def test_a_wrong_password_is_401(world):
    response = world.anonymous.post("/api/auth/login", json={
        "email": world.admin.email, "password": "not the password"})
    assert response.status_code == 401
    assert security.SESSION_COOKIE not in response.cookies


def test_an_unknown_address_and_a_wrong_password_are_indistinguishable(world):
    unknown = world.anonymous.post("/api/auth/login", json={
        "email": "nobody@nowhere.test", "password": TEST_PASSWORD})
    wrong = world.anonymous.post("/api/auth/login", json={
        "email": world.admin.email, "password": "wrong"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_a_disabled_user_cannot_sign_in_and_their_sessions_stop_working(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as c:
        sign_in_response = c.post("/api/auth/login", json={
            "email": world.recruiter.email, "password": TEST_PASSWORD})
        assert sign_in_response.status_code == 200
        assert c.get("/api/recruiter/interviews").status_code == 200

        accounts.set_status(world.recruiter.user_id, accounts.DISABLED)
        # The live session stops immediately: the principal is rebuilt from the
        # user record on every request, not trusted from the cookie.
        assert c.get("/api/recruiter/interviews").status_code == 401
        assert c.post("/api/auth/login", json={
            "email": world.recruiter.email, "password": TEST_PASSWORD}).status_code == 401


def test_signing_in_succeeds_and_says_who_you_are(world):
    body = world.client.get("/api/auth/me").json()
    assert body["user"]["email"] == world.admin.email
    assert body["user"]["organization_id"] == world.acme.organization_id
    assert body["user"]["role"] == accounts.ADMIN
    assert body["organization"]["name"] == "Acme Hiring"
    assert "password" not in json.dumps(body).lower()


def test_a_bearer_token_is_accepted_for_clients_without_cookies(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={
            "email": world.admin.email, "password": TEST_PASSWORD})
        token = login.cookies.get(security.SESSION_COOKIE)
        assert token
    with TestClient(app) as bare:
        assert bare.get("/api/recruiter/interviews").status_code == 401
        ok = bare.get("/api/recruiter/interviews",
                      headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200


def test_logging_out_revokes_the_session_server_side(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={
            "email": world.admin.email, "password": TEST_PASSWORD})
        token = c.cookies.get(security.SESSION_COOKIE)
        assert c.post("/api/auth/logout").status_code == 200
        # Not just "the browser forgot": the token itself is dead.
        with TestClient(app) as replay:
            refused = replay.get("/api/recruiter/interviews",
                                 headers={"Authorization": f"Bearer {token}"})
            assert refused.status_code == 401


def test_an_idle_session_expires(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={
            "email": world.admin.email, "password": TEST_PASSWORD})
        token = c.cookies.get(security.SESSION_COOKIE)

    session = accounts.get_session(token)
    session.last_seen_at = time.time() - accounts.IDLE_TIMEOUT_SEC - 60
    rows = accounts._read(accounts.SESSIONS, accounts.LoginSession)  # noqa: SLF001
    rows[token] = session
    accounts._write(accounts.SESSIONS, rows)  # noqa: SLF001

    with TestClient(app) as stale:
        assert stale.get("/api/recruiter/interviews",
                         headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_a_forged_or_random_session_cookie_is_401(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    for value in ("", "x", "not-a-token", "a" * 43, world.admin.user_id):
        with TestClient(app) as c:
            c.cookies.set(security.SESSION_COOKIE, value)
            assert c.get("/api/recruiter/interviews").status_code == 401


def test_identity_cannot_be_asserted_by_the_client(world):
    """No header, body field or query parameter may name the principal."""
    attempts = [
        {"headers": {"X-Organization-Id": "org_acme"}},
        {"headers": {"X-User-Id": world.admin.user_id}},
        {"params": {"organization_id": "org_acme", "user_id": world.admin.user_id}},
        {"params": {"role": "admin"}},
    ]
    for attempt in attempts:
        response = world.anonymous.get("/api/recruiter/interviews", **attempt)
        assert response.status_code == 401, attempt


# =========================================================================== #
#  Tenant isolation
# =========================================================================== #
def test_a_recruiter_reads_their_own_organizations_interview(world):
    response = world.client.get("/api/recruiter/interviews/iv_acme")
    assert response.status_code == 200
    assert response.json()["id"] == "iv_acme"


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/recruiter/interviews/iv_rival", None),
    ("get", "/api/recruiter/interviews/iv_rival/questions", None),
    ("get", "/api/recruiter/interviews/iv_rival/draft", None),
    ("get", "/api/recruiter/interviews/iv_rival/versions", None),
    ("get", "/api/recruiter/interviews/iv_rival/invitations", None),
    ("get", "/api/recruiter/interviews/iv_rival/results", None),
    ("get", "/api/recruiter/interviews/iv_rival/publish/check", None),
    ("patch", "/api/recruiter/interviews/iv_rival", {"title": "Taken over"}),
    ("post", "/api/recruiter/interviews/iv_rival/publish", {}),
    ("post", "/api/recruiter/interviews/iv_rival/invitations", {"candidates": ["Mallory"]}),
    ("post", "/api/recruiter/interviews/iv_rival/questions/generate", {}),
    ("post", "/api/recruiter/interviews/iv_rival/extract", {}),
    ("delete", "/api/recruiter/interviews/iv_rival", None),
])
def test_another_organizations_interview_is_not_found(world, method, path, body):
    """Read, write and delete alike. And 404 rather than 403: the caller learns
    nothing about whether `iv_rival` exists."""
    call = getattr(world.rival_client, "get")  # sanity: it exists for its owner
    assert call("/api/recruiter/interviews/iv_rival").status_code == 200

    send = getattr(world.client, method)
    response = send(path) if body is None else send(path, json=body)
    assert response.status_code == 404, f"{method} {path} → {response.status_code}"
    assert response.json()["detail"] == "Not found."


def test_a_cross_tenant_write_changes_nothing(world):
    before = interviews.get("iv_rival").title
    world.client.patch("/api/recruiter/interviews/iv_rival", json={"title": "Taken over"})
    assert interviews.get("iv_rival").title == before


def test_a_cross_tenant_delete_deletes_nothing(world):
    assert world.client.delete("/api/recruiter/interviews/iv_rival").status_code == 404
    assert interviews.get("iv_rival") is not None


def test_the_interview_list_shows_only_one_organization(world):
    mine = world.client.get("/api/recruiter/interviews").json()
    theirs = world.rival_client.get("/api/recruiter/interviews").json()
    assert [c["id"] for c in mine["interviews"]] == ["iv_acme"]
    assert [c["id"] for c in theirs["interviews"]] == ["iv_rival"]
    assert mine["total"] == theirs["total"] == 1


def test_the_candidate_list_shows_only_one_organizations_candidates(world):
    _publish(world.client, "iv_acme")
    _publish(world.rival_client, "iv_rival")
    _invite(world.client, "iv_acme", "Acme Applicant")
    _invite(world.rival_client, "iv_rival", "Rival Applicant")

    mine = world.client.get("/api/recruiter/candidates").json()["candidates"]
    theirs = world.rival_client.get("/api/recruiter/candidates").json()["candidates"]
    assert [c["candidate_name"] for c in mine] == ["Acme Applicant"]
    assert [c["candidate_name"] for c in theirs] == ["Rival Applicant"]


def test_the_overview_counts_only_one_organization(world):
    _publish(world.client, "iv_acme")
    _publish(world.rival_client, "iv_rival")
    _invite(world.rival_client, "iv_rival", "Rival Applicant")
    overview = world.client.get("/api/recruiter/overview").json()
    assert json.dumps(overview).count("Rival") == 0
    assert overview["interviews"] == 1
    assert overview["candidates"] == 0


def test_another_organizations_session_report_and_trail_are_not_found(world):
    _publish(world.rival_client, "iv_rival")
    token = _invite(world.rival_client, "iv_rival", "Their Candidate")["token"]
    session_id = _sit(world.rival_client, token)

    for path in (
        f"/api/recruiter/sessions/{session_id}",
        f"/api/recruiter/sessions/{session_id}/trail",
        f"/api/recruiter/sessions/{session_id}/score",
        f"/api/recruiter/sessions/{session_id}/evaluation",
        f"/api/recruiter/sessions/{session_id}/evaluation/result",
        f"/api/recruiter/sessions/{session_id}/evaluation/evidence",
        f"/api/recruiter/sessions/{session_id}/evaluations",
        f"/api/recruiter/sessions/{session_id}/review",
    ):
        response = world.client.get(path)
        assert response.status_code == 404, path
        assert response.json()["detail"] == "Not found."
    # And the owner can read it, so the refusal is about tenancy and not about
    # the session being unreadable.
    assert world.rival_client.get(
        f"/api/recruiter/sessions/{session_id}").status_code == 200


def test_another_organizations_evaluation_cannot_be_read_by_id(world):
    _publish(world.rival_client, "iv_rival")
    token = _invite(world.rival_client, "iv_rival")["token"]
    session_id = _sit(world.rival_client, token)
    created = world.rival_client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    assert created.status_code == 200
    evaluation_id = created.json()["evaluation_id"]

    for path in (
        f"/api/recruiter/evaluations/{evaluation_id}",
        f"/api/recruiter/evaluations/{evaluation_id}/result",
    ):
        response = world.client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"] == "Not found."


def test_a_cross_tenant_evaluation_cannot_be_triggered(world):
    _publish(world.rival_client, "iv_rival")
    token = _invite(world.rival_client, "iv_rival")["token"]
    session_id = _sit(world.rival_client, token)
    before = [r.evaluation_id for r in evaluations.list_for_session(session_id)]
    response = world.client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    assert response.status_code == 404
    # The session already has the record its own completion asked for; the
    # refusal must not have added a second one on someone else's behalf.
    assert [r.evaluation_id for r in evaluations.list_for_session(session_id)] == before


def test_a_cross_tenant_review_cannot_be_recorded(world):
    _publish(world.rival_client, "iv_rival")
    token = _invite(world.rival_client, "iv_rival")["token"]
    session_id = _sit(world.rival_client, token)
    world.rival_client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})

    refused = world.client.post(f"/api/recruiter/sessions/{session_id}/review", json={
        "reviewer": "mallory", "verdict": "disagree", "reasons": ["wrong_score"]})
    assert refused.status_code == 404
    from services.data import pilot

    assert pilot.list_reviews() == []


def test_a_cross_tenant_comparison_is_refused_whole(world):
    _publish(world.rival_client, "iv_rival")
    theirs = _sit(world.rival_client, _invite(world.rival_client, "iv_rival")["token"])
    _publish(world.client, "iv_acme")
    mine = _sit(world.client, _invite(world.client, "iv_acme")["token"])

    refused = world.client.post("/api/recruiter/compare", json={
        "session_ids": [mine, theirs]})
    assert refused.status_code == 404


def test_an_invitation_token_from_another_organization_cannot_be_revoked(world):
    _publish(world.rival_client, "iv_rival")
    token = _invite(world.rival_client, "iv_rival")["token"]
    refused = world.client.delete(
        f"/api/recruiter/interviews/iv_rival/invitations/{token}")
    assert refused.status_code == 404
    assert invites.get(token).effective_status != "revoked"


def test_pilot_reporting_is_organization_scoped(world):
    _publish(world.rival_client, "iv_rival")
    theirs = _sit(world.rival_client, _invite(world.rival_client, "iv_rival")["token"])
    world.rival_client.post(f"/api/recruiter/sessions/{theirs}/evaluation", json={})

    mine = world.client.get("/api/recruiter/pilot/summary").json()
    assert mine["candidate_experience"]["attempted"] == 0
    assert mine["evaluation_quality"]["requested"] == 0
    assert world.client.get("/api/recruiter/pilot/dataset").json()["rows"] == []

    theirs_summary = world.rival_client.get("/api/recruiter/pilot/summary").json()
    assert theirs_summary["candidate_experience"]["attempted"] == 1


# =========================================================================== #
#  Candidate isolation
# =========================================================================== #
def test_a_candidate_needs_no_account_and_their_link_works(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]

    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as candidate:      # no login anywhere in this block
        assert candidate.get(f"/api/invite/{token}").status_code == 200
        started = candidate.post("/api/session/start", json={
            "token": token, "consent_recording": True, "channel": "text"})
        assert started.status_code == 200
        session_id = started.json()["session_id"]
        # The grant came back as a cookie, so the turn needs nothing else.
        assert candidate.cookies.get(security.CANDIDATE_COOKIE)
        turn = candidate.post(f"/api/session/{session_id}/turn", json={"said": ANSWER})
        assert turn.status_code == 200
        assert candidate.get(f"/api/session/{session_id}").status_code == 200


@pytest.mark.parametrize("token", [
    "", "x", "not-a-token", "a" * 43, "..%2F..%2Fetc%2Fpasswd", "%00",
    "iv_acme", "org_acme", "*", "null",
])
def test_a_malformed_or_random_invitation_token_is_refused(world, token):
    response = world.anonymous.get(f"/api/invite/{token}")
    assert response.status_code in (404, 410, 422)
    started = world.anonymous.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text"})
    assert started.status_code in (404, 410, 422)


def test_production_refuses_to_boot_carrying_the_demo_invitation(world):
    """The dev seed mints a never-expiring invitation on the token "demo".

    It is genuinely useful locally and it is a well-known credential for a real
    interview, so: production does not create one, and if a data directory is
    promoted with one in it, the production check says so by name.
    """
    from services import config
    from services.data import invites as invite_store

    assert invite_store.get(config.DEMO_TOKEN) is not None, "the dev seed still runs"

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(config, "ENVIRONMENT", "production")
        monkeypatch.setattr(config, "COOKIES_SECURE", True)
        monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://hire.example.com"])
        def demo_problems() -> list[str]:
            # This test's subject is the demo invitation, not the whole
            # production contract — `tests/test_deployment.py` covers the other
            # checks one at a time. Asserting the full list were empty would
            # make this fail every time a new check is added, for a reason that
            # has nothing to do with the demo token.
            return [p for p in config.require_production_configuration()
                    if config.DEMO_TOKEN in p]

        assert demo_problems(), config.require_production_configuration()
        invite_store.revoke(config.DEMO_TOKEN)
        assert demo_problems() == []
    finally:
        monkeypatch.undo()


def test_an_expired_invitation_cannot_start_or_resume(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    invite = invites.get(token)
    invite.expires_at = time.time() - 60
    invites.update(invite)
    assert world.anonymous.get(f"/api/invite/{token}").status_code == 410
    assert world.anonymous.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text"}).status_code == 410


def test_a_revoked_invitation_cannot_start_a_session(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    invites.revoke(token)
    assert world.anonymous.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text"}).status_code == 410


def test_a_completed_invitation_cannot_be_sat_again(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    _sit(world.client, token)
    assert world.anonymous.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text"}).status_code == 410


def test_candidate_a_cannot_reach_candidate_bs_session(world):
    """The core candidate-isolation property, over every route that takes a
    session id — including the socket."""
    from fastapi.testclient import TestClient

    from services.api.app import app

    _publish(world.client, "iv_acme")
    token_a = _invite(world.client, "iv_acme", "Candidate A")["token"]
    token_b = _invite(world.client, "iv_acme", "Candidate B")["token"]

    with TestClient(app) as a, TestClient(app) as b:
        a.post("/api/session/start", json={
            "token": token_a, "consent_recording": True, "channel": "text"})
        session_b = b.post("/api/session/start", json={
            "token": token_b, "consent_recording": True, "channel": "text"}
        ).json()["session_id"]

        # A holds their own grant, and knows B's session id.
        assert a.get(f"/api/session/{session_b}").status_code == 404
        assert a.post(f"/api/session/{session_b}/turn",
                      json={"said": "let me in"}).status_code == 404
        # A's own invitation token does not unlock B's session either.
        assert a.get(f"/api/session/{session_b}",
                     params={"token": token_a}).status_code == 404
        assert a.post(f"/api/session/{session_b}/turn", params={"token": token_a},
                      json={"said": "let me in"}).status_code == 404
        with a.websocket_connect(f"/ws/interview/{session_b}") as ws:
            assert ws.receive_json()["type"] == "error"

    # And B's transcript is untouched by all of that.
    state = store.try_load(session_b)
    assert [u.text for u in state.transcript if u.speaker == "candidate"] == []


def test_a_session_with_no_credential_at_all_is_not_found(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    session_id = _sit(world.client, token)
    # A stranger who somehow learned the session id.
    assert world.anonymous.get(f"/api/session/{session_id}").status_code == 404
    assert world.anonymous.post(
        f"/api/session/{session_id}/turn", json={"said": "hello"}).status_code == 404
    with world.anonymous.websocket_connect(f"/ws/interview/{session_id}") as ws:
        assert ws.receive_json()["type"] == "error"


def test_a_candidate_grant_is_bound_to_one_session(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    _publish(world.client, "iv_acme")
    token_a = _invite(world.client, "iv_acme", "A")["token"]
    token_b = _invite(world.client, "iv_acme", "B")["token"]
    with TestClient(app) as a, TestClient(app) as b:
        session_a = a.post("/api/session/start", json={
            "token": token_a, "consent_recording": True, "channel": "text"}
        ).json()["session_id"]
        session_b = b.post("/api/session/start", json={
            "token": token_b, "consent_recording": True, "channel": "text"}
        ).json()["session_id"]
        grant_a = a.cookies.get(security.CANDIDATE_COOKIE)

        # B's browser, holding A's grant, still cannot read B's… or A's.
        b.cookies.set(security.CANDIDATE_COOKIE, grant_a)
        assert b.get(f"/api/session/{session_b}").status_code == 404
        assert b.get(f"/api/session/{session_a}").status_code == 200  # it IS A's grant
        assert store.try_load(session_a).session_grant == grant_a


def test_a_candidate_cannot_read_their_own_evaluation_or_report(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    session_id = _sit(world.client, token)
    world.client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})

    with TestClient(app) as candidate:
        candidate.post("/api/session/start", json={
            "token": token, "consent_recording": True, "channel": "text"})
        for path in (
            f"/api/recruiter/sessions/{session_id}/evaluation",
            f"/api/recruiter/sessions/{session_id}/evaluation/result",
            f"/api/recruiter/sessions/{session_id}/score",
            f"/api/recruiter/sessions/{session_id}/trail",
        ):
            refused = candidate.get(path, params={"token": token},
                                    headers={"x-candidate-token": token})
            assert refused.status_code == 401, path
        # Their own session payload carries no verdict of any kind.
        payload = candidate.get(f"/api/session/{session_id}", params={"token": token}).text
        for word in ("total_score", "recommendation", "overall_rating", "criteria"):
            assert word not in payload.lower()


def test_the_candidate_api_never_reveals_another_candidates_identity(world):
    _publish(world.client, "iv_acme")
    a = _invite(world.client, "iv_acme", "Alice Applicant")
    _invite(world.client, "iv_acme", "Bob Applicant")
    payload = world.anonymous.get(f"/api/invite/{a['token']}").text
    assert "Bob" not in payload
    for word in ("organization_id", "org_acme", "user_id", "evaluation_id"):
        assert word not in payload


# =========================================================================== #
#  Privilege escalation
# =========================================================================== #
def test_a_viewer_may_read_and_may_not_write(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as viewer:
        viewer.post("/api/auth/login", json={
            "email": world.viewer.email, "password": TEST_PASSWORD})
        assert viewer.get("/api/recruiter/interviews").status_code == 200
        for method, path, body in (
            ("patch", "/api/recruiter/interviews/iv_acme", {"title": "Nope"}),
            ("post", "/api/recruiter/interviews/iv_acme/publish", {}),
            ("post", "/api/recruiter/interviews", {"title": "Mine", "role": "x"}),
        ):
            response = getattr(viewer, method)(path, json=body)
            assert response.status_code == 403, path
            assert "role" in response.json()["detail"].lower()
        assert interviews.get("iv_acme").title == "Acme Backend"


def test_a_recruiter_may_publish_and_may_not_delete(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as recruiter:
        recruiter.post("/api/auth/login", json={
            "email": world.recruiter.email, "password": TEST_PASSWORD})
        assert recruiter.post(
            "/api/recruiter/interviews/iv_acme/questions/generate", json={}
        ).status_code == 200
        assert recruiter.post(
            "/api/recruiter/interviews/iv_acme/publish", json={}).status_code == 200
        refused = recruiter.delete("/api/recruiter/interviews/iv_acme")
        assert refused.status_code == 403
        assert interviews.get("iv_acme") is not None
    # An admin in the same organization can.
    assert world.client.delete("/api/recruiter/interviews/iv_acme").status_code in (200, 409)


def test_a_candidate_credential_is_not_a_recruiter_credential(world):
    """The invitation token and the session grant open the candidate API only."""
    from fastapi.testclient import TestClient

    from services.api.app import app

    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    with TestClient(app) as candidate:
        session_id = candidate.post("/api/session/start", json={
            "token": token, "consent_recording": True, "channel": "text"}
        ).json()["session_id"]
        grant = candidate.cookies.get(security.CANDIDATE_COOKIE)
        # Presented every way a client could present it.
        for kwargs in (
            {"headers": {"Authorization": f"Bearer {token}"}},
            {"headers": {"Authorization": f"Bearer {grant}"}},
            {"headers": {"x-candidate-token": token}},
            {"params": {"token": token}},
        ):
            assert candidate.get("/api/recruiter/interviews", **kwargs).status_code == 401
        assert candidate.get(f"/api/session/{session_id}").status_code == 200


def test_a_session_cookie_from_another_organization_stays_in_its_own_organization(world):
    """Not privilege escalation but its neighbour: a valid session is valid only
    for the organization it belongs to."""
    assert world.rival_client.get("/api/recruiter/interviews/iv_acme").status_code == 404
    assert world.client.get("/api/recruiter/interviews/iv_rival").status_code == 404


# =========================================================================== #
#  Enumeration
# =========================================================================== #
def test_a_foreign_resource_and_a_nonexistent_one_answer_identically(world):
    """The whole point of choosing 404 over 403 for tenancy."""
    foreign = world.client.get("/api/recruiter/interviews/iv_rival")
    missing = world.client.get("/api/recruiter/interviews/iv_does_not_exist")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()

    _publish(world.rival_client, "iv_rival")
    token = _invite(world.rival_client, "iv_rival")["token"]
    theirs = _sit(world.rival_client, token)
    foreign_session = world.client.get(f"/api/recruiter/sessions/{theirs}/evaluation")
    missing_session = world.client.get("/api/recruiter/sessions/deadbeef/evaluation")
    assert foreign_session.status_code == missing_session.status_code == 404
    assert foreign_session.json() == missing_session.json()


@pytest.mark.parametrize("candidate_id", [
    "iv_1", "iv_2", "iv_0001", "1", "iv_acme_2", "IV_ACME", "iv_acme ",
])
def test_guessing_around_a_real_id_finds_nothing(world, candidate_id):
    response = world.client.get(f"/api/recruiter/interviews/{candidate_id}")
    assert response.status_code == 404


def test_ids_are_not_sequential_and_not_derived_from_their_contents(world):
    """Two invitations for the same candidate share no structure, and neither
    token encodes the interview, the candidate or a counter."""
    _publish(world.client, "iv_acme")
    first = _invite(world.client, "iv_acme", "Priya Sharma")["token"]
    second = _invite(world.client, "iv_acme", "Priya Sharma")["token"]
    assert first != second
    assert len(first) >= 40 and len(second) >= 40
    for token in (first, second):
        assert "iv_acme" not in token
        assert "priya" not in token.lower()
    # Sessions and evaluations likewise.
    session_id = _sit(world.client, first)
    assert "iv_acme" not in session_id and len(session_id) >= 32
    created = world.client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={}).json()
    assert created["evaluation_id"].startswith("ev_")
    assert session_id not in created["evaluation_id"]


def test_error_bodies_carry_no_implementation_detail(world):
    """No stack traces, no file paths, no store internals in a refusal."""
    responses = [
        world.anonymous.get("/api/recruiter/interviews"),
        world.client.get("/api/recruiter/interviews/iv_rival"),
        world.anonymous.get("/api/invite/nonsense"),
        world.anonymous.post("/api/auth/login", json={"email": "a@b.c", "password": "x"}),
    ]
    for response in responses:
        body = response.text.lower()
        for leak in ("traceback", "/users/", "site-packages", ".py\"", "sqlite",
                     "json.decoder", "keyerror", "scrypt", "data/"):
            assert leak not in body, (response.url, leak)


# =========================================================================== #
#  Secrets
# =========================================================================== #
def test_no_response_carries_a_password_hash_or_a_session_token(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    session_id = _sit(world.client, token)
    world.client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})

    session_cookie = world.client.cookies.get(security.SESSION_COOKIE)
    hashes = [u.password_hash for u in accounts.list_users()]
    for path in (
        "/api/auth/me",
        "/api/recruiter/interviews",
        "/api/recruiter/candidates",
        "/api/recruiter/overview",
        f"/api/recruiter/sessions/{session_id}",
        f"/api/recruiter/sessions/{session_id}/evaluation/result",
        "/api/recruiter/pilot/summary",
    ):
        body = world.client.get(path).text
        assert "scrypt$" not in body
        assert "password" not in body.lower()
        assert session_cookie not in body
        for password_hash in hashes:
            assert password_hash not in body


def test_the_provider_key_never_reaches_a_client(world):
    from services import config

    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    bodies = [
        world.client.get("/api/health").text,
        world.client.get("/api/recruiter/interviews").text,
        world.anonymous.get(f"/api/invite/{token}").text,
    ]
    for body in bodies:
        if config.OPENROUTER_API_KEY:
            assert config.OPENROUTER_API_KEY not in body
        assert "OPENROUTER_API_KEY" not in body
        assert "Authorization" not in body


def test_no_credential_reaches_the_audit_trail(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    session_id = _sit(world.client, token)
    world.client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    world.anonymous.post("/api/auth/login", json={
        "email": world.admin.email, "password": "the-wrong-password"})

    blob = "\n".join(
        json.dumps(row) for row in audit.read_product(limit=10_000) + audit.read(session_id)
    )
    assert token not in blob
    assert "the-wrong-password" not in blob
    assert TEST_PASSWORD not in blob
    assert "scrypt$" not in blob
    assert world.client.cookies.get(security.SESSION_COOKIE) not in blob
    assert store.try_load(session_id).session_grant not in blob


def test_a_failed_login_is_audited_without_the_address_in_full(world):
    world.anonymous.post("/api/auth/login", json={
        "email": "someone@example.test", "password": "wrong"})
    row = next(r for r in reversed(audit.read_product(limit=1000))
               if r["event"] == audit.LOGIN_FAILED)
    assert row["actor"] == "anonymous"
    assert row["subject_id"] == "so…@example.test"
    assert "wrong" not in json.dumps(row)


# =========================================================================== #
#  Audit — actor and organization on every recruiter line
# =========================================================================== #
def test_recruiter_actions_are_attributed_to_the_user_and_the_organization(world):
    world.client.post("/api/recruiter/interviews", json={
        "title": "Audited interview", "role": "senior_backend_engineer"})
    rows = [r for r in audit.read_product(limit=1000)
            if r["event"] == audit.INTERVIEW_CREATED]
    assert rows, "no audit line for the creation"
    row = rows[-1]
    assert row["actor"] == world.admin.user_id
    assert row["actor_type"] == "user"
    assert row["org_id"] == world.acme.organization_id


def test_a_candidate_action_is_attributed_to_the_candidate_not_a_user(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme")["token"]
    session_id = _sit(world.client, token)
    rows = [r for r in audit.read(session_id) if r.get("event") == "session_started"]
    assert rows
    created = [r for r in audit.read_product(limit=1000)
               if r["event"] == audit.SESSION_CREATED]
    assert created and created[-1]["actor"] == "candidate"
    assert created[-1]["actor_type"] == "candidate"


def test_login_and_logout_are_audited(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={
            "email": world.admin.email, "password": TEST_PASSWORD})
        c.post("/api/auth/logout")
    events = [r["event"] for r in audit.read_product(limit=1000)]
    assert audit.LOGIN_SUCCEEDED in events
    assert audit.LOGOUT in events


# =========================================================================== #
#  Rate limiting
# =========================================================================== #
def test_repeated_failed_logins_are_rate_limited(world):
    ratelimit.reset()
    codes = []
    for _ in range(int(ratelimit.LIMITS["login"].requests) + 4):
        codes.append(world.anonymous.post("/api/auth/login", json={
            "email": world.admin.email, "password": "wrong"}).status_code)
    assert 429 in codes
    assert codes.count(401) <= ratelimit.LIMITS["login"].requests
    # And the limit is not a lockout: a different client is unaffected, and the
    # window is short enough to be a speed bump rather than a denial of service.
    assert ratelimit.LIMITS["login"].window_sec <= 300


def test_invitation_lookups_are_rate_limited(world):
    ratelimit.reset()
    codes = [
        world.anonymous.get(f"/api/invite/guess-{n}").status_code
        for n in range(ratelimit.LIMITS["invitation"].requests + 3)
    ]
    assert 429 in codes


def test_the_limiter_fails_open_rather_than_breaking_an_interview(world):
    """A bug in the limiter must not end a candidate's interview."""
    assert ratelimit.check("no-such-limit", "anyone") is True


# =========================================================================== #
#  Ids that arrive somewhere other than the path
#
#  The router's ownership guard walks `request.path_params`. A resource named in
#  a query string or a request body is invisible to it, so each of these
#  handlers has to ask for itself — and each one is a place where forgetting
#  looks exactly like working.
# =========================================================================== #
def test_a_query_parameter_cannot_name_another_organizations_interview(world):
    _publish(world.rival_client, "iv_rival")
    _sit(world.rival_client, _invite(world.rival_client, "iv_rival", "Their Candidate")["token"])

    refused = world.client.get("/api/recruiter/fairness",
                               params={"interview_id": "iv_rival"})
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Not found."

    # The unscoped aggregate covers this organization's sessions only, so the
    # rival's completed interview is not in it.
    mine = world.client.get("/api/recruiter/fairness")
    assert mine.status_code == 200
    assert mine.json()["sessions_audited"] == 0

    # The positive control — the owner reading their own fairness view — is not
    # asserted here. `analytics` scores against the authored role pool rather
    # than the session's pinned definition, so that call raises KeyError for any
    # LLM-generated interview. A pre-existing defect in the legacy scoring path,
    # unrelated to tenancy and out of this phase's scope; recorded rather than
    # papered over with a test that tolerates a 500.


def test_a_request_body_cannot_name_another_organizations_interview(world):
    _publish(world.rival_client, "iv_rival")
    before = len(invites.list_all())

    refused = world.client.post("/api/recruiter/candidates", json={
        "interview_id": "iv_rival", "candidate_name": "Planted", "ttl_days": 7})
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Not found."
    # No invitation was minted into their interview.
    assert len(invites.list_all()) == before
    assert not any(r["candidate_name"] == "Planted" for r in invites.list_all())


def test_body_named_sessions_are_all_checked_not_just_the_first(world):
    _publish(world.client, "iv_acme")
    _publish(world.rival_client, "iv_rival")
    mine_a = _sit(world.client, _invite(world.client, "iv_acme", "A")["token"])
    mine_b = _sit(world.client, _invite(world.client, "iv_acme", "B")["token"])
    theirs = _sit(world.rival_client, _invite(world.rival_client, "iv_rival")["token"])

    # In any position, and among any number of the caller's own, one foreign id
    # refuses the whole comparison.
    for ids in ([theirs, mine_a], [mine_a, theirs], [mine_a, mine_b, theirs]):
        response = world.client.post("/api/recruiter/compare", json={"session_ids": ids})
        assert response.status_code == 404, ids
        assert response.json()["detail"] == "Not found."
    # The positive side, checked at the layer the handler calls: both of the
    # caller's own sessions resolve rather than raising. Not driven through the
    # endpoint because rendering a comparison then hits the legacy-pool defect
    # noted above, which would make this a test of that bug instead.
    from services.security.principal import AuthenticatedPrincipal

    who = AuthenticatedPrincipal(
        user_id=world.admin.user_id, organization_id=world.acme.organization_id,
        role=world.admin.role, email=world.admin.email,
    )
    for session_id in (mine_a, mine_b):
        assert authz.session(who, session_id).session_id == session_id
    with pytest.raises(Exception):
        authz.session(who, theirs)


def test_another_organizations_pilot_run_cannot_be_listed_or_stopped(world):
    from services.data import pilot as pilot_store

    theirs = world.rival_client.post("/api/recruiter/pilot/runs", json={
        "label": "Rival Q3 pilot", "notes": "hiring for the payments team"}).json()
    mine = world.client.post("/api/recruiter/pilot/runs", json={
        "label": "Acme Q3 pilot"}).json()

    # Opening a run must not close the other organization's.
    assert pilot_store.get_run(theirs["pilot_run_id"]).active

    listed = world.client.get("/api/recruiter/pilot/runs").json()
    assert [r["label"] for r in listed["runs"]] == ["Acme Q3 pilot"]
    assert listed["active"]["pilot_run_id"] == mine["pilot_run_id"]
    assert "payments team" not in json.dumps(listed)

    refused = world.client.post(
        f"/api/recruiter/pilot/runs/{theirs['pilot_run_id']}/stop")
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Not found."
    assert pilot_store.get_run(theirs["pilot_run_id"]).active

    # A report defaulting to "the open run" names this organization's.
    assert world.client.get("/api/recruiter/pilot/summary").json()[
        "pilot_run_id"] == mine["pilot_run_id"]
    # And naming theirs explicitly describes nothing.
    scoped = world.client.get("/api/recruiter/pilot/summary",
                              params={"pilot_run_id": theirs["pilot_run_id"]}).json()
    assert scoped["pilot_run"] is None
    assert scoped["candidate_experience"]["attempted"] == 0


# =========================================================================== #
#  The access matrix
#
#  These are the tests that keep the matrix honest as routes are added. A new
#  endpoint fails them until it is classified, which is the only mechanism that
#  scales past the number of endpoints one person can hold in their head.
# =========================================================================== #
def test_every_route_is_classified_and_matches_what_the_code_enforces():
    from services.api.app import app
    from services.security import matrix

    discrepancies = matrix.audit(app)
    assert discrepancies == [], "\n".join(str(d) for d in discrepancies)


def test_the_matrix_covers_the_whole_surface():
    from services.api.app import app
    from services.security import matrix

    live = matrix.routes(app)
    assert len(live) > 60, "the walker stopped seeing most of the API"
    assert {matrix.derive(r) for r in live} <= set(matrix.CLASSES)
    # Every class is actually in use — an unused class is a class nobody checks.
    declared = set(matrix.DECLARED.values())
    assert declared == set(matrix.CLASSES)


def test_no_recruiter_route_is_reachable_without_the_guard():
    from services.api.app import app
    from services.security import matrix

    unguarded = [
        str(r) for r in matrix.routes(app, include_aliases=True)
        if r.path.startswith(("/api/recruiter", "/api/admin"))
        and "recruiter_scope" not in r.dependencies
    ]
    assert unguarded == [], unguarded


def test_the_admin_alias_is_protected_exactly_like_the_recruiter_namespace():
    from services.api.app import app
    from services.security import matrix

    by_path = {(r.method, r.path): r for r in matrix.routes(app, include_aliases=True)}
    pairs = 0
    for (method, path), route in by_path.items():
        if not path.startswith("/api/recruiter"):
            continue
        alias = by_path.get((method, path.replace("/api/recruiter", "/api/admin", 1)))
        assert alias is not None, f"{method} {path} has no /api/admin alias"
        assert alias.dependencies == route.dependencies, f"{method} {path}"
        pairs += 1
    assert pairs > 50


def test_the_public_surface_is_short_and_deliberate():
    """Anything reachable with no credential at all, listed by name.

    A route that becomes public by accident is the single worst outcome of this
    phase, so the public set is asserted exactly rather than bounded.
    """
    from services.api.app import app
    from services.security import matrix

    public = {
        (r.method, r.path) for r in matrix.routes(app)
        if matrix.DECLARED.get(r.key) == matrix.PUBLIC
    }
    assert public == {
        ("GET", "/api/health"),
        ("GET", "/api/ready"),
        ("GET", "/api/demo/prompts"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
    }


def test_the_demo_answer_key_is_not_served_in_production(world):
    """`/api/demo/prompts` is presenter copy — and it is keyed by the authored
    item ids of the default interview, which makes it an answer key for anyone
    sitting that interview. Local only."""
    from services import config

    local = world.anonymous.get("/api/demo/prompts").json()
    assert local["answers"], "the demo overlay still has its lines locally"

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(config, "ENVIRONMENT", "production")
        in_production = world.anonymous.get("/api/demo/prompts").json()
        assert in_production == {"answers": {}, "probe_replies": []}
    finally:
        monkeypatch.undo()


def test_the_public_health_check_says_nothing_useful_to_an_attacker(world):
    body = world.anonymous.get("/api/health")
    assert body.status_code == 200
    payload = body.text.lower()
    for leak in ("key", "secret", "token", "password", "org_", "usr_",
                 "/users/", "data_dir"):
        assert leak not in payload, leak


# =========================================================================== #
#  The secrets audit, as a test
#
#  `python -m tools.secrets_audit` is the thing a person runs before deploying.
#  Running its checks here as well means a change that starts inlining a key
#  into a bundle, or logging a token, fails the suite rather than waiting for
#  somebody to remember the tool exists.
# =========================================================================== #
def test_the_secrets_audit_finds_nothing_in_the_built_bundles_or_the_trail(world):
    from tools import secrets_audit

    findings = secrets_audit.audit()
    assert findings == [], "\n".join(findings)


def test_the_audit_diagnostic_never_prints_a_credential(capsys):
    from services import config
    from tools import secrets_audit

    secrets_audit.main()
    printed = capsys.readouterr().out
    for secret in (config.OPENROUTER_API_KEY, config.RETELL_API_KEY):
        if secret:
            assert secret not in printed
            # Not even a prefix: a masked value must reveal nothing usable.
            assert secret[:8] not in printed


def test_no_frontend_source_file_reads_a_secret_through_the_build(world):
    """`import.meta.env.VITE_*` is inlined into the bundle at build time.

    So it is not a place a credential can live, and the audit treats any use of
    one as a finding. This asserts the current state directly: none exist.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "apps"
    offenders = []
    for path in list(root.rglob("*.ts")) + list(root.rglob("*.tsx")):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        for name in set(re.findall(r"import\.meta\.env\.(VITE_\w+)",
                                   path.read_text(encoding="utf-8"))):
            offenders.append(f"{path.name}: {name}")
    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
#  The well-known demo invitation
# --------------------------------------------------------------------------- #
def test_the_demo_invitation_is_refused_on_a_public_host(data_dir, monkeypatch):
    """`demo` is a never-expiring credential anyone can guess.

    Gated on REACHABILITY, not on the environment label. A public deployment
    left in development is exactly the case that needs protecting, and an
    `is_production()` check misses it — which is how a live site ended up
    serving this to anyone holding the URL.
    """
    from fastapi.testclient import TestClient

    from services import config
    from services.api.app import app
    from services.data import invites

    monkeypatch.setattr(config, "PUBLIC_URL", "https://interviews.example.com")
    monkeypatch.setattr(config, "DEMO_INVITE_PUBLIC", False)

    with TestClient(app) as c:
        invites.ensure_demo_invite("iv_default", 1)  # it exists on disk
        response = c.get(f"/api/invite/{config.DEMO_TOKEN}")

    assert response.status_code == 404
    # Indistinguishable from a token that was never issued.
    assert "isn't valid" in response.text


def test_the_demo_invitation_still_works_for_local_development(data_dir, monkeypatch):
    """A fresh clone must not be a dead end — that is why it exists."""
    from fastapi.testclient import TestClient

    from services import config
    from services.api.app import app

    monkeypatch.setattr(config, "PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setattr(config, "DEMO_INVITE_PUBLIC", False)

    with TestClient(app) as c:
        response = c.get(f"/api/invite/{config.DEMO_TOKEN}")
    assert response.status_code == 200


def test_a_public_demo_deployment_can_opt_in(data_dir, monkeypatch):
    """Deliberate, and it has to be said out loud to happen."""
    from fastapi.testclient import TestClient

    from services import config
    from services.api.app import app

    monkeypatch.setattr(config, "PUBLIC_URL", "https://demo.example.com")
    monkeypatch.setattr(config, "DEMO_INVITE_PUBLIC", True)

    with TestClient(app) as c:
        response = c.get(f"/api/invite/{config.DEMO_TOKEN}")
    assert response.status_code == 200
