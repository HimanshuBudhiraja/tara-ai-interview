"""The question pool over HTTP: generate, edit, add, remove, regenerate.

Plus the stable-identifier guarantees the pool depends on — a question that
references `task_3` is only safe if `task_3` cannot become a different task.
"""
from __future__ import annotations

import pytest

from services.assessment import pool as pool_service
from services.data import interviews, versions
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import Skill, Task
from tests.conftest import sign_in

pytestmark = pytest.mark.usefixtures("data_dir")


def _seed(pool) -> InterviewConfig:
    """A designed interview, ready for questions."""
    cfg = interviews.save(InterviewConfig(
        id="iv_q", title="Senior Backend Engineer", role="senior_backend_engineer",
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
                 description="Reconcile the ledger.",
                 required_skills=["skl_recon", "skl_idem"]),
            Task(id="tsk_oncall", name="Take payment on-call",
                 description="Handle an incident.", required_skills=["skl_inc"]),
        ],
    ))
    return cfg


@pytest.fixture()
def client(data_dir, tenant, monkeypatch, pool):
    from fastapi.testclient import TestClient

    monkeypatch.setenv(pool_service.STUB_ENV, "1")
    from services.data import jobs

    monkeypatch.setattr(jobs, "_PATH", data_dir / "jobs.json")
    _seed(pool)

    from services.api.app import app

    with TestClient(app) as c:
        sign_in(c, tenant)
        yield c


BASE = "/api/recruiter/interviews/iv_q/questions"


def _generate(client) -> dict:
    response = client.post(f"{BASE}/generate", json={})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
#  Stable identifiers (§3)
# --------------------------------------------------------------------------- #
def test_tasks_keep_their_ids_across_a_save_and_reload(client):
    before = [t.id for t in interviews.get("iv_q").tasks]
    interviews.save(interviews.get("iv_q"))
    assert [t.id for t in interviews.get("iv_q").tasks] == before
    assert all(t.startswith("tsk_") for t in before)


def test_renaming_a_skill_does_not_break_the_questions_pointing_at_it(client):
    """Ids derived from a display name are stable only by convention. A question
    that stopped assessing anything because someone fixed a typo would be very
    hard to notice."""
    _generate(client)
    before = {
        q["question_id"]: q["primary_skill"]["id"]
        for g in client.get(BASE).json()["groups"] for q in g["questions"]
    }

    cfg = interviews.get("iv_q")
    cfg.skills[0].name = "Idempotency (renamed)"
    interviews.save(cfg)

    after = {
        q["question_id"]: q["primary_skill"]["id"]
        for g in client.get(BASE).json()["groups"] for q in g["questions"]
    }
    assert after == before
    assert any(g["skill_name"] == "Idempotency (renamed)"
               for g in client.get(BASE).json()["groups"])


def test_renaming_a_task_does_not_break_the_questions_grounded_in_it(client):
    _generate(client)
    before = {
        q["question_id"]: (q["task"] or {}).get("id")
        for g in client.get(BASE).json()["groups"] for q in g["questions"]
    }
    cfg = interviews.get("iv_q")
    cfg.tasks[0].name = "Design capture (renamed)"
    interviews.save(cfg)

    after = {
        q["question_id"]: (q["task"] or {}).get("id")
        for g in client.get(BASE).json()["groups"] for q in g["questions"]
    }
    assert after == before


def test_a_draft_written_before_tasks_had_ids_is_migrated(data_dir, pool):
    """Backward compatibility: drafts already on disk must keep working."""
    cfg = _seed(pool)
    raw = cfg.to_dict()
    for task in raw["tasks"]:
        task.pop("id")
    migrated = InterviewConfig.from_dict(raw)

    assert all(t.id.startswith("tsk_") for t in migrated.tasks)
    assert len({t.id for t in migrated.tasks}) == len(migrated.tasks)


