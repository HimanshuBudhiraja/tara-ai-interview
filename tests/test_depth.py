"""`depth_demonstrated`: what the candidate showed, not what Tara asked.

The rule under test is `evaluator.depth_demonstrated_from`, which reads three
labels per evidence item — dimension, type, strength — and returns the deepest
stage the evidence supports. Nothing else. It cannot see the probe count, the
answer's length, the criteria, the score, the skill's name or the candidate's
experience, and the tests below are mostly demonstrations that it cannot.

Everything here is deterministic. Whether a real model TAGS an answer correctly
is the depth benchmark's question (`evals/depth.py`, `python -m evals.depth
--live`); this file pins down what a tag MEANS once it exists, so a change to
the meaning is a visible change here rather than a quiet one in production.
"""
from __future__ import annotations

import itertools

import pytest

from packages.types.evaluation import (
    DIMENSIONS,
    STAGES,
    EvidenceItem,
    stage_index,
)
from services.evaluation import evaluator as E


def item(
    dimension: str,
    strength: str = "strong",
    evidence_type: str = "supported",
    *,
    stage: str = "direct",
) -> EvidenceItem:
    """One evidence item. `depth_stage` is the rung it was SAID on, and is set
    deliberately here so the tests can prove the derivation ignores it."""
    return EvidenceItem(
        skill_id="skl_x", skill_name="Skill",
        question_id="q1", turn_id="q1#0",
        depth_stage=stage,
        depth_dimension=dimension,
        candidate_quote="what they said",
        evidence_type=evidence_type,
        evidence_strength=strength,
        supports_criterion="Depth",
    )


# --------------------------------------------------------------------------- #
#  The rubric: which kind of evidence demonstrates which stage
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dimension,expected", [
    # Direct — they know what it is and can say it correctly.
    ("conceptual_understanding", "direct"),
    # Probed — they can apply it, or explain the logic of a choice.
    ("practical_application", "probed"),
    ("reasoning", "probed"),
    # `trade_offs` sits here too, and the reason is measured: promoting it to
    # the deep tier fixed three depth cases and broke five gold-benchmark ones,
    # because `evidence_strength` does not reliably separate a weighed
    # alternative from a mentioned one on this model. See `DIMENSION_STAGE`.
    ("trade_offs", "probed"),
    # Deep — what it costs and what breaks.
    ("edge_cases", "deep_probed"),
    ("production_judgment", "deep_probed"),
])
def test_each_dimension_demonstrates_its_own_stage(dimension, expected):
    assert E.depth_demonstrated_from([item(dimension)]) == expected


def test_every_dimension_has_a_stage_and_the_table_is_complete():
    """A dimension with no entry would silently demonstrate `direct`."""
    assert set(E.DIMENSION_STAGE) == set(DIMENSIONS)
    assert set(E.DIMENSION_STAGE.values()) <= set(STAGES)


def test_nothing_demonstrated_is_direct_rather_than_missing():
    """The floor is a stage, not an absence: a skill with no evidence has still
    been asked about, and `direct` is the honest bottom of the ladder."""
    assert E.depth_demonstrated_from([]) == "direct"


# --------------------------------------------------------------------------- #
#  Independence from the probe ladder — the property the whole split exists for
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said_on", STAGES)
def test_the_rung_an_answer_was_said_on_changes_nothing(said_on):
    """Same evidence, said on the first rung or the third: same demonstrated
    depth. `depth_stage` is on the item and the derivation never reads it."""
    for dimension in DIMENSIONS:
        deep_answer = E.depth_demonstrated_from([item(dimension, stage=said_on)])
        assert deep_answer == E.DIMENSION_STAGE[dimension]


def test_a_direct_answer_can_demonstrate_the_deepest_stage():
    """The saturation case: nothing was probed because nothing needed to be."""
    assert E.depth_demonstrated_from([
        item("production_judgment", stage="direct"),
    ]) == "deep_probed"


def test_being_probed_twice_demonstrates_nothing_by_itself():
    """The mirror: Tara went to the bottom of the ladder and the candidate
    stayed at the top. Three turns of conceptual evidence is still `direct`."""
    assert E.depth_demonstrated_from([
        item("conceptual_understanding", stage="direct"),
        item("conceptual_understanding", stage="probed"),
        item("conceptual_understanding", stage="deep_probed"),
    ]) == "direct"


@pytest.mark.parametrize("reached", STAGES)
def test_demonstrated_depth_is_never_a_function_of_reached_depth(reached):
    """Stated as a property rather than a case: for every rung the interview
    could have got to, the demonstrated figure is decided by the evidence."""
    shallow = [item("conceptual_understanding", stage=reached)]
    deep = [item("edge_cases", stage=reached)]
    assert E.depth_demonstrated_from(shallow) == "direct"
    assert E.depth_demonstrated_from(deep) == "deep_probed"


