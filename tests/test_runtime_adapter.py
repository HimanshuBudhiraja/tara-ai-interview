"""§31 — a published, generated assessment driven through the REAL orchestrator.

Not a mock of the runtime and not a reimplementation of it: the actual
`Orchestrator`, running a pool that came out of the Question Generator, through
the same turn loop a candidate uses.

The claim being tested is that publishing did not change how an interview
behaves. If the adapter could only satisfy the runtime by bending it, that would
be a failure to report — not a licence to edit the orchestrator.
"""
from __future__ import annotations

import pytest

from packages.types import InterviewDefinition
from packages.types.definition import SkillSpec, TaskSpec
from services.assessment import blueprint as bp
from services.assessment import pool as pool_service
from services.assessment import runtime_adapter
from services.assessment.publication import snapshot, validate_for_publish
from services.data import interviews, versions
from services.data import sessions as store
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import Skill, Task
from services.orchestrator.engine import Orchestrator
from services.orchestrator.state import SessionState

SUBSTANTIVE = (
    "Last quarter a retry storm double-charged eleven customers overnight. I stopped "
    "further captures within ten minutes by flipping the kill switch, then pulled the "
    "affected charge ids from the ledger and reconciled them against the provider's "
    "settlement file. I told the on-call lead and finance before I started refunding, "
    "because they needed to answer customers. Afterwards I added an idempotency key on "
    "the capture path and a test that replays a duplicate request."
)
THIN = "I'd just try to sort it out as best I can."


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setenv(pool_service.STUB_ENV, "1")


@pytest.fixture()
def published(data_dir, pool):
    """A designed interview, generated, validated and published for real."""
    cfg = interviews.save(InterviewConfig(
        id="iv_pub", title="Senior Backend Engineer", role="senior_backend_engineer",
        role_title="Senior Backend Engineer", job_id="job_x",
        jd_text="Own payment services.", experience_from=5, experience_to=9,
        interview_type="medium", difficulty="medium", recommended_duration_min=20,
        skills=[
            Skill(name="Idempotent design", competency_id="skl_idem", priority="high",
                  evaluated=True, assessment_scope="Retry-safe capture paths."),
            Skill(name="Reconciliation", competency_id="skl_recon", priority="high",
                  evaluated=True, assessment_scope="Settlement mismatches."),
            Skill(name="Design review", competency_id="skl_review", priority="medium",
                  evaluated=True, assessment_scope="Naming the failure mode."),
            Skill(name="Incident response", competency_id="skl_inc", priority="low",
                  evaluated=True, assessment_scope="Live triage."),
        ],
        tasks=[
            Task(id="tsk_capture", name="Design payment capture",
                 description="Design idempotent capture.", required_skills=["skl_idem"]),
            Task(id="tsk_recon", name="Reconcile settlements",
                 description="Reconcile the ledger.",
                 required_skills=["skl_recon", "skl_idem"]),
            Task(id="tsk_review", name="Review a peer design",
                 description="Review a design.", required_skills=["skl_review"]),
            Task(id="tsk_oncall", name="Take payment on-call",
                 description="Handle an incident.", required_skills=["skl_inc"]),
        ],
    ))
    definition = interviews.build_definition(cfg)
    plan = bp.build(definition)
    questions, _ = pool_service.generate(definition, plan)

    from dataclasses import asdict

    cfg.questions = [asdict(q) for q in questions]
    cfg.blueprint = plan.to_dict()
    cfg.question_budget = plan.live_item_budget
    interviews.save(cfg)

    definition = interviews.build_definition(cfg)
    check = validate_for_publish(definition, plan)
    assert check.ok, check.messages

    row = versions.publish(cfg.id, snapshot(definition), validate=False)
    cfg.status, cfg.published_version = "published", row.version
    interviews.save(cfg)
    return cfg, row


@pytest.fixture()
def orch(monkeypatch):
    """The production orchestrator, with the classifier on heuristics.

    No provider is called: the heuristic classifier is the runtime's own
    fallback, so this is a real path rather than a test-only one.
    """
    from services.ai.workloads.answer_classifier import classify_heuristic

    o = Orchestrator()
    monkeypatch.setattr(o.llm, "read_answer", classify_heuristic)
    monkeypatch.setattr(o.llm, "write_probe", lambda *a, **k: {"probe": ""})
    return o


