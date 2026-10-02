"""The Agent Builder and its one Retell agent.

What must hold, whatever the model writes:
  * every call sends all 18 variables, as strings, and the prompt is left with no `{{`;
  * voice comes only from Persona → Voice, and length only from Persona → Follow-up depth;
  * the rubric never reaches Retell, and a payload that would carry it is refused;
  * nothing in the code can create a Retell agent: there is one;
  * the webhook acts only on a correctly signed body;
  * one organization never sees another's agents.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import config
from services.ai.workloads import agent_builder as ab
from services.api import agent_builder as api
from services.api.app import app
from services.assessment import agent_builder_retell as rx
from services.data import built_agents as store
from tests.conftest import sign_in

BASE = "/api/recruiter/agent-builder"


def _row(**cfg) -> dict:
    p = ab.normalise_plan(ab._offline_plan("A technical interview for a senior AI Engineer.", "interview"))
    c = ab.normalise_content(ab._offline_content(p))
    row = api._new_row(p, c, "brief", "interview", "org")
    row["cfg"] = api.clean_cfg({**row["cfg"], **cfg})
    return row


@pytest.fixture()
def client(data_dir, tenant, monkeypatch):
    from services.ai import brain

    def _no_model(*_a, **_k):
        raise brain.LLMError("offline")

    monkeypatch.setattr(brain.RuntimeBrain, "complete_json", _no_model)
    # The Agent Builder calls the gateway on its own workloads; keep every test offline.
    monkeypatch.setattr(ab, "_complete", _no_model)
    c = TestClient(app)
    sign_in(c, tenant)
    return c


def _draft(c, brief="A 20-minute technical interview for a senior AI Engineer.", mode="interview"):
    r = c.post(f"{BASE}/drafts", json={"brief": brief, "mode": mode})
    assert r.status_code == 200, r.text
    events = {}
    for chunk in r.text.strip().split("\n\n"):
        ev = re.search(r"^event: (.*)$", chunk, re.M).group(1)
        events[ev] = json.loads(re.search(r"^data: (.*)$", chunk, re.M).group(1))
    assert "error" not in events, events.get("error")
    return events


# --------------------------------------------------------------------------- #
#  The variable contract and the prompt
# --------------------------------------------------------------------------- #
def test_the_flow_uses_only_contract_and_system_variables():
    import json as _json

    from services.assessment import agent_builder_flow as flow

    used = rx.prompt_variables() | rx.prompt_variables(_json.dumps(flow.NODES))
    assert used <= set(rx.VARIABLES) | rx.SYSTEM_VARIABLES
    # Sent but read by no node: max_minutes sets the call cap, language goes as
    # agent_override.agent.language.
    assert set(rx.VARIABLES) - used <= {"max_minutes", "language"}


def test_the_flow_is_the_seven_node_design():
    from services.assessment import agent_builder_flow as flow

    nodes = {n["id"]: n for n in flow.NODES}
    assert list(nodes) == ["opening", "resume", "main", "followup", "stop", "closing", "end_call"]
    assert flow.FLOW["start_node_id"] == "opening"
    out = {k: {e["destination_node_id"] for e in n.get("edges", [])} | (
        {n["skip_response_edge"]["destination_node_id"]} if n.get("skip_response_edge") else set()) for k, n in nodes.items()}
    assert out["opening"] == {"resume", "main", "stop"} and out["main"] == {"followup", "closing", "stop"}
    assert out["followup"] == {"main", "closing", "stop"} and out["stop"] == {"closing"} and out["closing"] == {"end_call"}
    assert nodes["closing"]["instruction"] == {"type": "static_text", "text": "{{closing_line}}"}
    assert rx.general_prompt() == flow.GLOBAL_PROMPT.strip()      # the repo copy is the flow's prompt


@pytest.mark.parametrize("speaker", rx.SPEAKERS)
def test_every_call_fills_every_placeholder(speaker):
    row = _row(speaker=speaker)
    variables = rx.dynamic_variables(row)
    assert set(variables) == set(rx.VARIABLES)
    assert all(isinstance(v, str) for v in variables.values())
    assert "{{" not in rx.render(variables)


@pytest.mark.parametrize("engine,key", [("llm", "retell_llm"), ("flow", "conversation_flow")])
def test_participant_opens_means_the_agent_waits(engine, key):
    body = rx.web_call_body(_row(speaker="Participant opens"), "agent_x", engine=engine)
    assert body["agent_override"][key]["start_speaker"] == "user"
    assert body["retell_llm_dynamic_variables"]["opening_line"] == ""
    body = rx.web_call_body(_row(speaker="Tara opens"), "agent_x", engine=engine)
    assert body["agent_override"][key]["start_speaker"] == "agent"
    assert body["retell_llm_dynamic_variables"]["opening_line"]
    if engine == "flow":
        # A flow takes no begin message: the opening travels as {{opening_line}}.
        assert set(body["agent_override"][key]) == {"start_speaker"}


# --------------------------------------------------------------------------- #
#  Voice and length come from Persona and nothing else
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", rx.VOICE_KEYS)
def test_the_voice_is_the_persona_voice_choice(key):
    body = rx.web_call_body(_row(voice=key), "agent_x")
    v = rx.voice(key)
    assert body["agent_override"]["agent"]["voice_id"] == v.voice_id
    assert body["agent_override"]["agent"]["language"] == v.locale
    assert body["retell_llm_dynamic_variables"]["language"] == v.language


@pytest.mark.parametrize("depth,minutes", [("Light", 10), ("Probing", 20), ("Deep dive", 30)])
@pytest.mark.parametrize("ending,cap", [("Tara decides", 5), ("Hard time limit", 0), ("No end time", None)])
def test_the_length_is_the_follow_up_depth(depth, minutes, ending, cap):
    body = rx.web_call_body(_row(depth=depth, ending=ending), "agent_x")
    expect_cap = 60 if cap is None else minutes + cap
    assert body["retell_llm_dynamic_variables"]["target_minutes"] == str(minutes)
    assert body["retell_llm_dynamic_variables"]["max_minutes"] == str(expect_cap)
    assert body["agent_override"]["agent"]["max_call_duration_ms"] == (expect_cap + 2) * 60_000


def test_difficulty_and_question_count_change_neither_voice_nor_length():
    base = rx.web_call_body(_row(voice="willa", depth="Probing"), "agent_x")
    row = _row(voice="willa", depth="Probing", tone="Tough")
    row["agent"]["questions"] *= 3
    other = rx.web_call_body(row, "agent_x")
    assert other["agent_override"]["agent"] == base["agent_override"]["agent"]  # tuning included
    assert other["retell_llm_dynamic_variables"]["target_minutes"] == "20"


def test_the_language_always_follows_the_voice():
    cfg = api.clean_cfg({"voice": "emma", "language": "German"})
    assert cfg["language"] == "French"


def test_every_voice_has_a_real_retell_id():
    for v in rx.VOICES:
        assert v.default_id and "RETELL_VOICE_ID" not in v.default_id, v.key
        assert "-" in v.default_id  # provider-Name, as Retell names them


# --------------------------------------------------------------------------- #
#  The rubric never crosses
# --------------------------------------------------------------------------- #
def test_a_clean_agent_sends_no_rubric():
    row = _row()
    body = rx.web_call_body(row, "agent_x")
    assert rx.leaked_cues(body, row) == []
    blob = json.dumps(body)
    for r in row["agent"]["rubric"]:
        assert r["anchor"] not in blob
    assert row["agent"]["description"] not in blob


def test_instructions_that_restate_the_rubric_are_caught():
    row = _row()
    row["agent"]["instructions"] += " " + row["agent"]["rubric"][0]["anchor"]
    assert rx.leaked_cues(rx.web_call_body(row, "agent_x"), row)


def test_nothing_in_the_code_creates_a_retell_agent():
    root = Path(config.ROOT_DIR)
    for path in list((root / "services").rglob("*.py")) + list((root / "tools").glob("retell*.py")):
        text = path.read_text()
        for verb in ("create-agent", "create-retell-llm", "create_agent("):
            assert verb not in text, f"{path} can create a Retell agent"


# --------------------------------------------------------------------------- #
#  The API
# --------------------------------------------------------------------------- #
def test_draft_streams_plan_then_content_then_the_saved_agent(client):
    ev = _draft(client)
    assert list(ev) == ["plan", "content", "done"]
    done = ev["done"]
    assert done["agent"]["questions"] and done["agent"]["rubric"]
    assert sum(r["weight"] for r in done["agent"]["rubric"]) == 100
    assert done["leaked_cues"] == []
    assert done["call_config"]["agent_override"]["agent"]["voice_id"] == rx.voice(done["cfg"]["voice"]).voice_id
    assert client.get(f"{BASE}/agents/{done['agent_id']}").status_code == 200


def test_save_publish_cycle(client):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    r = client.post(f"{BASE}/agents/{aid}/publish")
    assert r.status_code == 422 and "Skills" in r.json()["detail"]["missing"]

    row["cfg"]["depth"] = "Deep dive"
    row["cfg"]["voice"] = "anthony"
    saved = client.put(f"{BASE}/agents/{aid}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}, "reviewed": True})
    assert saved.status_code == 200, saved.text
    s = saved.json()
    assert s["length"] == {"target_minutes": 30, "cap_minutes": 35}
    assert s["fields"]["length"] == "30 min"
    assert s["call_config"]["agent_override"]["agent"]["voice_id"] == "11labs-Anthony"

    pub = client.post(f"{BASE}/agents/{aid}/publish")
    assert pub.status_code == 200, pub.text
    assert pub.json()["published_version"] == 1


def test_revise_needs_a_model(client):
    aid = _draft(client)["done"]["agent_id"]
    r = client.post(f"{BASE}/agents/{aid}/revise", json={"instruction": "make it tougher"})
    assert r.status_code == 503


def test_revise_applies_the_change_and_unreviews_the_rubric(client, monkeypatch):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    client.put(f"{BASE}/agents/{aid}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}, "reviewed": True})
    changed = copy.deepcopy(row["agent"])
    changed["persona"]["style"] = "blunt, skeptical"
    from services.ai import brain
    monkeypatch.setattr(ab, "_complete", lambda *a, **k: {
        "scenario": row["fields"], "agent": changed,
        "settings": {"difficulty": "Tough", "followUpDepth": "Light", "voice": "carola"},
        "summary": "Made it tougher and shorter.",
    })
    r = client.post(f"{BASE}/agents/{aid}/revise", json={"instruction": "tougher, shorter"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cfg"]["tone"] == "Tough" and out["cfg"]["depth"] == "Light"
    assert out["length"]["target_minutes"] == 10
    assert out["cfg"]["voice"] == "carola" and out["cfg"]["language"] == "German"
    assert out["reviewed"] is False
    assert out["summary"] == "Made it tougher and shorter."


def test_a_test_call_goes_to_the_one_agent_with_the_persona_overrides(client, monkeypatch):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    monkeypatch.setattr(config, "RETELL_API_KEY", "key_test")
    monkeypatch.setattr(config, "RETELL_AGENT_BUILDER_AGENT_ID", "agent_one")
    sent = {}

    class FakeResponse:
        status_code = 201

        def json(self):
            return {"call_id": "call_123", "access_token": "tok"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update(url=url, body=json)
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    r = client.post(f"{BASE}/agents/{aid}/test-call")
    assert r.status_code == 200, r.text
    assert r.json() == {"access_token": "tok", "call_id": "call_123"}
    assert sent["url"].endswith("/v2/create-web-call")
    assert sent["body"]["agent_id"] == "agent_one"
    assert set(sent["body"]["retell_llm_dynamic_variables"]) == set(rx.VARIABLES)
    assert store.by_call("call_123")["agent_id"] == aid


def test_a_test_call_that_would_leak_the_rubric_is_refused(client, monkeypatch):
    row = _draft(client)["done"]
    row["agent"]["instructions"] += " " + row["agent"]["rubric"][0]["anchor"]
    client.put(f"{BASE}/agents/{row['agent_id']}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}})
    monkeypatch.setattr(config, "RETELL_API_KEY", "key_test")
    monkeypatch.setattr(config, "RETELL_AGENT_BUILDER_AGENT_ID", "agent_one")
    r = client.post(f"{BASE}/agents/{row['agent_id']}/test-call")
    assert r.status_code == 422
    assert r.json()["detail"]["leaked"]


def test_another_organizations_agent_is_not_there(client):
    aid = _draft(client)["done"]["agent_id"]
    row = store.load(aid)
    row["org_id"] = "some_other_org"
    store.save(row)
    assert client.get(f"{BASE}/agents/{aid}").status_code == 404
    assert aid not in [a["agent_id"] for a in client.get(f"{BASE}/agents").json()["agents"]]


def test_the_builder_is_not_on_the_public_surface():
    from services.security import matrix

    for route in matrix.routes(app):
        if route.path.startswith("/api/recruiter/agent-builder"):
            assert matrix.derive(route) == matrix.RECRUITER_AUTHENTICATED, route.path


def test_signed_out_is_refused(data_dir):
    assert TestClient(app).get(f"{BASE}/options").status_code == 401


def test_builder_open_demo_switch_opens_only_the_agent_builder(data_dir, monkeypatch):
    monkeypatch.setattr(config, "BUILDER_OPEN", True)
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    c = TestClient(app)
    assert c.get(f"{BASE}/options").status_code == 200
    # The rest of the recruiter console still needs a sign-in.
    assert c.get("/api/recruiter/interviews").status_code in (401, 503)


# --------------------------------------------------------------------------- #
#  The webhook
# --------------------------------------------------------------------------- #
def _sign(raw: bytes, key: str, ts: int) -> str:
    return f"v={ts},d=" + hmac.new(key.encode(), raw + str(ts).encode(), hashlib.sha256).hexdigest()


def test_signature_verification():
    raw, key, now = b'{"event":"call_ended"}', "k", int(time.time() * 1000)
    assert api.verify_signature(raw, _sign(raw, key, now), key, now)
    assert not api.verify_signature(raw + b" ", _sign(raw, key, now), key, now)
    assert not api.verify_signature(raw, _sign(raw, "other", now), key, now)
    assert not api.verify_signature(raw, _sign(raw, key, now - 6 * 60_000), key, now)
    assert not api.verify_signature(raw, "garbage", key, now)


def test_the_webhook_refuses_an_unsigned_body(data_dir, monkeypatch):
    monkeypatch.setattr(config, "RETELL_API_KEY", "k")
    r = TestClient(app).post("/api/agent-builder/retell-webhook", content=b"{}")
    assert r.status_code == 401


def test_the_webhook_ignores_calls_it_did_not_place(data_dir, monkeypatch):
    monkeypatch.setattr(config, "RETELL_API_KEY", "k")
    raw = json.dumps({"event": "call_ended", "call": {"call_id": "nobody", "metadata": {"source": "tara-agent-builder"}}}).encode()
    r = TestClient(app).post("/api/agent-builder/retell-webhook", content=raw,
                             headers={"x-retell-signature": _sign(raw, "k", int(time.time() * 1000))})
    assert r.status_code == 200 and r.json()["ignored"] is True


@pytest.mark.parametrize("word,want", [("Challenging", "Tough"), ("harder", "Realistic"), ("hard", "Tough"),
                                       ("easy", "Friendly"), ("Realistic", "Realistic")])
def test_difficulty_synonyms_land_on_a_real_value(word, want):
    # "harder" is not a known word, so the current value (Realistic) stays.
    assert ab._pick(word, rx.DIFFICULTIES, "Realistic") == want


@pytest.mark.parametrize("key", rx.VOICE_KEYS)
def test_the_persona_is_named_after_its_voice(key):
    p = ab.normalise_plan({"persona": {"name": "Dr. Emily Carter"}, "voice": key,
                           "opening_line": "Hi, I'm Emily Carter. Thanks for joining."})
    name = ab.voice_name(key)
    assert p["agent"]["persona"]["name"] == name
    assert "Emily" not in p["agent"]["opening_line"] and name in p["agent"]["opening_line"]


# --------------------------------------------------------------------------- #
#  The participant flow
# --------------------------------------------------------------------------- #
def _published_invite(client) -> tuple[str, str]:
    row = _draft(client)["done"]
    aid = row["agent_id"]
    client.put(f"{BASE}/agents/{aid}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}, "reviewed": True})
    assert client.post(f"{BASE}/agents/{aid}/publish").status_code == 200
    inv = client.post(f"{BASE}/agents/{aid}/invites", json={"name": "Aarav", "email": "a@x.test"})
    assert inv.status_code == 200, inv.text
    assert inv.json()["link"].endswith("/participant?code=" + inv.json()["code"])
    return aid, inv.json()["code"]


def test_inviting_needs_a_published_agent(client):
    aid = _draft(client)["done"]["agent_id"]
    assert client.post(f"{BASE}/agents/{aid}/invites", json={}).status_code == 409


def test_a_participant_signs_in_and_sees_only_the_published_agent(client, data_dir):
    aid, code = _published_invite(client)
    p = TestClient(app)
    assert p.post("/api/participant/sign-in", json={"code": "RP-0000-ZZ", "name": "A", "email": "a@x.test", "consent": True}).json()["detail"]["error"] == "authFail"
    assert p.post("/api/participant/sign-in", json={"code": code, "name": "A", "email": "a@x.test", "consent": False}).status_code == 422
    r = p.post("/api/participant/sign-in", json={"code": code.lower(), "name": "Aarav Mehta", "email": "A@X.test", "consent": True})
    assert r.status_code == 200, r.text
    v = r.json()
    blob = json.dumps(v)
    agent = store.load(aid)["published"]["agent"]
    for r_ in agent["rubric"]:
        assert r_["name"] in v["agent"]["skills"]
        assert r_["anchor"] not in blob                  # descriptors stay hidden
    assert agent["instructions"] not in blob and agent["questions"][0]["text"] not in blob
    assert "weight" not in blob and "result" not in blob
    assert v["call"]["target_minutes"] in (10, 20, 30)
    sid = v["session_id"]
    assert p.get(f"/api/participant/session/{sid}").status_code == 200
    # another browser, no grant: the session does not exist
    assert TestClient(app).get(f"/api/participant/session/{sid}").status_code == 404


def test_the_call_carries_the_name_and_a_reconnect_resumes(client, monkeypatch):
    aid, code = _published_invite(client)
    p = TestClient(app)
    sid = p.post("/api/participant/sign-in", json={"code": code, "name": "Aarav Mehta", "email": "a@x.test", "consent": True}).json()["session_id"]
    monkeypatch.setattr(config, "RETELL_API_KEY", "key_test")
    monkeypatch.setattr(config, "RETELL_AGENT_BUILDER_AGENT_ID", "agent_one")
    _book_now(p, sid, monkeypatch)
    sent = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json)
            return R(201, {"call_id": f"call_{len(sent)}", "access_token": "tok"})

        async def get(self, url, headers=None):
            return R(200, {"transcript_object": [{"role": "agent", "content": "Hi Aarav, tell me about RAG."},
                                                 {"role": "user", "content": "RAG retrieves documents at query time."}]})

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    first = p.post(f"/api/participant/session/{sid}/call", json={"speed": "slow"})
    assert first.status_code == 200 and first.json()["resumed"] is False
    v1 = sent[0]["retell_llm_dynamic_variables"]
    assert v1["candidate_name"] == "Aarav Mehta" and v1["resume_context"] == "none"
    assert sent[0]["agent_override"]["agent"]["voice_speed"] == 0.85
    assert sent[0]["metadata"]["participant_session"] == sid
    assert set(v1) == set(rx.VARIABLES)

    again = p.post(f"/api/participant/session/{sid}/call", json={})
    assert again.json()["resumed"] is True
    v2 = sent[1]["retell_llm_dynamic_variables"]
    assert "RAG retrieves documents" in v2["resume_context"]
    ov = sent[1]["agent_override"].get("conversation_flow") or sent[1]["agent_override"]["retell_llm"]
    assert ov["start_speaker"] == "agent"
    assert "cut off" in v2["opening_line"]


def _book_now(p, sid, monkeypatch):
    """Book the first offered slot and pretend it is that time."""
    from services.assessment import slots
    monkeypatch.setattr(slots, "retell_concurrency", lambda: (0, 20))
    start = p.get(f"/api/participant/session/{sid}/slots").json()["slots"][0]["start"]
    assert p.post(f"/api/participant/session/{sid}/booking", json={"start": start}).status_code == 200
    monkeypatch.setattr(slots, "can_join", lambda b, resuming=False, now=None: bool(b))


def test_complete_and_feedback_then_the_recruiter_sees_it(client):
    aid, code = _published_invite(client)
    p = TestClient(app)
    sid = p.post("/api/participant/sign-in", json={"code": code, "name": "Aarav", "email": "a@x.test", "consent": True}).json()["session_id"]
    assert p.post(f"/api/participant/session/{sid}/complete", json={"early": True, "elapsed_sec": 95}).status_code == 200
    assert p.post(f"/api/participant/session/{sid}/feedback", json={"rating": 4, "answers": {"clear": "Clear"}}).status_code == 200
    # submitted: the code cannot be used to sit it again
    again = p.post("/api/participant/sign-in", json={"code": code, "name": "Aarav", "email": "a@x.test", "consent": True})
    assert again.status_code == 409 and again.json()["detail"]["error"] == "completed"
    rows = client.get(f"{BASE}/agents/{aid}/sessions").json()["sessions"]
    assert rows[0]["status"] == "complete" and rows[0]["early"] is True and rows[0]["feedback"]["rating"] == 4


def test_every_agent_is_voice_only(client):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    row["cfg"].update(format="Chat", proctoring="Strict")
    out = client.put(f"{BASE}/agents/{aid}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}}).json()
    assert out["cfg"]["format"] == "Voice only" and out["cfg"]["proctoring"] == "Off"

def test_nothing_about_the_role_play_is_public_before_sign_in(client):
    _published_invite(client)
    assert TestClient(app).get("/api/participant/invite/RP-0000-ZZ").status_code in (404, 405)

def test_publishing_makes_one_shared_candidate_link_everyone_can_use(client):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    client.put(f"{BASE}/agents/{aid}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}, "reviewed": True})
    pub = client.post(f"{BASE}/agents/{aid}/publish").json()
    link = pub["candidate_link"]
    assert link["path"] == "/participant?code=" + link["code"]
    assert client.post(f"{BASE}/agents/{aid}/publish").json()["candidate_link"] == link   # same link on republish
    a, b = TestClient(app), TestClient(app)
    sa = a.post("/api/participant/sign-in", json={"code": link["code"], "name": "A", "email": "a@x.test", "consent": True}).json()["session_id"]
    sb = b.post("/api/participant/sign-in", json={"code": link["code"], "name": "B", "email": "b@x.test", "consent": True}).json()["session_id"]
    assert sa != sb                                                    # one session per person
    again = a.post("/api/participant/sign-in", json={"code": link["code"], "name": "A", "email": "A@x.test", "consent": True}).json()["session_id"]
    assert again == sa                                                 # the same person rejoins
    a.post(f"/api/participant/session/{sa}/complete", json={})
    assert a.post("/api/participant/sign-in", json={"code": link["code"], "name": "A", "email": "a@x.test", "consent": True}).status_code == 409


def test_every_call_carries_the_tuned_turn_taking():
    agent = rx.web_call_body(_row(), "agent_x")["agent_override"]["agent"]
    assert agent["responsiveness"] == 0.4 and agent["interruption_sensitivity"] == 0.7
    assert agent["backchannel_frequency"] == 0.2 and agent["enable_backchannel"] is True


def test_publishing_applies_the_current_rules_to_an_older_agent(client):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    client.put(f"{BASE}/agents/{aid}", json={**{k: row[k] for k in ("fields", "agent", "cfg")}, "reviewed": True})
    stored = store.load(aid)
    stored["cfg"]["format"], stored["fields"]["type"] = "Video & voice", "Interview"   # saved under the old rules
    store.save(stored)
    pub = store.load(aid) if client.post(f"{BASE}/agents/{aid}/publish").status_code == 200 else None
    assert pub["published"]["cfg"]["format"] == "Voice only"
    assert pub["published"]["fields"]["type"] == "Role-play"



# --------------------------------------------------------------------------- #
#  Slots: never more live calls than Retell allows
# --------------------------------------------------------------------------- #
def _signed_in(client, n=1):
    aid, code = _published_invite(client)
    row = store.load(aid)
    shared = next(i for i in row["invites"] if i.get("shared"))["code"]
    out = []
    for i in range(n):
        c = TestClient(app)
        sid = c.post("/api/participant/sign-in", json={"code": shared, "name": f"P{i}", "email": f"p{i}@x.test", "consent": True}).json()["session_id"]
        out.append((c, sid))
    return out


def test_a_call_needs_a_booking_and_the_booked_time(client, monkeypatch):
    from services.assessment import slots
    monkeypatch.setattr(slots, "retell_concurrency", lambda: (0, 20))
    monkeypatch.setattr(config, "RETELL_API_KEY", "key_test")
    monkeypatch.setattr(config, "RETELL_AGENT_BUILDER_AGENT_ID", "agent_one")
    (p, sid), = _signed_in(client)
    r = p.post(f"/api/participant/session/{sid}/call", json={})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "slot"          # no booking
    later = p.get(f"/api/participant/session/{sid}/slots").json()["slots"][-1]["start"]
    p.post(f"/api/participant/session/{sid}/booking", json={"start": later})
    r = p.post(f"/api/participant/session/{sid}/call", json={})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "slot"          # booked, but not yet time


def test_a_slot_never_takes_more_people_than_the_limit(client, monkeypatch):
    monkeypatch.setenv("TARA_SLOT_CAPACITY", "2")
    people = _signed_in(client, 3)
    start = people[0][0].get(f"/api/participant/session/{people[0][1]}/slots").json()["slots"][0]["start"]
    codes = [c.post(f"/api/participant/session/{sid}/booking", json={"start": start}).status_code for c, sid in people]
    assert codes == [200, 200, 409]
    c, sid = people[2]
    left = {s["start"]: s["left"] for s in c.get(f"/api/participant/session/{sid}/slots").json()["slots"]}
    assert left[start] == 0
    # moving a booking frees its place
    c0, s0 = people[0]
    later = c0.get(f"/api/participant/session/{s0}/slots").json()["slots"][-1]["start"]
    assert c0.post(f"/api/participant/session/{s0}/booking", json={"start": later}).status_code == 200
    assert c.post(f"/api/participant/session/{sid}/booking", json={"start": start}).status_code == 200


def test_a_long_conversation_holds_every_block_it_runs_into():
    from datetime import datetime, timezone
    from services.assessment import slots
    t = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    assert len(slots.blocks_for(t, 25)) == 1 and len(slots.blocks_for(t, 35)) == 2 and len(slots.blocks_for(t, 60)) == 2


def test_the_joining_window():
    from datetime import datetime, timedelta, timezone
    from services.assessment import slots
    start = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    b = {"start": slots.iso(start), "cap_minutes": 25}
    assert not slots.can_join(b, now=start - timedelta(minutes=6))
    assert slots.can_join(b, now=start - timedelta(minutes=5))
    assert slots.can_join(b, now=start + timedelta(minutes=15))
    assert not slots.can_join(b, now=start + timedelta(minutes=16))
    assert slots.can_join(b, resuming=True, now=start + timedelta(minutes=30))     # a reconnect, still inside the booking


def test_no_call_when_retell_is_at_its_limit(client, monkeypatch):
    from services.assessment import slots
    monkeypatch.setattr(config, "RETELL_API_KEY", "key_test")
    monkeypatch.setattr(config, "RETELL_AGENT_BUILDER_AGENT_ID", "agent_one")
    (p, sid), = _signed_in(client)
    _book_now(p, sid, monkeypatch)
    monkeypatch.setattr(slots, "retell_concurrency", lambda: (20, 20))
    r = p.post(f"/api/participant/session/{sid}/call", json={})
    assert r.status_code == 503 and r.json()["detail"]["error"] == "busy"


def test_the_slot_under_way_can_still_be_booked_while_joinable():
    from datetime import datetime, timedelta, timezone
    from services.assessment import slots
    starts = slots.offered(now=datetime.now(timezone.utc))
    first = starts[0]
    assert first + timedelta(minutes=slots.JOIN_LATE_MIN) > datetime.now(timezone.utc)



def test_each_llm_use_has_its_own_model_slot():
    from services.ai.gateway import Workload, workload_config
    uses = {"agent_designer": "AGENT_DESIGNER_MODEL", "agent_reviser": "AGENT_REVISER_MODEL",
            "question_suggester": "QUESTION_SUGGESTER_MODEL", "rehearsal": "REHEARSAL_MODEL",
            "agent_scorer": "AGENT_SCORER_MODEL"}
    for w, var in uses.items():
        assert workload_config(Workload(w)).model == getattr(config, var)
    assert workload_config(Workload.AGENT_SCORER).temperature == 0.0


# --------------------------------------------------------------------------- #
#  Skill Master
# --------------------------------------------------------------------------- #
def test_the_skill_master_lists_readable_skills_for_every_domain(client):
    cat = client.get(f"{BASE}/skill-master").json()
    assert {d["category"] for d in cat["domains"]} == {"technical", "functional", "behavioural"}
    assert all(d["skills"] for d in cat["domains"])
    assert "Negotiation" in [s for d in cat["domains"] for s in d["skills"]]


def test_skills_added_from_the_master_get_anchors_and_questions(client, monkeypatch):
    row = _draft(client)["done"]
    aid = row["agent_id"]
    agent = copy.deepcopy(row["agent"])
    agent["rubric"].append({"name": "Negotiation", "anchor": "", "weight": 0})
    client.put(f"{BASE}/agents/{aid}", json={"fields": row["fields"], "agent": agent, "cfg": row["cfg"], "reviewed": True})
    monkeypatch.setattr(ab, "_complete", lambda *a, **k: {"skills": [
        {"name": "negotiation", "anchor": "Trades concessions for value.", "questions": ["What would you give up first?", "Why that price?"]},
        {"name": "Not in this agent", "anchor": "x", "questions": ["ignored"]}]})
    r = client.post(f"{BASE}/agents/{aid}/skills/draft", json={"names": ["Negotiation"]})
    assert r.status_code == 200, r.text
    out = r.json()
    neg = next(s for s in out["agent"]["rubric"] if s["name"] == "Negotiation")
    assert neg["anchor"] == "Trades concessions for value."
    assert [q["tag"] for q in out["agent"]["questions"]].count("Negotiation") == 2
    assert out["added"] == 2 and out["reviewed"] is False


def test_the_participant_never_hears_tara_only_the_persona():
    row = _row()
    row["agent"]["persona"]["name"] = "Dr. Maya Rao"
    row["agent"]["opening_line"] = "Hi, I'm Tara. Thanks for joining."
    row["agent"]["instructions"] = "Tara plays a skeptical buyer."
    v = rx.dynamic_variables(row, candidate_name="Tara Singh")
    assert "Tara" not in v["opening_line"] and "Maya" in v["opening_line"]
    assert v["conversation_instructions"].startswith("Maya plays")
    assert v["candidate_name"] == "Tara Singh"  # a participant may really be called Tara
