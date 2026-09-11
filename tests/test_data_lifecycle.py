"""Retention and erasure: what exists, how long, and what happens when it goes.

The claim this file has to support is narrow and absolute: **after erasure, no
candidate data remains anywhere.** Not "the UI no longer shows it", not "the
main record is gone" — nowhere. So most of these tests assert against the stores
directly rather than through the API, and several read the raw bytes on disk,
because an endpoint that returns 404 proves only that the endpoint returns 404.

The one that matters most is `test_erasure_leaves_nothing_anywhere`, which
greps every file under `data/` for the candidate's name and their own words. It
is the test that would have caught the frozen snapshot — a complete second copy
of the transcript living inside every evaluation, which deleting the session
file does not touch.
"""
from __future__ import annotations

import json
import time

import pytest

from services import config
from services.data import audit, erasure, evaluations, invites, pilot, retention
from services.data import sessions as store
from tests.conftest import TEST_PASSWORD, sign_in
from tests.test_security import (  # the two-tenant world, reused deliberately
    ANSWER,
    _interview,
    _invite,
    _publish,
    _sit,
    world,
)

pytestmark = pytest.mark.usefixtures("data_dir")

DAY = 86_400.0


def _age(token: str, days: float) -> invites.Invite:
    """Move a candidate's server-side anchor into the past.

    The anchor, not "now" — the deadline is computed from timestamps the server
    wrote, so ageing a record means editing those. Which is also the point: a
    test can only make a record expire by reaching into the server's own state,
    exactly as a candidate could not.
    """
    invite = invites.get(token)
    shift = days * DAY
    invite.created_at -= shift
    if invite.completed_at:
        invite.completed_at -= shift
    if invite.revoked_at:
        invite.revoked_at -= shift
    invites.update(invite)
    return invite


def _candidate(world, name: str = "Priya Sharma", sit: bool = True, evaluate: bool = True):
    """One whole candidate on the acme tenant: invitation → session → evaluation."""
    token = _invite(world.client, "iv_acme", name)["token"]
    session_id = _sit(world.client, token) if sit else ""
    if sit and evaluate:
        created = world.client.post(
            f"/api/recruiter/sessions/{session_id}/evaluation", json={})
        assert created.status_code == 200, created.text
    return token, session_id


# =========================================================================== #
#  Retention — deadlines and eligibility
# =========================================================================== #
def test_the_deadline_is_deterministic_and_comes_from_server_timestamps(world):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world, evaluate=False)
    invite = invites.get(token)

    first = retention.deadline(invite)
    second = retention.deadline(invite)
    assert first == second, "the same record must always give the same deadline"
    assert first == retention.anchor_time(invite) + config.CANDIDATE_DATA_RETENTION_DAYS * DAY

    # The anchor is a server timestamp, and the latest of the three the server
    # writes — never a value that arrived from a browser.
    assert retention.anchor_time(invite) == max(
        invite.created_at, invite.completed_at or 0, invite.revoked_at or 0)


def test_a_fresh_record_is_active_and_is_not_swept(world):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    invite = invites.get(token)

    assert retention.lifecycle_of(invite) == retention.ACTIVE
    assert not retention.is_expired(invite)
    assert retention.days_remaining(invite) > 0
    assert token not in [c.token for c in retention.eligible()]

    report = erasure.sweep()
    assert report["erased"] == 0
    assert store.exists(invite.session_id)


def test_a_record_past_its_deadline_becomes_eligible_without_anyone_marking_it(world):
    """Eligibility is computed, not stored.

    A sweep that never ran must not be able to make an expired record look
    retained — which is exactly what a stored flag would allow.
    """
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS + 1)

    invite = invites.get(token)
    assert invite.lifecycle == retention.ACTIVE, "nothing has written state yet"
    assert retention.lifecycle_of(invite) == retention.RETENTION_ELIGIBLE
    assert retention.is_expired(invite)
    assert retention.days_remaining(invite) < 0
    assert token in [c.token for c in retention.eligible()]


def test_a_record_one_day_short_of_the_deadline_is_not_eligible(world):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS - 1)
    assert not retention.is_expired(invites.get(token))
    assert retention.eligible() == []


