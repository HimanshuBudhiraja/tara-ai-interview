"""The role-play HTTP surface.

Most of these are about what must NOT come back. The engine can be correct and
the product still broken if an endpoint hands a candidate the scoring key, so
the leak tests matter more here than the happy path does.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.app import app
from services.assessment import scenarios


@pytest.fixture()
def client(data_dir):
    return TestClient(app)


@pytest.fixture()
def library():
    return scenarios.load_all()


def _start(client, scenario_id: str, subject_id: str = "t1"):
    r = client.post("/api/roleplay/session/start", json={
        "scenario_id": scenario_id, "subject_name": "Sam", "subject_id": subject_id,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _play_to_end(client, session_id: str, said: str = "mmm", cap: int = 40) -> None:
    for _ in range(cap):
        out = client.post(f"/api/roleplay/session/{session_id}/turn", json={"said": said})
        assert out.status_code == 200, out.text
        if out.json()["reply"]["ends"]:
            return
    raise AssertionError("scene never ended — the turn budget is not being enforced")


# --------------------------------------------------------------------------- #
#  The library
# --------------------------------------------------------------------------- #
def test_agents_lists_the_library_and_its_configurations(client):
    body = client.get("/api/roleplay/agents").json()
    assert body["poc"] is True
    by_type = {a["agent_type"]: a for a in body["agents"]}
    assert set(by_type) == {"role_readiness", "sales", "customer_service", "roleplay"}
    assert len(by_type["sales"]["scenarios"]) >= 3


def test_an_unknown_scenario_is_a_404(client):
    assert client.get("/api/roleplay/scenario/nope").status_code == 404


# --------------------------------------------------------------------------- #
#  Running one
# --------------------------------------------------------------------------- #
def test_a_session_opens_on_the_briefing_and_the_personas_line(client, library):
    defn = library["cs_double_charge"]
    body = _start(client, "cs_double_charge")
    assert body["reply"]["text"] == defn.persona.opening_line
    assert defn.script.welcome in body["briefing"]
    assert body["attempt_no"] == 1


def test_a_turn_advances_and_the_scene_ends(client):
    body = _start(client, "cs_double_charge")
    _play_to_end(client, body["session_id"])


def test_resume_returns_the_transcript(client):
    body = _start(client, "cs_double_charge")
    client.post(f"/api/roleplay/session/{body['session_id']}/turn", json={"said": "hello"})
    out = client.get(f"/api/roleplay/session/{body['session_id']}").json()
    assert out["phase"] == "in_scene"
    assert len(out["transcript"]) >= 3


def test_resume_does_not_tell_the_subject_which_beat_they_are_in(client):
    """A beat id is a map of the assessment they are sitting."""
    body = _start(client, "cs_double_charge")
    out = client.get(f"/api/roleplay/session/{body['session_id']}").json()
    for turn in out["transcript"]:
        assert set(turn) == {"speaker", "text"}


def test_a_turn_on_an_unknown_session_is_a_404(client):
    assert client.post("/api/roleplay/session/nope/turn", json={"said": "hi"}).status_code == 404


# --------------------------------------------------------------------------- #
#  Attempts
# --------------------------------------------------------------------------- #
def test_a_one_shot_scenario_refuses_a_second_attempt(client):
    """The hiring scenario is selection-grade: one sitting, and the refusal has
    to come from the server, because the client is not a security control."""
    _start(client, "hiring_sjt_missed_handoff", subject_id="once")
    again = client.post("/api/roleplay/session/start", json={
        "scenario_id": "hiring_sjt_missed_handoff", "subject_id": "once",
    })
    assert again.status_code == 409


def test_practice_allows_unlimited_attempts(client):
    for i in range(3):
        body = _start(client, "sales_price_objection", subject_id="many")
        assert body["attempt_no"] == i + 1


# --------------------------------------------------------------------------- #
#  What the result endpoint discloses
# --------------------------------------------------------------------------- #
def test_a_selection_grade_result_tells_the_subject_nothing(client):
    body = _start(client, "hiring_sjt_missed_handoff")
    _play_to_end(client, body["session_id"])
    out = client.get(f"/api/roleplay/session/{body['session_id']}/result").json()
    assert out["visibility"] == "hidden"
    assert "competencies" not in out
    assert "evidence" not in out


def test_a_certification_result_is_gated_to_pass_or_fail(client):
    body = _start(client, "cs_double_charge")
    _play_to_end(client, body["session_id"])
    out = client.get(f"/api/roleplay/session/{body['session_id']}/result").json()
    assert out["visibility"] == "gated"
    for row in out["competencies"]:
        assert set(row) == {"label", "met", "reached"}   # never the cues behind it


def test_a_practice_result_shows_everything(client):
    body = _start(client, "sales_price_objection")
    _play_to_end(client, body["session_id"])
    out = client.get(f"/api/roleplay/session/{body['session_id']}/result").json()
    assert out["visibility"] == "full"
    assert "covered" in out["competencies"][0]
    assert "missed" in out["competencies"][0]


@pytest.mark.parametrize("scenario_id", [
    "hiring_sjt_missed_handoff", "cs_double_charge", "sales_enterprise_discovery",
])
def test_no_subject_facing_response_ever_leaks_the_scoring_key(client, library, scenario_id):
    """The one that would quietly end the product: a subject who can read the
    key can rehearse it, and everyone measured against it afterwards is not
    being measured."""
    defn = library[scenario_id]
    body = _start(client, scenario_id)
    session_id = body["session_id"]
    _play_to_end(client, session_id)

    seen = " ".join([
        client.get(f"/api/roleplay/scenario/{scenario_id}").text,
        client.get(f"/api/roleplay/session/{session_id}").text,
        client.get(f"/api/roleplay/session/{session_id}/result").text,
    ])
    for beat in defn.beats:
        for cue in beat.red_flags:
            assert cue not in seen, f"{scenario_id} leaked red flag: {cue}"
        if defn.policy.feedback_visibility != "full":
            for cue in beat.looking_for:
                assert cue not in seen, f"{scenario_id} leaked cue: {cue}"
    for behaviour in defn.candidate.expected_behaviours:
        assert behaviour not in seen, f"{scenario_id} leaked: {behaviour}"


def test_the_reviewer_view_carries_its_own_warning(client):
    """`reviewer=true` is a demo affordance, not an access control, and the
    response says so — a POC that reads like the product is how a demo switch
    survives into a deployment."""
    body = _start(client, "hiring_sjt_missed_handoff")
    _play_to_end(client, body["session_id"])
    out = client.get(
        f"/api/roleplay/session/{body['session_id']}/result", params={"reviewer": "true"}
    ).json()
    assert out["audience"] == "reviewer"
    assert "not an access control" in out["warning"]
    assert out["evidence"]["per_skill"]


# --------------------------------------------------------------------------- #
#  The session grant
# --------------------------------------------------------------------------- #
def test_another_browser_cannot_reach_someone_elses_session(data_dir):
    """A uuid4 is hard to guess. Hard to guess is not authorization, and these
    routes return a transcript."""
    owner = TestClient(app)
    started = owner.post("/api/roleplay/session/start", json={
        "scenario_id": "cs_double_charge", "subject_id": "owner",
    })
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    assert owner.get(f"/api/roleplay/session/{session_id}").status_code == 200

    stranger = TestClient(app)          # same server, no grant cookie
    for method, path in (
        ("get", f"/api/roleplay/session/{session_id}"),
        ("get", f"/api/roleplay/session/{session_id}/result"),
    ):
        assert getattr(stranger, method)(path).status_code == 404
    assert stranger.post(
        f"/api/roleplay/session/{session_id}/turn", json={"said": "hi"}
    ).status_code == 404


def test_a_refused_session_is_indistinguishable_from_a_missing_one(data_dir):
    """404 rather than 403, both times: confirming an id is real tells an
    attacker which ids are worth attacking."""
    owner = TestClient(app)
    real = owner.post("/api/roleplay/session/start", json={
        "scenario_id": "cs_double_charge", "subject_id": "owner",
    }).json()["session_id"]

    stranger = TestClient(app)
    refused = stranger.get(f"/api/roleplay/session/{real}")
    missing = stranger.get("/api/roleplay/session/deadbeef")
    assert refused.status_code == missing.status_code == 404
    assert refused.json() == missing.json()
