"""The question pool: blueprint, generation, validation, editing, coverage.

Runs entirely on the deterministic stub — no provider is called anywhere in this
file. That is the point of the stub existing: the whole feature has to be
verifiable with the inference balance at zero.
"""
from __future__ import annotations

import pytest

from packages.types import InterviewDefinition
from packages.types.definition import CriterionSpec, QuestionSpec, SkillSpec, TaskSpec
from services.assessment import blueprint as bp
from services.assessment import pool as pool_service
from services.assessment import preview as preview_service
from services.assessment.blueprint import BlueprintError
from services.assessment.validation import (
    validate_question,
    validate_question_pool_coverage,
)


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setenv(pool_service.STUB_ENV, "1")


def _definition(**overrides) -> InterviewDefinition:
    defn = InterviewDefinition(
        interview_id="iv_test",
        role_title="Senior Backend Engineer",
        experience_from=5,
        experience_to=9,
        interview_type="medium",
        difficulty="medium",
        recommended_duration_min=20,
        skills=[
            SkillSpec(id="skl_idem", name="Idempotent design", priority="high",
                      assessment_scope="Retry-safe capture paths."),
            SkillSpec(id="skl_recon", name="Reconciliation", priority="high",
                      assessment_scope="Settlement mismatches."),
            SkillSpec(id="skl_review", name="Design review", priority="medium",
                      assessment_scope="Naming the failure mode."),
            SkillSpec(id="skl_incident", name="Incident response", priority="low",
                      assessment_scope="Live triage."),
        ],
        tasks=[
            TaskSpec(id="tsk_capture", name="Design payment capture",
                     description="Design idempotent capture.", skill_ids=["skl_idem"]),
            TaskSpec(id="tsk_recon", name="Reconcile settlements",
                     description="Reconcile the ledger.",
                     skill_ids=["skl_recon", "skl_idem"]),
            TaskSpec(id="tsk_review", name="Review a peer design",
                     description="Review a design.", skill_ids=["skl_review"]),
            TaskSpec(id="tsk_oncall", name="Take payment on-call",
                     description="Handle an incident.", skill_ids=["skl_incident"]),
        ],
    )
    for key, value in overrides.items():
        setattr(defn, key, value)
    return defn


def _generated() -> tuple[InterviewDefinition, bp.Blueprint]:
    defn = _definition()
    plan = bp.build(defn)
    defn.questions, _ = pool_service.generate(defn, plan)
    return defn, plan


# --------------------------------------------------------------------------- #
#  Blueprint
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind,duration", [("short", 9), ("medium", 20), ("deep", 40)])
def test_each_interview_type_produces_a_workable_blueprint(kind, duration):
    plan = bp.build(_definition(interview_type=kind, recommended_duration_min=duration))
    assert plan.slots
    assert plan.pool_size >= plan.live_item_budget, (
        "the interview would run out of questions and close early"
    )
    assert plan.target_duration_min == duration


def test_a_longer_interview_asks_more_questions():
    short = bp.build(_definition(interview_type="short", recommended_duration_min=9))
    deep = bp.build(_definition(interview_type="deep", recommended_duration_min=40))
    assert deep.live_item_budget > short.live_item_budget
    assert deep.pool_size >= short.pool_size


def test_priority_drives_coverage_at_every_duration():
    """§6. A pool where every skill gets the same number of questions has
    thrown away the recruiter's judgement about what the role depends on."""
    for kind, duration in (("short", 9), ("medium", 20), ("deep", 40)):
        plan = bp.build(_definition(interview_type=kind, recommended_duration_min=duration))
        by_priority: dict[str, list[int]] = {}
        for target in plan.coverage:
            by_priority.setdefault(target.priority, []).append(target.target_question_count)
        assert min(by_priority["high"]) >= max(by_priority["low"]), (
            f"{kind}: high-priority skills got no more coverage than low-priority ones"
        )