def _session(published) -> SessionState:
    cfg, row = published
    return SessionState.new(
        "Priya", "cand_1", cfg.role, invite_token="t",
        interview_id=cfg.id, interview_version=row.version,
    )


# --------------------------------------------------------------------------- #
#  Conversion
# --------------------------------------------------------------------------- #
def test_a_published_version_converts_into_the_runtime_contract(published):
    cfg, row = published
    definition = versions.definition_for(cfg.id, row.version)

    assert runtime_adapter.check_compatibility(definition) == []
    assessment = runtime_adapter.adapt(definition)
    assert assessment.question_count == len(definition.questions)
    assert assessment.version == row.version


def test_every_adapted_item_carries_what_the_turn_loop_reads(published):
    cfg, row = published
    assessment = runtime_adapter.adapt(versions.definition_for(cfg.id, row.version))
    for item in assessment.pool.items:
        assert item.prompt.strip()
        assert item.competency
        assert item.difficulty in ("easy", "medium", "hard")
        assert item.looking_for
        assert item.clarify
        if item.probe_eligible:
            assert item.probe_bank
        assert item.time_estimate_sec > 0


def test_competency_is_the_stable_skill_id(published):
    """§18. One competency hierarchy, not two."""
    cfg, row = published
    definition = versions.definition_for(cfg.id, row.version)
    assessment = runtime_adapter.adapt(definition)
    skill_ids = {s.id for s in definition.skills}
    for item in assessment.pool.items:
        assert item.competency in skill_ids


def test_an_assessment_the_runtime_cannot_run_is_refused(published):
    cfg, row = published
    definition = versions.definition_for(cfg.id, row.version)
    definition.questions[0].looking_for = []

    problems = runtime_adapter.check_compatibility(definition)
    assert problems
    with pytest.raises(runtime_adapter.AdapterError):
        runtime_adapter.adapt(definition)


# --------------------------------------------------------------------------- #
#  The orchestrator, running a published generated pool
# --------------------------------------------------------------------------- #
def test_the_interview_opens_with_a_warm_up(published, orch):
    """Unchanged behaviour: nobody meets a hard scenario first."""
    state = _session(published)
    orch.start(state)
    first = orch._context(state)[0].item(state.asked_item_ids[0])
    assert first.difficulty == "easy"


def test_selection_is_deterministic_on_a_published_pool(published, orch):
    a, b = _session(published), _session(published)
    orch.start(a)
    orch.start(b)
    for _ in range(4):
        orch.on_answer(a, SUBSTANTIVE)
        orch.on_answer(b, SUBSTANTIVE)
    assert a.asked_item_ids == b.asked_item_ids


def test_competency_balancing_favours_high_priority_skills(published, orch):
    state = _session(published)
    orch.start(state)
    for _ in range(20):
        if orch.on_answer(state, SUBSTANTIVE).ends:
            break

    definition = versions.definition_for(*[state.interview_id, state.interview_version])
    priority = {s.id: s.priority for s in definition.skills}
    asked: dict[str, int] = {}
    for item_id in state.asked_item_ids:
        record = state.records[item_id]
        asked[record.competency] = asked.get(record.competency, 0) + 1

    high = max((n for s, n in asked.items() if priority.get(s) == "high"), default=0)
    low = max((n for s, n in asked.items() if priority.get(s) == "low"), default=0)
    assert high >= low


def test_the_interview_respects_the_published_budget(published, orch):
    state = _session(published)
    orch.start(state)
    reply = None
    for _ in range(40):
        reply = orch.on_answer(state, SUBSTANTIVE)
        if reply.ends:
            break
    assert reply.ends
    definition = versions.definition_for(state.interview_id, state.interview_version)
    assert len(state.asked_item_ids) <= definition.runtime.question_budget


def test_probing_works_on_generated_questions(published, orch):
    state = _session(published)
    orch.start(state)
    assert orch.on_answer(state, THIN).kind == "probe"