def test_generated_questions_have_stable_opaque_ids(client):
    pool_payload = _generate(client)
    ids = [q["question_id"] for g in pool_payload["groups"] for q in g["questions"]]
    assert all(i.startswith("q_") for i in ids)
    assert len(set(ids)) == len(ids)
    # And they survive a reload.
    again = [q["question_id"] for g in client.get(BASE).json()["groups"]
             for q in g["questions"]]
    assert sorted(again) == sorted(ids)


# --------------------------------------------------------------------------- #
#  Generation
# --------------------------------------------------------------------------- #
def test_generation_returns_a_reviewable_pool(client):
    payload = _generate(client)
    assert payload["generated"] is True
    assert payload["summary"]["pool_size"] > 0
    assert payload["groups"]
    assert payload["running_order"]
    assert payload["coverage"]["ok"] is True


def test_generation_refuses_an_interview_with_no_design(client):
    cfg = interviews.get("iv_q")
    cfg.skills, cfg.tasks = [], []
    interviews.save(cfg)
    response = client.post(f"{BASE}/generate", json={})
    assert response.status_code == 422
    assert "design" in response.json()["detail"]["errors"]


def test_generation_writes_the_draft_and_publishes_nothing(client):
    _generate(client)
    assert versions.get_draft("iv_q") is not None
    assert versions.latest_published("iv_q") is None
    assert versions.list_for("iv_q") == []


def test_the_draft_definition_carries_the_questions(client):
    _generate(client)
    definition = versions.draft_definition("iv_q")
    assert definition.questions
    for q in definition.questions:
        assert q.looking_for and q.evaluation_criteria and q.clarify


def test_the_pool_groups_high_priority_skills_first(client):
    payload = _generate(client)
    priorities = [g["priority"] for g in payload["groups"]]
    assert priorities == sorted(priorities, key=lambda p: {"high": 0, "medium": 1, "low": 2}[p])


# --------------------------------------------------------------------------- #
#  Editing
# --------------------------------------------------------------------------- #
def _first_question(client) -> dict:
    return client.get(BASE).json()["groups"][0]["questions"][0]


def test_a_question_can_be_edited(client):
    _generate(client)
    q = _first_question(client)
    response = client.patch(f"{BASE}/{q['question_id']}", json={
        "question_text": "Walk me through designing capture so a retry cannot double-charge.",
        "looking_for": ["names the idempotency key", "explains the duplicate path",
                        "says how they would test it"],
    })
    assert response.status_code == 200
    edited = next(x for g in response.json()["groups"] for x in g["questions"]
                  if x["question_id"] == q["question_id"])
    assert "double-charge" in edited["prompt"]
    assert edited["looking_for"][0] == "names the idempotency key"


def test_edits_persist_to_the_draft_version(client):
    _generate(client)
    q = _first_question(client)
    client.patch(f"{BASE}/{q['question_id']}", json={"difficulty": "hard"})
    stored = versions.draft_definition("iv_q")
    assert next(x for x in stored.questions if x.id == q["question_id"]).difficulty == "hard"


@pytest.mark.parametrize("patch,because", [
    ({"question_text": "How old were you when you started?"}, "illegal"),
    ({"looking_for": []}, "no cues"),
    ({"evaluation_criteria": []}, "no rubric"),
    ({"clarify": ""}, "no clarification"),
    ({"probe_bank": ["Only one."]}, "thin probe bank"),
    ({"primary_skill_id": "skl_ghost"}, "unknown skill"),
    ({"difficulty": "brutal"}, "bad difficulty"),
])
def test_invalid_edits_are_refused(client, patch, because):
    _generate(client)
    q = _first_question(client)
    response = client.patch(f"{BASE}/{q['question_id']}", json=patch)
    assert response.status_code == 422, f"{because} was accepted"
    assert "question" in response.json()["detail"]["errors"]


def test_a_rejected_edit_leaves_the_question_as_it_was(client):
    _generate(client)
    q = _first_question(client)
    client.patch(f"{BASE}/{q['question_id']}", json={"looking_for": []})
    assert _first_question(client)["looking_for"] == q["looking_for"]