def test_high_priority_skills_get_a_floor_of_two():
    """A skill the role depends on must not rest on one answer, because one bad
    question then decides it."""
    plan = bp.build(_definition())
    for target in plan.coverage:
        if target.priority == "high":
            assert target.min_items >= 2
            assert target.target_question_count >= 2


def test_the_budget_leaves_room_for_follow_ups():
    """Duration is not questions × minutes: a probe costs time, and it happens
    exactly when the candidate is struggling."""
    with_probes = bp.live_item_budget(20, "medium", probe_rate=0.55)
    without = bp.live_item_budget(20, "medium", probe_rate=0.0)
    assert with_probes < without


def test_every_pool_contains_a_warm_up_even_when_small_and_hard():
    """The runtime always opens easy. A short, hard interview rounds every
    skill's easy allocation to zero unless the blueprint guarantees one."""
    plan = bp.build(_definition(
        interview_type="short", recommended_duration_min=8, difficulty="hard",
    ))
    assert any("easy" in slot.difficulties for slot in plan.slots)


def test_slot_ids_are_stable_across_rebuilds():
    """Otherwise regenerating a blueprint renumbers every slot and orphans the
    questions already generated against them."""
    a, b = bp.build(_definition()), bp.build(_definition())
    assert [s.id for s in a.slots] == [s.id for s in b.slots]


def test_slots_are_batched_so_a_generator_can_tell_them_apart():
    """§19. Asked five separate times for a question about one skill, a
    generator has no way to know it already wrote four."""
    plan = bp.build(_definition())
    multi = [s for s in plan.slots if s.count > 1]
    assert multi, "every slot asks for exactly one question"
    for slot in plan.slots:
        assert len(slot.difficulties) == slot.count


def test_question_type_follows_the_work_not_a_template():
    """§8. A payments engineer interviewed entirely in 'tell me about a time'
    is being measured on storytelling."""
    plan = bp.build(_definition())
    types = {s.skill_name: s.question_type for s in plan.slots}
    assert types["Idempotent design"] in ("technical", "task_based")


def test_a_design_with_no_skills_or_tasks_is_rejected():
    with pytest.raises(BlueprintError):
        bp.build(_definition(skills=[]))
    with pytest.raises(BlueprintError):
        bp.build(_definition(tasks=[]))


# --------------------------------------------------------------------------- #
#  Generation
# --------------------------------------------------------------------------- #
def test_generation_fills_the_blueprint():
    defn, plan = _generated()
    assert defn.questions
    assert len({q.id for q in defn.questions}) == len(defn.questions), "duplicate ids"
    for q in defn.questions:
        assert q.slot_id, "a question with no slot cannot be regenerated in place"


def test_every_generated_question_carries_the_whole_contract():
    """§36. A question missing any of this is one the runtime cannot run or the
    scoring engine cannot judge."""
    defn, _ = _generated()
    for q in defn.questions:
        assert q.id.startswith("q_")
        assert q.question_text.strip()
        assert q.question_type
        assert q.difficulty
        assert q.skill_id
        assert 3 <= len(q.looking_for) <= 5
        assert len(q.evaluation_criteria) >= 2
        assert q.clarify.strip()
        if q.probe_eligible:
            assert len(q.probe_bank) >= 2
        assert q.time_budget_sec > 0


def test_generated_questions_pass_their_own_validation():
    defn, _ = _generated()
    for q in defn.questions:
        verdict = validate_question(
            q, defn, existing=[o for o in defn.questions if o.id != q.id]
        )
        assert verdict.ok, f"{q.question_text!r}: {verdict.messages}"


def test_one_failing_slot_does_not_lose_the_rest_of_the_pool():
    """§20. Throwing away eleven good questions because the twelfth failed
    helps nobody."""
    defn = _definition()
    plan = bp.build(defn)
    failing = plan.slots[0].id

    from services.ai.workloads import question_writer_stub

    original = question_writer_stub.generate_slot

    def flaky(slot, definition, **kw):
        if slot.id == failing:
            raise RuntimeError("provider exploded")
        return original(slot, definition, **kw)

    question_writer_stub.generate_slot = flaky
    try:
        questions, report = pool_service.generate(defn, plan)
    finally:
        question_writer_stub.generate_slot = original

    assert questions, "the whole pool was discarded"
    assert len(report.failed_slots) == 1
    assert report.failed_slots[0]["slot_id"] == failing
    assert plan.slot(failing).status == "failed"