def test_the_authored_fallback_probe_is_used_when_generation_is_rejected(
    published, orch, monkeypatch
):
    """The generated pool's probe banks feed the runtime's existing fallback."""
    monkeypatch.setattr(orch, "_probes_may_be_generated", lambda s: True)
    monkeypatch.setattr(orch.llm, "write_probe",
                        lambda *a, **k: {"probe": "How old are you?"})   # illegal

    state = _session(published)
    orch.start(state)
    item_id = state.asked_item_ids[0]
    reply = orch.on_answer(state, THIN)

    assert reply.kind == "probe"
    assert "How old" not in reply.text
    bank = orch._context(state)[0].item(item_id).probe_bank
    assert any(p in reply.text for p in bank)


def test_clarify_uses_the_published_clarification(published, orch):
    state = _session(published)
    orch.start(state)
    item = orch._context(state)[0].item(state.current_item_id)
    reply = orch.on_answer(state, "What do you mean by that?")
    assert reply.kind == "clarify"
    assert item.clarify[:30] in reply.text


def test_repeat_works(published, orch):
    state = _session(published)
    orch.start(state)
    item = state.current_item_id
    reply = orch.on_repeat(state)
    assert reply.kind == "repeat"
    assert state.current_item_id == item


def test_silence_does_not_advance_and_eventually_moves_on(published, orch):
    state = _session(published)
    orch.start(state)
    item = state.current_item_id

    first = orch.on_silence(state)
    assert state.current_item_id == item, "silence advanced the interview"
    assert first.awaiting_same_answer

    offered = False
    for _ in range(6):
        reply = orch.on_silence(state)
        offered = offered or reply.device_help_offered
        if state.current_item_id != item:
            break
    assert offered
    assert state.current_item_id != item


def test_skip_is_acknowledged_and_advances(published, orch):
    state = _session(published)
    orch.start(state)
    item = state.current_item_id
    orch.on_answer(state, "I've never done that, so I'd have to skip this one.")
    assert state.current_item_id != item


def test_rejoin_resumes_the_same_question_on_the_same_version(published, orch):
    state = _session(published)
    orch.start(state)
    orch.on_answer(state, THIN)

    reloaded = store.load(state.session_id)
    assert reloaded.interview_version == state.interview_version

    reply = orch.resume(reloaded)
    record = reloaded.current
    assert reply.item_id == record.item_id
    expected = record.probes_asked[-1] if record.probes_asked else record.prompt
    assert expected in reply.text


def test_completion_is_reached_through_the_turn_loop(published, orch):
    state = _session(published)
    orch.start(state)
    for _ in range(40):
        if orch.on_answer(state, SUBSTANTIVE).ends:
            break
    assert state.phase == "complete"
    assert state.completed_at is not None


def test_the_candidate_never_sees_a_score_or_a_future_question(published, orch):
    """§23. What crosses the boundary is one turn's worth of text."""
    state = _session(published)
    reply = orch.start(state)
    payload = reply.as_dict()

    assert "score" not in payload and "level" not in payload
    assert set(payload["progress"]) <= {"asked", "answered", "total", "coverage", "phase"}
    for entry in payload["progress"]["coverage"]:
        assert set(entry) == {"id", "label", "asked", "target"}

    definition = versions.definition_for(state.interview_id, state.interview_version)
    blob = str(payload)
    future = [q.question_text for q in definition.questions
              if q.id != state.current_item_id]
    for text in future:
        assert text not in blob, "a future question leaked into the turn payload"
    for question in definition.questions:
        for cue in question.looking_for:
            assert cue not in blob, "an expected signal leaked into the turn payload"
        for criterion in question.evaluation_criteria:
            assert criterion.label not in blob, "a rubric label leaked"


def test_the_authored_csr_interview_still_behaves_identically(pool, data_dir):
    """The adapter must not have changed the interview that already existed."""
    from services.orchestrator.pool import Pool

    cfg = interviews.ensure_default(pool)
    definition = versions.definition_for(cfg.id, 1)
    runtime_pool = Pool.from_definition(definition)
    order = [
        i.id for i in runtime_pool.running_order(
            runtime_pool.plan_from_definition(definition)
        )
    ]
    assert order == [
        "csr-emp-01", "csr-tro-03", "csr-esc-01", "csr-cla-01",
        "csr-pol-01", "csr-own-01", "csr-emp-02", "csr-tro-01",
    ]
