"""Interview versioning — the guarantee, tested rather than asserted.

The rule this file defends: **a candidate is judged against the interview as it
was when they were invited, whatever the recruiter has done to it since.**

Without a test, versioning is a data model. With one, it is a property.
"""
from __future__ import annotations

import pytest

from packages.types import DefinitionError, InterviewDefinition
from services.data import interviews, versions
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import Skill


def _config(pool, budget: int = 8) -> InterviewConfig:
    return interviews.save(
        InterviewConfig(
            id="iv_test",
            title="Test interview",
            role=pool.role,
            role_title=pool.role_title,
            question_budget=budget,
            skills=[
                Skill(
                    name=c.label,
                    competency_id=c.id,
                    priority="high" if c.weight >= 0.18 else "medium",
                    evaluated=True,
                    pool_competency=c.id,
                )
                for c in pool.competencies
            ],
        )
    )


def test_publishing_freezes_the_definition(data_dir, pool):
    cfg = _config(pool)
    v1 = interviews.publish(cfg, pool)

    assert v1.version == 1
    assert v1.status == "published"
    assert v1.checksum
    assert len(v1.definition["questions"]) == len(pool.items)


def test_editing_after_publish_creates_a_new_version_and_leaves_the_old_one_alone(
    data_dir, pool
):
    cfg = _config(pool, budget=8)
    v1 = interviews.publish(cfg, pool)
    v1_questions = versions.definition_for("iv_test", 1).runtime.question_budget

    # The recruiter changes their mind after candidates are already invited.
    cfg.question_budget = 4
    interviews.save(cfg)
    v2 = interviews.publish(cfg, pool)

    assert v2.version == 2
    assert versions.definition_for("iv_test", 1).runtime.question_budget == v1_questions == 8
    assert versions.definition_for("iv_test", 2).runtime.question_budget == 4
    assert versions.get("iv_test", 1).checksum == v1.checksum, "v1 was rewritten"


def test_republishing_an_unchanged_config_does_not_mint_a_duplicate(data_dir, pool):
    """Pressing Publish twice has not created a second interview."""
    cfg = _config(pool)
    first = interviews.publish(cfg, pool)
    second = interviews.publish(cfg, pool)

    assert second.version == first.version == 1
    assert len(versions.list_for("iv_test")) == 1


def test_a_session_pinned_to_v1_never_sees_v2(data_dir, pool):
    """The property that matters, end to end through the orchestrator."""
    from services.orchestrator.engine import Orchestrator
    from services.orchestrator.state import SessionState

    cfg = _config(pool, budget=8)
    interviews.publish(cfg, pool)

    state = SessionState.new("Candidate A", "cand_a", pool.role, interview_id="iv_test",
                             interview_version=1)
    orch = Orchestrator()
    before = orch._plan(state)

    # Recruiter republishes a much narrower interview mid-morning.
    cfg.question_budget = 3
    for s in cfg.skills[2:]:
        s.evaluated = False
    interviews.save(cfg)
    v2 = interviews.publish(cfg, pool)
    assert v2.version == 2

    after = orch._plan(state)
    assert after.budget == before.budget == 8
    assert after.allowed == before.allowed
    assert after.weights == before.weights


def test_a_session_pinned_to_v2_sees_v2(data_dir, pool):
    """The mirror of the above — pinning must not mean 'always the first one'."""
    from services.orchestrator.engine import Orchestrator
    from services.orchestrator.state import SessionState

    cfg = _config(pool, budget=8)
    interviews.publish(cfg, pool)
    cfg.question_budget = 3
    interviews.save(cfg)
    interviews.publish(cfg, pool)

    orch = Orchestrator()
    a = SessionState.new("A", "a", pool.role, interview_id="iv_test", interview_version=1)
    b = SessionState.new("B", "b", pool.role, interview_id="iv_test", interview_version=2)

    assert orch._plan(a).budget == 8
    assert orch._plan(b).budget == 3


def test_an_uninterviewable_definition_is_refused_at_publish_not_at_runtime(data_dir, pool):
    cfg = _config(pool)
    for s in cfg.skills:
        s.pool_competency = ""   # nothing can ask about any of them

    with pytest.raises(DefinitionError) as exc:
        interviews.publish(cfg, pool)
    assert "question bank" in str(exc.value)
    assert versions.latest_published("iv_test") is None


def test_a_definition_survives_a_round_trip_unchanged(data_dir, pool):
    cfg = _config(pool)
    original = interviews.build_definition(cfg, pool)
    restored = InterviewDefinition.from_dict(original.to_dict())

    assert restored.checksum() == original.checksum()
    assert [q.id for q in restored.questions] == [q.id for q in original.questions]
    assert restored.bank_weights() == original.bank_weights()