# --------------------------------------------------------------------------- #
#  Strength and type: a claim about depth needs evidence firm enough to carry it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dimension", [d for d in DIMENSIONS if d != "conceptual_understanding"])
def test_anything_deeper_than_direct_needs_strong_evidence(dimension):
    assert E.depth_demonstrated_from([item(dimension, "moderate")]) == "direct"
    assert E.depth_demonstrated_from([item(dimension, "weak")]) == "direct"
    assert E.depth_demonstrated_from([item(dimension, "strong")]) != "direct"


def test_weak_evidence_carries_no_stage_at_all():
    assert E.depth_demonstrated_from([item("conceptual_understanding", "weak")]) == "direct"
    assert E.depth_demonstrated_from([
        item("edge_cases", "weak"), item("production_judgment", "weak"),
    ]) == "direct"


@pytest.mark.parametrize("evidence_type", ["partial", "unclear", "contradicted", "missing"])
def test_only_supported_evidence_demonstrates_anything(evidence_type):
    """Gesturing at a failure mode is not demonstrating failure-mode thinking,
    and a claim the candidate later withdrew establishes nothing at all."""
    assert E.depth_demonstrated_from([
        item("production_judgment", "strong", evidence_type)
    ]) == "direct"


# --------------------------------------------------------------------------- #
#  §18 — aggregation across several items
# --------------------------------------------------------------------------- #
def test_the_deepest_qualifying_item_decides_the_skill():
    """The documented rule: the strongest valid evidence, not an average and not
    a count. A candidate who demonstrated a failure mode once has demonstrated
    it, and three conceptual sentences beside it do not undo that."""
    assert E.depth_demonstrated_from([
        item("conceptual_understanding"),
        item("practical_application"),
        item("edge_cases"),
    ]) == "deep_probed"


def test_the_order_of_the_evidence_does_not_matter():
    rows = [item("conceptual_understanding"), item("reasoning"), item("edge_cases")]
    for permutation in itertools.permutations(rows):
        assert E.depth_demonstrated_from(list(permutation)) == "deep_probed"


def test_more_evidence_of_the_same_kind_is_not_more_depth():
    """Counting items would make a talkative candidate a deep one."""
    one = E.depth_demonstrated_from([item("practical_application")])
    many = E.depth_demonstrated_from([item("practical_application")] * 6)
    assert one == many == "probed"


def test_a_weak_item_cannot_erase_a_strong_one():
    """Aggregation is not an average: a noisy weak tag beside real deep evidence
    must not pull the skill back down."""
    assert E.depth_demonstrated_from([
        item("edge_cases", "strong"),
        item("conceptual_understanding", "weak"),
        item("practical_application", "weak"),
    ]) == "deep_probed"


def test_a_contradicted_deep_item_does_not_stop_the_rest_counting():
    """§19: a withdrawn claim is dropped, and what the candidate actually did
    still counts. The trade-off they retracted carries nothing; the thing they
    built is still practical application."""
    assert E.depth_demonstrated_from([
        item("production_judgment", "strong", "contradicted"),
        item("practical_application", "strong"),
    ]) == "probed"


def test_a_self_correction_leaves_the_corrected_account_standing():
    """The other shape of contradiction: the first answer was wrong and the
    candidate fixed it. The corrected evidence is what counts."""
    assert E.depth_demonstrated_from([
        item("conceptual_understanding", "moderate", "contradicted"),
        item("practical_application", "strong"),
        item("edge_cases", "strong"),
    ]) == "deep_probed"


# --------------------------------------------------------------------------- #
#  §17 — depth is not the score wearing a different name
# --------------------------------------------------------------------------- #
def test_depth_reads_nothing_but_the_evidence_labels():
    """No criterion, no total, no expectation is an input. Written as a
    signature check because the guarantee is structural: if a score ever became
    an argument, this is where it would show up."""
    import inspect

    signature = inspect.signature(E.depth_demonstrated_from)
    assert list(signature.parameters) == ["evidence"]


def test_a_high_scoring_answer_can_demonstrate_only_direct_depth():
    """Accuracy 5 with conceptual evidence only: a textbook-perfect answer.
    Depth follows the evidence, so it stays `direct` — and the score is free to
    be high, which is the separation this test exists to keep."""
    from packages.types.evaluation import SkillAssessment

    row = SkillAssessment(
        skill_name="Skill", discussion_status="discussed",
        accuracy=5, depth=3, clarity=5, problem_solving=3, communication=5,
        remarks="Correct throughout.",
        # `score` is the sum the engine writes onto the row; it is a stored field
        # rather than a property, so the fixture sets it explicitly.
        score=21,
    )
    assert row.score == sum(row.criteria().values()) == 21
    assert E.depth_demonstrated_from([item("conceptual_understanding")]) == "direct"


def test_deep_evidence_does_not_require_a_high_score():
    """And the other direction: a candidate can name a failure mode inside an
    otherwise weak answer. Depth records that they did."""
    assert E.depth_demonstrated_from([item("edge_cases")]) == "deep_probed"


