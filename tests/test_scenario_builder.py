"""The Scenario Builder and the Retell export.

Two properties matter more than the happy path. A draft must leave `normalise`
publishable, whatever shape the model returned it in. And nothing the evaluator
judges on may reach the Retell payload, for any scenario, shipped or built.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from packages.types.agent import get_agent
from services.ai.workloads import scenario_builder as builder
from services.api.app import app
from services.assessment import knowledge, retell_export, scenarios
from services.config import ROOT_DIR
from tests.conftest import sign_in


@pytest.fixture()
def client(data_dir, tenant, monkeypatch):
    # Offline: the builder falls back to its template, so no test needs a key.
    from services.ai import brain

    def _no_model(*_a, **_k):
        raise brain.LLMError("offline")

    monkeypatch.setattr(brain.RuntimeBrain, "complete_json", _no_model)
    c = TestClient(app)
    sign_in(c, tenant)
    return c


# --------------------------------------------------------------------------- #
#  normalise
# --------------------------------------------------------------------------- #
def test_a_negative_signal_is_moved_to_red_flags_not_left_to_stall_the_beat():
    out = builder.normalise({
        "title": "t", "briefing": "b",
        "beats": [{"intent": "push", "skill_id": "listening",
                   "looking_for": ["Asks why", "Does not offer a discount"]}],
    })
    beat = out["beats"][0]
    assert beat["looking_for"] == ["Asks why"]
    assert "Does not offer a discount" in beat["red_flags"]


def test_a_beat_skill_with_no_competency_gets_one():
    out = builder.normalise({"beats": [{"intent": "x", "skill_id": "Rapport", "looking_for": ["Builds rapport"]}]})
    assert [c["id"] for c in out["evaluation"]["competencies"]] == ["rapport"]


@pytest.mark.parametrize("purpose,visibility,attempts,selection", [
    ("practice", "full", 0, False),
    ("certification", "gated", 3, False),
    ("selection", "hidden", 1, True),
])
def test_purpose_sets_a_policy_that_validates(purpose, visibility, attempts, selection):
    out = builder.normalise({"purpose": purpose})
    p = out["policy"]
    assert (p["feedback_visibility"], p["attempts"], p["selection_grade"]) == (visibility, attempts, selection)
    assert not builder.to_definition(out).policy.validate()


def test_the_offline_template_draft_is_publishable():
    out = builder.draft([{"role": "user", "text": "A manager tells a report their project is cancelled."}])
    assert out["drafted_by"] == "template" or out["drafted_by"] == "model"
    assert builder.problems(out["scenario"]) == []


# --------------------------------------------------------------------------- #
#  The Retell export
# --------------------------------------------------------------------------- #
def test_the_prompt_uses_exactly_the_variable_contract():
    """A placeholder with no value is spoken literally in a live call."""
    text = (ROOT_DIR / "content" / "retell" / "generic_roleplay_prompt.md").read_text()
    used = set(re.findall(r"\{\{(\w+)\}\}", text.split("\n---\n", 1)[-1]))
    assert used == set(retell_export.VARIABLES)


@pytest.mark.parametrize("scenario_id", sorted(scenarios.load_all()))
def test_no_shipped_scenario_sends_its_scoring_key_to_retell(scenario_id):
    defn = scenarios.load_all()[scenario_id]
    kb = knowledge.load_all().get(defn.knowledge_base_id)
    body = retell_export.web_call_body(defn, "agent_x", get_agent(defn.agent_type), kb)
    assert retell_export.leaked_cues(body, defn) == []
    assert all(isinstance(v, str) for v in body["retell_llm_dynamic_variables"].values())


def test_an_exit_condition_that_restates_the_key_is_withheld():
    defn = scenarios.load_all()["hiring_sjt_missed_handoff"]
    body = retell_export.web_call_body(defn, "agent_x")
    assert body["withheld_exit_conditions"]
    assert "concrete plan" not in body["retell_llm_dynamic_variables"]["exit_conditions"].lower()


def test_allowed_sources_filter_the_knowledge_that_crosses():
    defn = scenarios.load_all()["cs_double_charge"]
    kb = knowledge.load_all()[defn.knowledge_base_id]
    defn.guardrails.allowed_sources = ["escalation"]
    text = retell_export.dynamic_variables(defn, kb=kb)["knowledge"]
    assert "Supervisor callbacks" in text
    assert "duplicate charge is refunded" not in text


# --------------------------------------------------------------------------- #
#  The API
# --------------------------------------------------------------------------- #
def test_draft_then_publish_makes_the_scenario_playable(client):
    r = client.post("/api/recruiter/roleplay-builder/draft", json={
        "messages": [{"role": "user", "text": "A nurse explains a delayed discharge to a worried relative."}],
    })
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["problems"] == []
    assert draft["retell"]["leaked_cues"] == []

    pub = client.post("/api/recruiter/roleplay-builder/publish", json={"scenario": draft["scenario"]})
    assert pub.status_code == 200, pub.text
    sid = pub.json()["scenario"]["scenario_id"]
    assert pub.json()["version"] == 1

    start = client.post("/api/roleplay/session/start", json={"scenario_id": sid, "subject_id": "b1"})
    assert start.status_code == 200, start.text

    again = client.post("/api/recruiter/roleplay-builder/publish", json={"scenario": pub.json()["scenario"]})
    assert again.json()["version"] == 2


def test_publish_refuses_a_broken_configuration(client):
    r = client.post("/api/recruiter/roleplay-builder/publish", json={"scenario": {"title": "x", "beats": []}})
    assert r.status_code == 422
    assert r.json()["detail"]["problems"]


def test_the_builder_is_not_on_the_public_surface():
    from services.security import matrix

    for route in matrix.routes(app):
        if "roleplay-builder" in route.path:
            assert matrix.derive(route) == matrix.RECRUITER_AUTHENTICATED, route.path


# --------------------------------------------------------------------------- #
#  The local-prototype exception
# --------------------------------------------------------------------------- #
def _anonymous(monkeypatch, host: str, flag: bool, env: str = "development"):
    from services import config
    from services.ai import brain

    monkeypatch.setattr(config, "LOCAL_NO_LOGIN", flag)
    monkeypatch.setattr(config, "ENVIRONMENT", env)
    monkeypatch.setattr(brain.RuntimeBrain, "complete_json",
                        lambda *a, **k: (_ for _ in ()).throw(brain.LLMError("offline")))
    return TestClient(app, client=(host, 5000))


def _draft(c):
    return c.post("/api/recruiter/roleplay-builder/draft",
                  json={"messages": [{"role": "user", "text": "An upset customer."}]})


def test_local_no_login_lets_this_machine_in(data_dir, monkeypatch):
    assert _draft(_anonymous(monkeypatch, "127.0.0.1", True)).status_code == 200


@pytest.mark.parametrize("host,flag,env", [
    ("127.0.0.1", False, "development"),   # off by default
    ("203.0.113.9", True, "development"),  # not this machine
    ("127.0.0.1", True, "production"),     # never in a deployment
    ("127.0.0.1", True, "staging"),
])
def test_everything_else_still_needs_a_sign_in(data_dir, monkeypatch, host, flag, env):
    assert _draft(_anonymous(monkeypatch, host, flag, env)).status_code == 401