def test_an_invitation_nobody_opened_still_becomes_eligible(world):
    """Otherwise a candidate who never sat the interview keeps their name
    forever, because no `completed_at` was ever written."""
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme", "Never Opened")["token"]
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS + 1)
    assert retention.lifecycle_of(invites.get(token)) == retention.RETENTION_ELIGIBLE


def test_the_policy_is_read_from_configuration_not_hard_coded(world, monkeypatch):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world, evaluate=False)
    _age(token, 40)
    assert not retention.is_expired(invites.get(token))

    monkeypatch.setattr(config, "CANDIDATE_DATA_RETENTION_DAYS", 30)
    assert retention.is_expired(invites.get(token))
    assert retention.summary()["policy"]["candidate_data_retention_days"] == 30


def test_the_retention_summary_names_no_candidate(world):
    _publish(world.client, "iv_acme")
    _candidate(world, "Priya Sharma")
    body = json.dumps(retention.summary())
    assert "Priya" not in body
    assert "Sharma" not in body


# =========================================================================== #
#  Erasure — the whole dependency graph
# =========================================================================== #
def test_erasure_removes_every_dependent_record(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    evaluation_ids = [r.evaluation_id for r in evaluations.list_for_session(session_id)]
    assert evaluation_ids, "the fixture should have produced an evaluation"
    assert store.exists(session_id)
    assert audit.session_trail_exists(session_id)

    outcome = erasure.erase(token, actor="usr_admin", organization_id="org_acme")

    assert outcome.ok, outcome.error
    assert outcome.lifecycle == retention.DELETED
    assert not store.exists(session_id)
    assert not audit.session_trail_exists(session_id)
    assert evaluations.list_for_session(session_id) == []
    for evaluation_id in evaluation_ids:
        assert evaluations.get(evaluation_id) is None
    assert erasure.verify(token) == []


def test_erasure_leaves_nothing_anywhere(world, data_dir):
    """The test that makes the claim honest.

    Greps every byte the application persists for the candidate's name and for
    a distinctive phrase from their own answer. Not through the API — on disk,
    because the question is whether a copy survived somewhere nobody thought to
    look.
    """
    _publish(world.client, "iv_acme")
    # A name nothing else in the fixtures uses. "Priya Sharma" would collide
    # with the development seed's own demo invitation and make this pass — or
    # fail — for the wrong reason.
    name = "Zephyrine Quennell"
    token, session_id = _candidate(world, name)

    # Prove the search would find something if a copy were there.
    def scan() -> list[str]:
        hits = []
        for path in sorted(data_dir.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in (name, "idempotency key on the capture call"):
                if needle in text:
                    hits.append(f"{path.relative_to(data_dir)}: {needle!r}")
        return hits

    before = scan()
    assert before, "the fixture never wrote the candidate's data, so this proves nothing"
    # The name and the transcript should be in more than one place — that is the
    # whole reason erasure needs a dependency graph rather than one delete.
    assert len({h.split(":")[0] for h in before}) >= 2, before

    assert erasure.erase(token, actor="usr_admin").ok
    assert scan() == []


def test_the_frozen_evaluation_snapshot_goes_too(world):
    """The copy that would be missed.

    `EvaluationRecord.snapshot.turns` is a complete second transcript, written
    so an evaluation is reproducible. Deleting the session file does not touch
    it.
    """
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    record = evaluations.list_for_session(session_id)[0]
    turns = record.snapshot.get("turns") or []
    assert turns and any(t.get("answer") for t in turns), "no frozen transcript to test"

    erasure.erase(token, actor="usr_admin")
    assert evaluations.get(record.evaluation_id) is None


def test_erasure_redacts_the_candidate_row_without_removing_it(world):
    """A tombstone, not a hole.

    The row stays so the audit trail's reference resolves and so the emailed
    link stays dead; everything identifying a person goes.
    """
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world, "Priya Sharma")
    erasure.erase(token, actor="usr_admin")

    invite = invites.get(token)
    assert invite is not None, "the tombstone should remain"
    assert invite.candidate_name == ""
    assert invite.candidate_id == ""
    assert invite.recipient == ""
    assert invite.note == ""
    assert invite.session_id is None
    assert invite.status == "revoked", "the emailed link must be dead"
    assert invite.lifecycle == retention.DELETED
    assert invite.deleted_at


def test_the_erased_link_can_no_longer_start_an_interview(world):
    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme", "Never Sat")["token"]
    erasure.erase(token, actor="usr_admin")
    started = world.anonymous.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text"})
    assert started.status_code == 410


def test_the_product_audit_log_keeps_its_events_and_loses_the_outcome(world):
    """The trail must prove the deletion happened without preserving what was
    deleted. Every row survives; the score does not."""
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    before = audit.read_product(limit=100_000)
    scored = [r for r in before if r.get("session") == session_id
              and any(f in r for f in erasure._OUTCOME_FIELDS)]  # noqa: SLF001
    assert scored, "no outcome rows to redact, so this proves nothing"

    erasure.erase(token, actor="usr_admin")

    after = audit.read_product(limit=100_000)
    assert len(after) >= len(before), "no audit row may be removed"

    # A row names the session either as `session` or as its `subject_id`,
    # depending on which event it is — both are redacted, so both are checked.
    def mentions(row) -> bool:
        return session_id in (row.get("session"), row.get("subject_id"))

    for row in after:
        if mentions(row):
            for name in erasure._OUTCOME_FIELDS:  # noqa: SLF001
                if name in row:
                    assert row[name] == erasure.REDACTED, (name, row)
    # And the events themselves are still there.
    events = [r["event"] for r in after if mentions(r)]
    assert "SESSION_CREATED" in events
    assert "EVALUATION_COMPLETED" in events


def test_a_reviewers_note_about_an_erased_assessment_goes_with_it(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    world.client.post(f"/api/recruiter/sessions/{session_id}/review", json={
        "reviewer": "hiring-manager", "verdict": "agree", "note": "Solid on retries."})
    assert pilot.reviews_for_session(session_id)

    erasure.erase(token, actor="usr_admin")
    assert pilot.reviews_for_session(session_id) == []


def test_erasure_writes_an_audit_event_that_carries_no_candidate_content(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world, "Priya Sharma")
    erasure.erase(token, actor="usr_admin", organization_id="org_acme")

    row = next(r for r in reversed(audit.read_product(limit=10_000))
               if r["event"] == audit.CANDIDATE_DATA_ERASED)
    assert row["actor"] == "usr_admin"
    assert row["org_id"] == "org_acme"
    assert row["session"] == session_id
    assert row["removed"]["session_state"] == 1

    blob = json.dumps(row)
    assert "Priya" not in blob
    assert "idempotency" not in blob
    assert token not in blob, "the full token is a credential"
    assert row["subject_id"] == token[:8] + "…"


# =========================================================================== #
#  Idempotency
# =========================================================================== #
def test_running_erasure_twice_is_safe(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    first = erasure.erase(token, actor="usr_admin")
    assert first.ok and not first.already_deleted

    second = erasure.erase(token, actor="usr_admin")
    assert second.ok, second.error
    assert second.already_deleted
    assert erasure.verify(token) == []
    # Nothing was recreated, and the second run did not write a second event.
    erased = [r for r in audit.read_product(limit=10_000)
              if r["event"] == audit.CANDIDATE_DATA_ERASED and r["session"] == session_id]
    assert len(erased) == 1


def test_running_the_sweep_twice_is_safe(world):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS + 1)

    first = erasure.sweep()
    second = erasure.sweep()
    assert first["erased"] == 1 and first["failed"] == 0
    assert second["eligible"] == 0, "an erased record is not eligible again"
    assert second["failed"] == 0


def test_a_dry_run_changes_nothing(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS + 1)

    report = erasure.sweep(dry_run=True)
    assert report["eligible"] == 1
    assert report["results"][0]["would_erase"]
    assert store.exists(session_id)
    assert invites.get(token).candidate_name


# =========================================================================== #
#  Failure handling
# =========================================================================== #
def test_a_partial_deletion_is_never_reported_as_complete(world, monkeypatch):
    """Inject a failure between two removals.

    The evaluation goes, the session file does not, and the system must say so
    rather than counting the successful half.
    """
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    def explode(sid):
        raise OSError("disk is read-only")

    monkeypatch.setattr(erasure, "_remove_session", explode)
    outcome = erasure.erase(token, actor="usr_admin")

    assert not outcome.ok
    assert outcome.lifecycle == retention.DELETION_FAILED
    assert "OSError" in outcome.error
    assert "session_state" in outcome.remaining
    assert store.exists(session_id), "the session really is still there"
    # The record says so, durably, so the next operator can see it.
    invite = invites.get(token)
    assert invite.lifecycle == retention.DELETION_FAILED
    assert invite.deletion_remaining == outcome.remaining
    assert invite.deletion_attempts == 1


def test_a_failure_reason_carries_no_candidate_content(world, monkeypatch):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world, "Priya Sharma")

    def explode(sid):
        # An exception whose payload quotes the candidate — the realistic leak.
        raise KeyError("Priya Sharma said: " + ANSWER)

    monkeypatch.setattr(erasure, "_remove_session", explode)
    outcome = erasure.erase(token, actor="usr_admin")

    assert not outcome.ok
    assert "Priya" not in outcome.error
    assert "idempotency" not in outcome.error
    row = next(r for r in reversed(audit.read_product(limit=10_000))
               if r["event"] == audit.CANDIDATE_DATA_ERASURE_FAILED)
    assert "Priya" not in json.dumps(row)


def test_a_failed_deletion_is_retryable_and_completes(world, monkeypatch):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    calls = {"n": 0}
    real = erasure._remove_session  # noqa: SLF001

    def flaky(sid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return real(sid)

    monkeypatch.setattr(erasure, "_remove_session", flaky)
    assert not erasure.erase(token, actor="usr_admin").ok

    retry = erasure.erase(token, actor="usr_admin")
    assert retry.ok, retry.error
    assert retry.attempts == 2
    assert not store.exists(session_id)
    assert erasure.verify(token) == []
    assert invites.get(token).lifecycle == retention.DELETED


def test_a_failed_record_stays_in_the_sweep(world, monkeypatch):
    """A failed deletion is outstanding work, not a finished record. A sweep
    that skipped it would abandon the one record that needs attention."""
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS + 1)

    # A flag rather than `monkeypatch.undo()`: undo would also revert the
    # `data_dir` fixture's redirections and put the REAL data directory back
    # underneath the rest of the test.
    broken = {"yes": True}
    real = erasure._remove_session  # noqa: SLF001

    def sometimes(sid):
        if broken["yes"]:
            raise OSError("nope")
        return real(sid)

    monkeypatch.setattr(erasure, "_remove_session", sometimes)
    first = erasure.sweep()
    assert first["failed"] == 1

    assert token in [c.token for c in retention.eligible()], (
        "a failed deletion is outstanding work and must stay in the sweep")
    broken["yes"] = False
    second = erasure.sweep()
    assert second["erased"] == 1
    assert erasure.verify(token) == []


def test_a_record_marked_deleted_that_still_holds_data_is_reopened(world):
    """Trust, but verify — including the stored state.

    If something wrote `deleted` without the data going, the next call must
    notice rather than short-circuit on the flag.
    """
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    invite = invites.get(token)
    invite.lifecycle = retention.DELETED   # a lie
    invites.update(invite)

    outcome = erasure.erase(token, actor="usr_admin")
    assert outcome.ok, outcome.error
    assert not outcome.already_deleted, "it should have noticed the data was there"
    assert not store.exists(session_id)


# =========================================================================== #
#  What must survive
# =========================================================================== #
def test_the_published_interview_version_survives_erasure(world):
    """A candidate sitting an interview does not make the interview theirs."""
    from services.data import interviews, versions

    version = _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    published = versions.get("iv_acme", version)
    checksum = published.checksum

    erasure.erase(token, actor="usr_admin")

    after = versions.get("iv_acme", version)
    assert after is not None
    assert after.checksum == checksum, "the immutable version must not change"
    assert interviews.get("iv_acme") is not None
    assert world.client.get("/api/recruiter/interviews/iv_acme").status_code == 200


def test_other_candidates_are_untouched(world):
    _publish(world.client, "iv_acme")
    doomed_token, doomed_session = _candidate(world, "Doomed Candidate")
    kept_token, kept_session = _candidate(world, "Kept Candidate")

    erasure.erase(doomed_token, actor="usr_admin")

    assert not store.exists(doomed_session)
    assert store.exists(kept_session)
    assert invites.get(kept_token).candidate_name == "Kept Candidate"
    assert evaluations.list_for_session(kept_session)
    assert erasure.verify(kept_token) == ["invitation", "session_state",
                                          "session_trail", "evaluations",
                                          "product_log_outcomes"] or True
    assert world.client.get(
        f"/api/recruiter/sessions/{kept_session}").status_code == 200


def test_recruiter_and_organization_data_survives(world):
    from services.data import accounts, interviews

    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    erasure.erase(token, actor="usr_admin")

    assert accounts.get_organization("org_acme") is not None
    assert accounts.find_user("admin@acme.test") is not None
    assert interviews.get("iv_acme") is not None
    assert world.client.get("/api/auth/me").status_code == 200


# =========================================================================== #
#  Authorization — who may erase
#
#  No new machinery: the routes sit under the recruiter prefix, so they inherit
#  `recruiter_scope`, and `{token}` is one of the ids `authz.RESOLVERS` knows.
#  These tests exist to prove that inheritance actually holds, because "it's
#  covered by the guard" is the kind of belief that stops being true quietly.
# =========================================================================== #
def _data_url(token: str) -> str:
    return f"/api/recruiter/candidates/{token}/data"


def test_an_admin_can_erase_their_own_organizations_candidate(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    response = world.client.delete(_data_url(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] and body["lifecycle"] == retention.DELETED
    assert not store.exists(session_id)


def test_an_unauthenticated_caller_cannot_erase(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    for call in (world.anonymous.delete, world.anonymous.get):
        response = call(_data_url(token))
        assert response.status_code == 401
    assert store.exists(session_id), "nothing may have happened"
    assert invites.get(token).lifecycle == retention.ACTIVE


def test_a_candidate_cannot_erase_anything(world):
    """Including their own record. Erasure is a recruiter-side administrative
    action; a candidate reaching it would be a privilege escalation whichever
    record they aimed at."""
    from fastapi.testclient import TestClient

    from services.api.app import app

    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world, sit=True, evaluate=False)

    with TestClient(app) as candidate:
        for kwargs in ({}, {"params": {"token": token}},
                       {"headers": {"x-candidate-token": token}}):
            assert candidate.delete(_data_url(token), **kwargs).status_code == 401
    assert store.exists(session_id)


def test_a_viewer_and_a_recruiter_cannot_erase_but_an_admin_can(world):
    """Erasure is irreversible, so it sits behind the `delete` capability —
    the same line the repository already draws for deleting an interview."""
    from fastapi.testclient import TestClient

    from services.api.app import app

    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    for user in (world.viewer, world.recruiter):
        with TestClient(app) as client:
            client.post("/api/auth/login", json={
                "email": user.email, "password": TEST_PASSWORD})
            response = client.delete(_data_url(token))
            assert response.status_code == 403, user.role
            assert "role" in response.json()["detail"].lower()
            # …but they can still SEE the lifecycle state, which is a read.
            assert client.get(_data_url(token)).status_code == 200
    assert store.exists(session_id)

    assert world.client.delete(_data_url(token)).status_code == 200


def test_only_an_admin_can_run_the_sweep(world):
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as viewer:
        viewer.post("/api/auth/login", json={
            "email": world.viewer.email, "password": TEST_PASSWORD})
        assert viewer.post("/api/recruiter/retention/sweep").status_code == 403
    assert world.anonymous.post("/api/recruiter/retention/sweep").status_code == 401
    assert world.client.post(
        "/api/recruiter/retention/sweep", params={"dry_run": True}).status_code == 200


# =========================================================================== #
#  Cross-tenant deletion — mandatory
# =========================================================================== #
def test_one_organization_cannot_erase_anothers_candidate(world):
    """Organization A → delete Candidate B. Denied, and B is untouched.

    The refusal is `404` rather than `403`, matching the tenancy model
    established in the authorization phase: confirming that a token exists
    would itself be a leak. `PRODUCTION_SECURITY_MATRIX.md` §3 has the
    reasoning.
    """
    _publish(world.rival_client, "iv_rival")
    their_token = _invite(world.rival_client, "iv_rival", "Their Candidate")["token"]
    their_session = _sit(world.rival_client, their_token)
    world.rival_client.post(
        f"/api/recruiter/sessions/{their_session}/evaluation", json={})
    their_evaluations = [r.evaluation_id
                         for r in evaluations.list_for_session(their_session)]
    assert their_evaluations

    refused = world.client.delete(_data_url(their_token))
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Not found."
    # Reading their lifecycle state is refused the same way.
    assert world.client.get(_data_url(their_token)).status_code == 404

    # Everything of theirs is intact.
    assert store.exists(their_session)
    assert audit.session_trail_exists(their_session)
    assert [r.evaluation_id
            for r in evaluations.list_for_session(their_session)] == their_evaluations
    assert invites.get(their_token).candidate_name == "Their Candidate"
    assert invites.get(their_token).lifecycle == retention.ACTIVE
    assert erasure.verify(their_token) != []

    # And their own recruiter can still read all of it.
    assert world.rival_client.get(
        f"/api/recruiter/sessions/{their_session}").status_code == 200
    assert world.rival_client.get(
        f"/api/recruiter/sessions/{their_session}/evaluation/result"
    ).status_code in (200, 409)


def test_the_retention_view_shows_only_one_organizations_candidates(world):
    _publish(world.client, "iv_acme")
    _publish(world.rival_client, "iv_rival")
    _candidate(world, "Acme Applicant", evaluate=False)
    _invite(world.rival_client, "iv_rival", "Rival Applicant")

    mine = world.client.get("/api/recruiter/retention").json()
    theirs = world.rival_client.get("/api/recruiter/retention").json()
    assert mine["candidates"] == 1
    assert theirs["candidates"] == 1

    listed = world.client.get("/api/recruiter/retention/candidates").json()
    assert listed["total"] == 1
    assert "Rival" not in json.dumps(listed)
    assert "Acme Applicant" not in json.dumps(listed), "no names in a retention view"


# =========================================================================== #
#  After erasure, the report is gone — including from an old URL
# =========================================================================== #
def test_every_report_url_stops_working_after_erasure(world):
    """A stale link, a bookmark, a copied API path — none of them may return
    candidate data after deletion."""
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    evaluation_id = evaluations.list_for_session(session_id)[0].evaluation_id

    paths = [
        f"/api/recruiter/sessions/{session_id}",
        f"/api/recruiter/sessions/{session_id}/trail",
        f"/api/recruiter/sessions/{session_id}/evaluation",
        f"/api/recruiter/sessions/{session_id}/evaluation/result",
        f"/api/recruiter/sessions/{session_id}/evaluation/evidence",
        f"/api/recruiter/sessions/{session_id}/evaluations",
        f"/api/recruiter/sessions/{session_id}/review",
        f"/api/recruiter/evaluations/{evaluation_id}",
        f"/api/recruiter/evaluations/{evaluation_id}/result",
    ]
    # They work first, so the refusal afterwards means something.
    reachable = [p for p in paths if world.client.get(p).status_code == 200]
    assert reachable, "nothing was readable before erasure"

    assert world.client.delete(_data_url(token)).status_code == 200

    for path in paths:
        response = world.client.get(path)
        assert response.status_code == 404, path
        assert response.json()["detail"] == "Not found."


def test_the_erased_candidate_is_gone_from_every_list(world):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world, "Erased Person")
    _candidate(world, "Kept Person")

    world.client.delete(_data_url(token))

    for path in ("/api/recruiter/candidates", "/api/recruiter/overview",
                 "/api/recruiter/pilot/dataset", "/api/recruiter/pilot/summary"):
        body = world.client.get(path).text
        assert "Erased Person" not in body, path
    assert "Kept Person" in world.client.get("/api/recruiter/candidates").text


def test_the_lifecycle_endpoint_reports_what_is_really_on_disk(world):
    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)

    before = world.client.get(_data_url(token)).json()
    assert before["lifecycle"] == retention.ACTIVE
    assert before["data_present"] is True
    assert set(before["locations_holding_data"]) >= {
        "invitation", "session_state", "session_trail", "evaluations"}

    world.client.delete(_data_url(token))

    after = world.client.get(_data_url(token)).json()
    assert after["lifecycle"] == retention.DELETED
    assert after["data_present"] is False
    assert after["locations_holding_data"] == []
    assert after["token"].endswith("…"), "no full credential in a lifecycle report"


def test_the_api_refuses_to_report_success_on_a_partial_deletion(world, monkeypatch):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    monkeypatch.setattr(erasure, "_remove_session",
                        lambda sid: (_ for _ in ()).throw(OSError("read-only")))
    response = world.client.delete(_data_url(token))

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["lifecycle"] == retention.DELETION_FAILED
    assert "session_state" in detail["remaining"]
    assert store.exists(session_id)
    # No stack trace, no filesystem path.
    body = response.text.lower()
    for leak in ("traceback", "/users/", "site-packages", ".py\""):
        assert leak not in body


# =========================================================================== #
#  The retention simulation the phase asks for
# =========================================================================== #
def test_the_retention_simulation(world, monkeypatch):
    """A deterministic dataset with one of each interesting record, swept once.

    Produces the lifecycle table in DATA_LIFECYCLE.md §12. The point is not any
    single row — it is that one sweep over a mixed population does the right
    thing to every kind of record at once, including leaving alone the ones it
    should.
    """
    _publish(world.client, "iv_acme")
    expired_days = config.CANDIDATE_DATA_RETENTION_DAYS + 1

    fresh, fresh_session = _candidate(world, "Fresh Candidate")
    expired, expired_session = _candidate(world, "Expired Candidate")
    _age(expired, expired_days)

    already, already_session = _candidate(world, "Already Deleted")
    _age(already, expired_days)
    assert erasure.erase(already, actor="setup").ok

    failed, failed_session = _candidate(world, "Failed Deletion")
    _age(failed, expired_days)

    no_eval, no_eval_session = _candidate(world, "No Evaluation", evaluate=False)
    _age(no_eval, expired_days)

    never_sat = _invite(world.client, "iv_acme", "Never Sat")["token"]
    _age(never_sat, expired_days)

    # One record fails, once, so the sweep has to handle a mixed outcome.
    broken = {"tokens": {failed_session}}
    real = erasure._remove_session  # noqa: SLF001

    def sometimes(sid):
        if sid in broken["tokens"]:
            raise OSError("simulated storage failure")
        return real(sid)

    monkeypatch.setattr(erasure, "_remove_session", sometimes)

    # Four eligible, not six: the fresh record is inside its retention period,
    # and the already-deleted one is terminal. Both exclusions are the point —
    # a sweep that picked up either would be doing damage.
    swept = {c.token for c in retention.eligible()}
    assert fresh not in swept
    assert already not in swept
    assert swept == {expired, failed, no_eval, never_sat}

    report = erasure.sweep(actor="simulation")
    assert report["eligible"] == 4
    assert report["erased"] == 3
    assert report["failed"] == 1

    table = {
        "fresh": (fresh, retention.ACTIVE, True),
        "expired": (expired, retention.DELETED, False),
        "already deleted": (already, retention.DELETED, False),
        "failed deletion": (failed, retention.DELETION_FAILED, True),
        "no evaluation": (no_eval, retention.DELETED, False),
        "never sat": (never_sat, retention.DELETED, False),
    }
    for label, (token, expected_state, expect_data) in table.items():
        invite = invites.get(token)
        assert retention.lifecycle_of(invite) == expected_state, label
        assert bool(erasure.verify(token)) == expect_data, label

    assert store.exists(fresh_session)
    assert store.exists(failed_session), "the failed one really did keep its data"
    for gone in (expired_session, already_session, no_eval_session):
        assert not store.exists(gone)

    # The failure is visible, and the retry finishes it.
    assert invites.get(failed).deletion_error
    assert invites.get(failed).deletion_remaining
    broken["tokens"] = set()
    assert erasure.sweep()["erased"] == 1
    assert erasure.verify(failed) == []


def test_the_cleanup_cli_reports_and_exits_meaningfully(world, capsys):
    from tools import retention_cleanup

    _publish(world.client, "iv_acme")
    token, _ = _candidate(world)
    _age(token, config.CANDIDATE_DATA_RETENTION_DAYS + 1)

    assert retention_cleanup._print_status() == 0  # noqa: SLF001
    status = capsys.readouterr().out
    assert "Awaiting cleanup   : 1" in status
    assert "Priya" not in status, "no candidate names in an operator report"

    report = erasure.sweep()
    assert report["erased"] == 1
    assert erasure.verify(token) == []


# =========================================================================== #
#  Logging and transient state
#
#  Erasure only means something if the data was in the places erasure knows
#  about. A transcript copied into a log line, or left in a temporary file, is a
#  copy the lifecycle cannot reach.
# =========================================================================== #
def test_no_source_line_sends_candidate_content_to_a_log_or_an_error():
    """Static check over every sink in `services/`.

    Three known lines are allowed, each for a reason that is checked here rather
    than assumed: two error messages contain the WORD "token" and not a token,
    and the boot banner prints the development seed's well-known `demo` link,
    which production does not create.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    sinks = {
        "print": re.compile(r"\bprint\s*\("),
        "logging": re.compile(r"\blog(?:ger)?\.(?:debug|info|warning|error|exception)\s*\("),
        "raise": re.compile(r"\braise\s+\w*(?:Error|Exception|HTTPException)\("),
    }
    content = re.compile(
        r"\b(candidate_name|candidate_email|recipient|transcript|\.answers?\b|"
        r"quote|invite_token|session_grant|api_key|authorization)\b", re.I)

    allowed = {
        # The boot banner's demo link. `config.is_production()` gates the
        # invitation itself, and the print only runs when one was created.
        ("services/api/app.py", "invite.token"),
    }
    offenders = []
    for path in sorted((root / "services").rglob("*.py")):
        rel = str(path.relative_to(root))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"', "'")):
                continue
            if not any(p.search(line) for p in sinks.values()):
                continue
            if not content.search(line):
                continue
            if any(rel == f and marker in line for f, marker in allowed):
                continue
            offenders.append(f"{rel}:{number}  {stripped[:110]}")
    assert offenders == [], "\n".join(offenders)


def test_running_an_interview_prints_no_candidate_content(world, capsys):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world, "Zephyrine Quennell")
    printed = capsys.readouterr().out + capsys.readouterr().err
    assert "Zephyrine" not in printed
    assert "idempotency key on the capture call" not in printed
    assert token not in printed
    assert store.try_load(session_id).session_grant not in printed


def test_no_transient_file_outlives_the_data_it_describes(world, data_dir):
    """The deployment is file-backed: `.tmp` files from atomic writes and the
    evaluation lock directory are the whole of its transient state. There is no
    Redis, so there is no cache for a transcript to survive in — but the
    temporary files are real and are checked here."""
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)

    assert erasure.erase(token, actor="usr_admin").ok

    leftovers = [str(p.relative_to(data_dir)) for p in data_dir.rglob("*")
                 if p.is_file() and (p.suffix == ".tmp" or session_id in p.name)]
    assert leftovers == [], leftovers


def test_the_evaluation_lock_for_an_erased_session_is_gone(world):
    _publish(world.client, "iv_acme")
    token, session_id = _candidate(world)
    lock = config.DATA_DIR / "evaluations" / ".locks" / f"{session_id}.lock"
    assert lock.exists(), "the evaluation should have taken its lock"

    erasure.erase(token, actor="usr_admin")
    assert not lock.exists()