def test_a_failed_slot_can_be_retried_on_its_own():
    defn = _definition()
    plan = bp.build(defn)
    first = plan.slots[0]

    # Generate everything except the first slot.
    defn.questions, _ = pool_service.generate(
        defn, plan, only_slots=[s.id for s in plan.slots[1:]]
    )
    before = len(defn.questions)
    assert not any(q.slot_id == first.id for q in defn.questions)

    defn.questions, report = pool_service.generate(defn, plan, only_slots=[first.id])
    assert report.generated > 0
    assert len(defn.questions) > before
    assert any(q.slot_id == first.id for q in defn.questions)


def test_retrying_one_slot_leaves_other_questions_untouched():
    defn, plan = _generated()
    other_slot = plan.slots[1].id
    untouched = [q for q in defn.questions if q.slot_id == other_slot]
    before = {q.id: q.question_text for q in untouched}

    defn.questions, _ = pool_service.generate(
        defn, plan, only_slots=[plan.slots[0].id]
    )
    after = {q.id: q.question_text for q in defn.questions if q.slot_id == other_slot}
    assert after == before


def test_a_malformed_generation_is_rejected_not_persisted():
    defn = _definition()
    plan = bp.build(defn)

    from services.ai.workloads import question_writer_stub
    from services.ai.workloads.question_writer import SlotResult

    original = question_writer_stub.generate_slot

    def unsafe(slot, definition, **kw):
        result = original(slot, definition, **kw)
        result.questions[0].question_text = "How old were you when you started?"
        return result

    question_writer_stub.generate_slot = unsafe
    try:
        questions, report = pool_service.generate(defn, plan)
    finally:
        question_writer_stub.generate_slot = original

    texts = [q.question_text for q in questions]
    assert "How old were you when you started?" not in texts
    assert report.rejected