def test_a_question_can_be_remapped_to_a_different_skill(client):
    _generate(client)
    q = _first_question(client)
    response = client.patch(f"{BASE}/{q['question_id']}",
                            json={"primary_skill_id": "skl_recon"})
    assert response.status_code == 200
    moved = next(x for g in response.json()["groups"] for x in g["questions"]
                 if x["question_id"] == q["question_id"])
    assert moved["primary_skill"]["id"] == "skl_recon"


# --------------------------------------------------------------------------- #
#  Adding and removing
# --------------------------------------------------------------------------- #
VALID_MANUAL = {
    "question_text": "A retry storm double-charges twelve customers overnight. "
                     "Walk me through your first hour.",
    "primary_skill_id": "skl_idem",
    "question_type": "situational",
    "difficulty": "hard",
    "looking_for": [
        "stops further charges before investigating",
        "names who they tell and when",
        "describes how they identify the affected charges",
    ],
    "evaluation_criteria": [
        {"label": "Containment first", "description": "Stops the harm.", "importance": "high"},
        {"label": "Communication", "description": "Tells the right people.", "importance": "medium"},
    ],
    "probe_bank": [
        "How would you identify which charges were affected?",
        "Who would you tell first, and why them?",
    ],
    "clarify": "I'm asking about the first hour, not the eventual fix.",
}


def test_a_recruiter_can_add_a_question_by_hand(client):
    _generate(client)
    before = client.get(BASE).json()["summary"]["pool_size"]
    response = client.post(BASE, json=VALID_MANUAL)
    assert response.status_code == 201
    payload = response.json()
    assert payload["summary"]["pool_size"] == before + 1
    assert any(q["source"] == "manual" for g in payload["groups"] for q in g["questions"])


@pytest.mark.parametrize("missing", ["looking_for", "evaluation_criteria", "probe_bank", "clarify"])
def test_a_hand_written_question_without_its_metadata_is_refused(client, missing):
    """§24. A bare question is one the runtime cannot classify and the scoring
    engine cannot judge."""
    _generate(client)
    body = {**VALID_MANUAL, missing: [] if missing != "clarify" else ""}
    response = client.post(BASE, json=body)
    assert response.status_code == 422


def test_a_hand_written_question_with_no_skill_is_refused(client):
    _generate(client)
    response = client.post(BASE, json={**VALID_MANUAL, "primary_skill_id": ""})
    assert response.status_code == 422


def test_a_question_can_be_removed(client):
    _generate(client)
    client.post(BASE, json=VALID_MANUAL)          # headroom, so removal is allowed
    payload = client.get(BASE).json()
    before = payload["summary"]["pool_size"]
    target = payload["groups"][0]["questions"][0]["question_id"]

    response = client.delete(f"{BASE}/{target}")
    assert response.status_code == 200
    assert response.json()["summary"]["pool_size"] == before - 1


def test_a_removal_that_breaks_coverage_is_refused_by_the_backend(client):
    """§25. The screen showing the warning is not the thing enforcing it."""
    _generate(client)
    payload = client.get(BASE).json()
    high = next(g for g in payload["groups"] if g["priority"] == "high")

    refused = False
    for question in high["questions"]:
        response = client.delete(f"{BASE}/{question['question_id']}")
        if response.status_code == 409:
            refused = True
            assert response.json()["detail"]["problems"]
            break
    assert refused, "a high-priority skill was emptied without complaint"


def test_removing_a_question_that_does_not_exist_is_a_404(client):
    _generate(client)
    assert client.delete(f"{BASE}/q_nonexistent").status_code == 404


