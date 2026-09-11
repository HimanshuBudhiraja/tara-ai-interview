"""The interview contract: task → skill mapping, and what the runtime derives.

§7's claim is that the orchestrator cannot tell where a question came from. That
is only true if a definition built from the authored pool produces exactly the
interview the pool produced directly — which is what most of this file checks.
"""
from __future__ import annotations

from packages.types.definition import (
    BankSpec,
    InterviewDefinition,
    QuestionSpec,
    SkillSpec,
    TaskSpec,
    derive_bank_min_items,
    derive_bank_weights,
)
from services.data import interviews
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import Skill, Task


# --------------------------------------------------------------------------- #
#  Task → assesses → Skill
# --------------------------------------------------------------------------- #
def test_tasks_carry_the_skills_they_assess(data_dir, pool):
    cfg = InterviewConfig(
        id="iv_map",
        title="Mapping",
        role=pool.role,
        skills=[
            Skill(name="Empathy", competency_id="empathy", evaluated=True,
                  pool_competency="empathy"),
            Skill(name="De-escalation", competency_id="deescalation", evaluated=True,
                  pool_competency="deescalation"),
            Skill(name="Communication clarity", competency_id="clarity", evaluated=True,
                  pool_competency="clarity"),
        ],
        tasks=[
            Task(
                description="Handle an escalated customer complaint",
                required_skills=["empathy", "deescalation", "clarity"],
                outcome="Customers leave with the problem solved.",
            ),
            Task(description="Write a handover note", required_skills=["clarity"]),
        ],
    )
    defn = interviews.build_definition(cfg, pool)

    escalation = next(t for t in defn.tasks if "escalated" in t.description)
    assert set(escalation.skill_ids) == {"empathy", "deescalation", "clarity"}

    # And the mapping is navigable in both directions — a skill knows which
    # tasks ground it, which is what the review screen shows a hiring manager.
    clarity = defn.skill("clarity")
    assert len(clarity.task_ids) == 2
    empathy = defn.skill("empathy")
    assert len(empathy.task_ids) == 1


def test_a_task_requiring_an_unknown_skill_is_caught_by_validation():
    defn = InterviewDefinition(
        skills=[SkillSpec(id="empathy", name="Empathy", question_bank="empathy")],
        tasks=[TaskSpec(id="t1", description="Escalate well", skill_ids=["empathy", "ghost"])],
        questions=[QuestionSpec(id="q1", question_text="Tell me about a time…",
                                competency="empathy", probe_bank=["And then?"])],
        banks=[BankSpec(id="empathy", label="Customer empathy")],
    )
    problems = defn.validate()
    assert any("ghost" in p for p in problems)


# --------------------------------------------------------------------------- #
#  Priority bands → the distribution selection needs
# --------------------------------------------------------------------------- #
def test_priority_bands_become_a_distribution_that_sums_to_one():
    weights = derive_bank_weights(
        [("empathy", "high"), ("deescalation", "medium"), ("clarity", "low")]
    )
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights["empathy"] > weights["deescalation"] > weights["clarity"]


def test_skills_sharing_a_bank_add_their_ranks():
    """A bank answering two high-priority skills matters more than one
    answering a single one."""
    one = derive_bank_weights([("empathy", "high"), ("clarity", "high")])
    two = derive_bank_weights(
        [("empathy", "high"), ("empathy", "high"), ("clarity", "high")]
    )
    assert two["empathy"] > one["empathy"]


def test_high_priority_skills_ask_for_two_questions():
    """A skill the job hangs on should not rest on a single answer."""
    mins = derive_bank_min_items([("empathy", "high"), ("clarity", "medium")])
    assert mins == {"empathy": 2, "clarity": 1}


def test_the_console_preview_and_the_published_contract_agree(data_dir, pool):
    """Two callers derive the distribution; if they disagreed, the recruiter's
    preview would show a question order no candidate ever gets."""
    cfg = InterviewConfig(
        id="iv_agree", title="Agree", role=pool.role,
        skills=[
            Skill(name=c.label, competency_id=c.id, evaluated=True, pool_competency=c.id,
                  priority="high" if c.weight >= 0.18 else "low")
            for c in pool.competencies
        ],
    )
    defn = interviews.build_definition(cfg, pool)
    assert cfg.pool_weights() == defn.bank_weights()
    assert cfg.pool_min_items() == defn.bank_min_items()


# --------------------------------------------------------------------------- #
#  The seam itself
# --------------------------------------------------------------------------- #
def test_a_pool_rebuilt_from_a_definition_asks_the_identical_interview(data_dir, pool):
    """The §7 property: the orchestrator cannot tell the difference."""
    from services.orchestrator.pool import Pool

    cfg = InterviewConfig(
        id="iv_seam", title="Seam", role=pool.role,
        skills=[
            Skill(name=c.label, competency_id=c.id, evaluated=True, pool_competency=c.id,
                  priority="high" if c.weight >= 0.18 else "medium")
            for c in pool.competencies
        ],
    )
    defn = interviews.build_definition(cfg, pool)
    rebuilt = Pool.from_definition(defn)

    direct = [i.id for i in pool.running_order(pool.plan(cfg))]
    through_contract = [
        i.id for i in rebuilt.running_order(rebuilt.plan_from_definition(defn))
    ]
    assert through_contract == direct
    assert len(direct) == cfg.question_budget


def test_a_question_keeps_its_authored_probe_bank_and_clarify_through_the_contract(
    data_dir, pool
):
    """The candidate-facing authored text must survive the round trip — those
    are the fallbacks that keep a rejected probe from becoming silence."""
    from services.orchestrator.pool import Pool

    cfg = InterviewConfig(
        id="iv_text", title="Text", role=pool.role,
        skills=[Skill(name=c.label, competency_id=c.id, evaluated=True, pool_competency=c.id)
                for c in pool.competencies],
    )
    rebuilt = Pool.from_definition(interviews.build_definition(cfg, pool))

    for original in pool.items:
        carried = rebuilt.item(original.id)
        assert carried.prompt == original.prompt
        assert carried.probe_bank == original.probe_bank
        assert carried.clarify == original.clarify
        assert carried.looking_for == original.looking_for
        assert carried.probe_eligible == original.probe_eligible