# --------------------------------------------------------------------------- #
#  Validation
# --------------------------------------------------------------------------- #
def _question(defn: InterviewDefinition, **overrides) -> QuestionSpec:
    base = QuestionSpec(
        id="q_probe",
        question_text="A retry storm double-charges twelve customers. Walk me through your first hour.",
        competency="skl_idem",
        skill_id="skl_idem",
        task_id="tsk_capture",
        question_type="situational",
        difficulty="hard",
        looking_for=[
            "stops further charges before investigating",
            "names who they tell and when",
            "describes how they identify the affected charges",
        ],
        evaluation_criteria=[
            CriterionSpec(id="c1", label="Containment first", description="Stops the harm."),
            CriterionSpec(id="c2", label="Communication", description="Tells the right people."),
        ],
        probe_bank=[
            "How would you identify which charges were affected?",
            "Who would you tell first, and why them?",
        ],
        clarify="I'm asking about the first hour, not the eventual fix.",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_a_well_formed_question_passes():
    defn = _definition()
    assert validate_question(_question(defn), defn).ok


@pytest.mark.parametrize("overrides,because", [
    ({"question_text": ""}, "empty"),
    ({"question_text": "How old were you when you started in payments?"}, "illegal"),
    ({"question_text": "As an AI language model, describe your approach."}, "meta text"),
    ({"question_text": "Don't you think you should always contain it first?"}, "leading"),
    ({"skill_id": ""}, "no primary skill"),
    ({"skill_id": "skl_ghost"}, "unknown skill"),
    ({"task_id": "tsk_ghost"}, "unknown task"),
    ({"difficulty": "brutal"}, "bad difficulty"),
    ({"question_type": "riddle"}, "bad type"),
    ({"looking_for": []}, "no cues"),
    ({"looking_for": ["a", "b", "c", "d", "e", "f", "g"]}, "too many cues"),
    ({"evaluation_criteria": []}, "no rubric"),
    ({"clarify": ""}, "no clarification"),
    ({"probe_bank": ["Only one?"]}, "thin probe bank"),
    ({"probe_bank": ["What visa are you on?", "And then?"]}, "illegal probe"),
    ({"probe_bank": ["Tell me more.", "And then?"]}, "malformed probe"),
])
def test_invalid_questions_are_rejected(overrides, because):
    defn = _definition()
    verdict = validate_question(_question(defn, **overrides), defn)
    assert not verdict.ok, f"{because} was accepted"


def test_a_clarification_that_recites_a_cue_is_rejected():
    """It hands the candidate the answer key at exactly the moment they said
    they were struggling."""
    defn = _definition()
    bad = _question(defn, clarify="Tell me how you stop further charges before investigating.")
    assert not validate_question(bad, defn).ok


def test_a_duplicate_question_is_rejected():
    defn = _definition()
    first = _question(defn)
    second = _question(defn, id="q_other")
    assert not validate_question(second, defn, existing=[first]).ok


def test_two_questions_about_the_same_task_are_not_duplicates_by_default():
    """Questions about one task inevitably share its vocabulary. Measuring that
    as similarity would reject every second question about anything."""
    defn = _definition()
    first = _question(defn)
    second = _question(
        defn, id="q_other",
        question_text="Before a retry storm happens, how would you design capture to prevent it?",
    )
    assert validate_question(second, defn, existing=[first]).ok


def test_human_edited_questions_are_held_to_the_same_standard():
    """A recruiter can type an illegal question as easily as a model can
    generate one, and the candidate cannot tell which happened."""
    defn = _definition()
    hand_written = _question(defn, question_text="Do you have children who'd affect on-call?")
    assert not validate_question(hand_written, defn).ok


# --------------------------------------------------------------------------- #
#  Coverage
# --------------------------------------------------------------------------- #
def test_a_generated_pool_satisfies_its_own_coverage():
    defn, plan = _generated()
    verdict = validate_question_pool_coverage(defn, plan)
    assert verdict.ok, verdict.messages


def test_coverage_fails_when_a_high_priority_skill_drops_below_its_floor():
    defn, plan = _generated()
    high = next(c for c in plan.coverage if c.priority == "high")
    defn.questions = [q for q in defn.questions if q.skill_id != high.skill_id]

    verdict = validate_question_pool_coverage(defn, plan)
    assert not verdict.ok
    assert any("needs at least" in m for m in verdict.messages)


def test_coverage_fails_when_the_pool_has_no_warm_up():
    defn, plan = _generated()
    defn.questions = [q for q in defn.questions if q.difficulty != "easy"]
    verdict = validate_question_pool_coverage(defn, plan)
    assert not verdict.ok
    assert any("easy" in m for m in verdict.messages)


def test_coverage_catches_a_question_pointing_at_a_deleted_skill():
    defn, plan = _generated()
    defn.skills = [s for s in defn.skills if s.id != "skl_incident"]
    verdict = validate_question_pool_coverage(defn, plan)
    assert any("no longer exists" in m for m in verdict.messages)


def test_an_empty_pool_reports_as_empty_not_as_broken():
    defn = _definition()
    plan = bp.build(defn)
    verdict = validate_question_pool_coverage(defn, plan)
    assert not verdict.ok
    assert "No questions have been generated yet." in verdict.messages


# --------------------------------------------------------------------------- #
#  Single-question regeneration
# --------------------------------------------------------------------------- #
def test_regenerating_one_question_preserves_its_slot_and_leaves_the_rest_alone():
    defn, plan = _generated()
    target = defn.questions[0]
    others = {q.id: q.question_text for q in defn.questions[1:]}

    replaced, report = pool_service.regenerate_question(defn, plan, target.id)

    assert report.generated == 1
    assert len(replaced) == len(defn.questions), "the pool changed size"
    assert not any(q.id == target.id for q in replaced), "the original is still there"

    new = next(q for q in replaced if q.slot_id == target.slot_id
               and q.id not in others)
    assert new.skill_id == target.skill_id
    assert new.difficulty == target.difficulty
    assert new.task_id == target.task_id

    for q in replaced:
        if q.id in others:
            assert q.question_text == others[q.id], "an unrelated question was rewritten"


def test_a_failed_regeneration_leaves_the_original_in_place():
    """A regeneration that fails must not leave a hole in the pool."""
    defn, plan = _generated()
    target = defn.questions[0]

    from services.ai.workloads import question_writer_stub

    original = question_writer_stub.generate_slot
    question_writer_stub.generate_slot = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("provider exploded")
    )
    try:
        replaced, report = pool_service.regenerate_question(defn, plan, target.id)
    finally:
        question_writer_stub.generate_slot = original

    assert report.generated == 0
    assert any(q.id == target.id for q in replaced)
    assert len(replaced) == len(defn.questions)