# --------------------------------------------------------------------------- #
#  Regeneration
# --------------------------------------------------------------------------- #
def test_regenerating_one_question_leaves_the_others_alone(client):
    _generate(client)
    payload = client.get(BASE).json()
    target = payload["groups"][0]["questions"][0]
    others = {q["question_id"]: q["prompt"]
              for g in payload["groups"] for q in g["questions"]
              if q["question_id"] != target["question_id"]}

    response = client.post(f"{BASE}/{target['question_id']}/regenerate")
    assert response.status_code == 200
    after = {q["question_id"]: q["prompt"]
             for g in response.json()["groups"] for q in g["questions"]}

    assert target["question_id"] not in after
    for qid, prompt in others.items():
        assert after[qid] == prompt, "an unrelated question was rewritten"


def test_regeneration_preserves_the_recruiter_s_edits_elsewhere(client):
    """§26. Deliberately unlike the Interview Designer, which is destructive."""
    _generate(client)
    payload = client.get(BASE).json()
    edited = payload["groups"][1]["questions"][0]
    client.patch(f"{BASE}/{edited['question_id']}",
                 json={"question_text": "My own carefully worded question about reconciliation."})

    target = payload["groups"][0]["questions"][0]["question_id"]
    after = client.post(f"{BASE}/{target}/regenerate").json()

    kept = next(q for g in after["groups"] for q in g["questions"]
                if q["question_id"] == edited["question_id"])
    assert kept["prompt"] == "My own carefully worded question about reconciliation."


def test_regeneration_publishes_nothing(client):
    _generate(client)
    target = _first_question(client)["question_id"]
    client.post(f"{BASE}/{target}/regenerate")
    assert versions.latest_published("iv_q") is None


# --------------------------------------------------------------------------- #
#  Coverage endpoint and audit
# --------------------------------------------------------------------------- #
def test_the_coverage_endpoint_reports_the_pool_state(client):
    _generate(client)
    payload = client.get(f"{BASE}/validate").json()
    assert payload["coverage"]["ok"] is True
    assert payload["summary"]["pool_size"] > 0
    assert payload["preview"]


def test_the_flow_leaves_an_audit_trail(client):
    from services.data import audit

    _generate(client)
    q = _first_question(client)
    client.patch(f"{BASE}/{q['question_id']}", json={"difficulty": "hard"})
    client.post(BASE, json=VALID_MANUAL)
    client.post(f"{BASE}/{q['question_id']}/regenerate")
    client.get(f"{BASE}/validate")

    events = [e["event"] for e in audit.read_product()]
    for expected in ("QUESTION_GENERATION_STARTED", "QUESTION_GENERATED",
                     "QUESTION_EDITED", "QUESTION_ADDED", "QUESTION_REGENERATED",
                     "QUESTION_POOL_VALIDATED"):
        assert expected in events, f"{expected} was not recorded"


def test_the_audit_trail_records_that_the_stub_produced_these(client):
    """A pool generated by the stub must be distinguishable from one a real
    model wrote — otherwise nobody can tell which questions were reviewed
    against a real provider."""
    from services.data import audit

    _generate(client)
    generated = [e for e in audit.read_product() if e["event"] == "QUESTION_GENERATED"]
    assert generated and generated[-1]["stub"] is True


def test_the_audit_trail_holds_no_job_description_or_secret(client):
    from services import config
    from services.data import audit

    _generate(client)
    blob = str(audit.read_product())
    assert "Own payment services." not in blob
    if config.OPENROUTER_API_KEY:
        assert config.OPENROUTER_API_KEY not in blob


# --------------------------------------------------------------------------- #
#  Provider failure must not look like success
# --------------------------------------------------------------------------- #
def test_with_no_stub_and_no_provider_generation_fails_retryably(client, monkeypatch):
    """§32. Fake success in an assessment tool is worse than an outage."""
    monkeypatch.delenv(pool_service.STUB_ENV, raising=False)
    from services.ai.gateway import get_gateway

    monkeypatch.setattr(get_gateway(), "live", False)

    response = client.post(f"{BASE}/generate", json={})
    assert response.status_code == 502
    assert response.json()["detail"]["retryable"] is True
    assert not interviews.get("iv_q").questions