# --------------------------------------------------------------------------- #
#  The two figures, side by side on the row a recruiter reads
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("reached,demonstrated", list(itertools.product(STAGES, STAGES)))
def test_every_combination_of_the_two_figures_is_representable(reached, demonstrated):
    """All nine, including the three the product cares most about: probed hard
    and showed nothing, asked once and showed everything, and the two agreeing."""
    from packages.types.evaluation import DepthEvaluation

    depth = DepthEvaluation(depth_reached=reached, depth_demonstrated=demonstrated)
    payload = depth.to_dict()
    assert payload["depth_reached"] == reached
    assert payload["depth_demonstrated"] == demonstrated
    # Neither is derived from the other, so the pair survives a round trip in
    # any combination — including demonstrated deeper than reached.
    assert stage_index(payload["depth_demonstrated"]) == stage_index(demonstrated)


def test_the_stage_vocabulary_is_shared_and_ordered():
    """One ladder, three rungs, shallowest first. Both figures use it, and the
    order is what `deeper_of` means."""
    assert STAGES == ("direct", "probed", "deep_probed")
    assert [stage_index(s) for s in STAGES] == [0, 1, 2]


# --------------------------------------------------------------------------- #
#  The one known cap, pinned so it cannot change without being noticed
# --------------------------------------------------------------------------- #
def test_a_trade_off_alone_is_recorded_as_probed_and_this_is_known():
    """A measured limitation, not an accident.

    An answer whose deepest evidence is a meaningful trade-off — an alternative
    weighed with the cost named — is recorded as `probed`. The rubric would call
    that deep. Letting it through cost five gold-benchmark cases, because the
    extractor also tags `trade_offs` on answers containing no alternative at
    all, so the cap is the lesser error. The trade-off itself is still visible
    on the recruiter's row under demonstrated evidence.
    """
    assert E.depth_demonstrated_from([item("trade_offs", "strong")]) == "probed"
    # And it is not lost: the dimension is reported beside the figure.
    shown, missing = E.dimensions_split([item("trade_offs", "strong")])
    assert shown == ["trade_offs"]
    assert "trade_offs" not in missing


# --------------------------------------------------------------------------- #
#  Repeatability of the deterministic half
# --------------------------------------------------------------------------- #
def test_the_derivation_is_deterministic_over_repeated_calls():
    """The model's labels move between runs; the rule applied to them must not.

    Measured on the extraction side: 40 of 46 depth cases keep their label
    across five identical provider runs. That residual belongs to the
    extractor. This asserts the other half contributes none of it.
    """
    evidence = [
        item("conceptual_understanding"),
        item("practical_application", "moderate"),
        item("edge_cases", "strong"),
        item("trade_offs", "strong"),
        item("production_judgment", "weak"),
    ]
    answers = {E.depth_demonstrated_from(evidence) for _ in range(200)}
    assert answers == {"deep_probed"}

    shuffled = list(reversed(evidence))
    assert E.depth_demonstrated_from(shuffled) == E.depth_demonstrated_from(evidence)


@pytest.mark.parametrize("dimension", list(DIMENSIONS))
def test_one_item_of_each_dimension_derives_the_same_stage_every_time(dimension):
    stages = {E.depth_demonstrated_from([item(dimension)]) for _ in range(50)}
    assert len(stages) == 1


# --------------------------------------------------------------------------- #
#  The derivation variants that were measured and rejected
# --------------------------------------------------------------------------- #
def test_a_meaningful_trade_off_alone_stays_at_probed_and_the_reason_is_recorded():
    """Promoting `trade_offs` to the deep tier was measured over 15 real runs
    and rejected twice over.

    Re-derived from the stored labels of five runs per configuration, the
    promotion raised exact depth agreement (0.665 → 0.709 on the shipped
    prompt, 0.743 → 0.791 on the candidate one) and broke contradiction
    handling (0.80 → 0.30, and 0.70 → 0.50) — a retracted trade-off carried the
    deepest stage. Phase 17 had already measured it costing five gold-benchmark
    cases. Two independent costs against one benefit, so the cap stays.
    """
    assert E.DIMENSION_STAGE["trade_offs"] == "probed"
    assert E.depth_demonstrated_from([item("trade_offs", "strong")]) == "probed"
    # The deep tier is reachable, just not by a trade-off on its own.
    assert E.depth_demonstrated_from([
        item("trade_offs", "strong"), item("production_judgment", "strong"),
    ]) == "deep_probed"


def test_a_withdrawn_trade_off_carries_nothing_whatever_the_tier():
    """The contradiction case the promotion broke, asserted directly."""
    assert E.depth_demonstrated_from([
        item("trade_offs", "strong", "contradicted"),
        item("practical_application", "strong"),
    ]) == "probed"


def test_requiring_two_deep_items_was_measured_and_is_not_the_rule():
    """The other candidate: make the deep tier need two strong deep items. It
    measured 0.587-0.626 against 0.665-0.743 for the shipped rule, so a single
    genuine failure mode still establishes deep depth."""
    assert E.depth_demonstrated_from([item("edge_cases", "strong")]) == "deep_probed"