def test_regenerating_a_question_that_does_not_exist_raises():
    defn, plan = _generated()
    with pytest.raises(LookupError):
        pool_service.regenerate_question(defn, plan, "q_nonexistent")


# --------------------------------------------------------------------------- #
#  Runtime compatibility — §33's most important group
# --------------------------------------------------------------------------- #
def test_a_generated_pool_loads_into_the_production_pool_class():
    """Not an adapter that mimics the runtime — the runtime's own class."""
    from services.orchestrator.pool import Pool

    defn, _ = _generated()
    runtime_pool, plan = preview_service.build_pool(defn)
    assert isinstance(runtime_pool, Pool)
    assert len(runtime_pool.items) == len(defn.questions)


def test_generated_items_satisfy_the_candidate_item_contract():
    """Everything the orchestrator reads off an item has to be there."""
    defn, _ = _generated()
    runtime_pool, _ = preview_service.build_pool(defn)
    for item in runtime_pool.items:
        assert item.prompt.strip()
        assert item.competency
        assert item.difficulty in ("easy", "medium", "hard")
        assert item.looking_for, "the classifier reads this to decide covered/missing"
        assert item.clarify, "the clarify path needs authored text"
        if item.probe_eligible:
            assert item.probe_bank, "the probe fallback path needs somewhere to fall back to"
        assert item.time_estimate_sec > 0


def test_the_running_order_preview_uses_the_production_selection_algorithm():
    """§28. The preview and the live interview must not be two algorithms."""
    from services.orchestrator.pool import Pool

    defn, _ = _generated()
    order = preview_service.running_order(defn)

    runtime = preview_service.as_runtime_definition(defn)
    direct = Pool.from_definition(runtime)
    expected = [
        i.id for i in direct.running_order(direct.plan_from_definition(runtime))
    ]
    assert [e["question_id"] for e in order] == expected


def test_the_preview_opens_with_a_warm_up():
    defn, _ = _generated()
    order = preview_service.running_order(defn)
    assert order[0]["difficulty"] == "easy"


def test_the_preview_gives_high_priority_skills_more_airtime():
    defn, _ = _generated()
    coverage = {c["skill_name"]: c for c in preview_service.coverage_preview(defn)}
    high = max(c["asked"] for c in coverage.values() if c["priority"] == "high")
    low = max(c["asked"] for c in coverage.values() if c["priority"] == "low")
    assert high >= low


def test_the_preview_is_deterministic():
    defn, _ = _generated()
    a = [e["question_id"] for e in preview_service.running_order(defn)]
    b = [e["question_id"] for e in preview_service.running_order(defn)]
    assert a == b


def test_generating_questions_does_not_touch_the_candidate_pool():
    """§29. The candidate still sits the authored pool until publication."""
    from services.orchestrator.pool import get_pool

    before = [i.id for i in get_pool().items]
    _generated()
    assert [i.id for i in get_pool().items] == before
