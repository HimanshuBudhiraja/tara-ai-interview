"""The evaluator benchmark's own tests.

Two things live here, and they answer different questions.

**Mode A (default).** Are the gold labels internally consistent with the rules
the engine computes in code, and do the deterministic rules still hold? This
runs on every commit and calls no provider. A benchmark with a wrong case in it
will condemn a correct evaluator, so the dataset is checked before it is ever
allowed to judge a model.

**Mode B (`pytest -m live`).** Does the real configured model actually decide
correctly? Opt-in, following the repository's existing convention for tests that
need something a laptop does not have — and here, that spend money.
"""
from __future__ import annotations

import pytest

from evals import benchmark as B
from packages.types.evaluation import CRITERIA, DIMENSIONS
from services.evaluation import evaluator as E
from services.evaluation import transcript as T


# --------------------------------------------------------------------------- #
#  The dataset
# --------------------------------------------------------------------------- #
#: Pinned so the dataset cannot shrink unnoticed — a benchmark that quietly
#: loses cases stops being an oracle. Raise it deliberately when cases are added.
EXPECTED_CASES = 47


def test_the_dataset_loads_and_is_the_size_it_claims():
    cases = B.cases()
    assert len(cases) == EXPECTED_CASES
    assert len({c["case_id"] for c in cases}) == len(cases)
    assert B.load()["engine_contract"] == "deep_evidence_v1"


def test_every_criterion_is_the_focus_of_at_least_one_case():
    """§4 — the previous real runs never exercised Clarity or Communication."""
    focused = {c for case in B.cases() for c in (case.get("focus") or [])}
    assert set(CRITERIA) <= focused, sorted(set(CRITERIA) - focused)


def test_every_evidence_dimension_is_expected_by_at_least_one_case():
    expected = {
        d for case in B.cases()
        for d in case["expect"].get("dimensions_any_of", [])
    }
    assert set(DIMENSIONS) <= expected, sorted(set(DIMENSIONS) - expected)


def test_all_three_discussion_statuses_are_covered():
    statuses = {c["expect"].get("discussion_status") for c in B.cases()}
    assert {"discussed", "mentioned", "not_discussed"} <= statuses


def test_all_four_depth_combinations_are_covered():
    pairs = {
        (c["expect"].get("depth_reached"), c["expect"].get("depth_demonstrated"))
        for c in B.cases()
    }
    for combination in (("direct", "direct"), ("direct", "deep_probed"),
                        ("deep_probed", "direct"), ("deep_probed", "deep_probed")):
        assert combination in pairs, combination


def test_the_benchmark_is_not_one_interview_on_one_skill():
    """§18 — a benchmark that repeats itself measures one thing well and nothing else."""
    cases = B.cases()
    skills = {c["skill"]["name"] for c in cases}
    assert len(skills) >= 8, sorted(skills)
    # No single skill dominates.
    for skill in skills:
        share = sum(1 for c in cases if c["skill"]["name"] == skill) / len(cases)
        assert share < 0.25, f"{skill} is {share:.0%} of the benchmark"


def test_both_matched_pairs_exist_and_point_at_each_other():
    by_id = {c["case_id"]: c for c in B.cases()}
    pairs = [
        (c["case_id"], key, c["expect"][key])
        for c in B.cases()
        for key in ("must_score_no_higher_than", "must_score_at_least",
                    "must_score_above")
        if key in c["expect"]
    ]
    assert len(pairs) >= 2
    for _, _, other in pairs:
        assert other in by_id


def test_every_case_carries_a_reason_a_reviewer_can_disagree_with():
    for case in B.cases():
        assert len(case["reason"]) > 60, case["case_id"]


# --------------------------------------------------------------------------- #
#  Mode A — the deterministic rules
# --------------------------------------------------------------------------- #
def test_the_gold_labels_agree_with_the_deterministic_rules():
    result = B.run_deterministic()
    assert result["findings"] == [], result["findings"]
    assert result["cases_checked"] == EXPECTED_CASES


@pytest.mark.parametrize("case", B.RECOMMENDATION_CASES, ids=lambda c: c["case_id"])
def test_the_recommendation_rules_are_unchanged(case):
    """§12 — deterministic, so tested directly rather than asked of a model."""
    results, findings = B.run_recommendations()
    mine = next(r for r in results if r["case_id"] == case["case_id"])
    assert mine["passed"], f"{mine['expected']!r} != {mine['actual']!r} — {case['reason']}"
    assert mine["explanation_is_qualitative"], "a number reached the explanation"
    assert not [f for f in findings if f.case_id == case["case_id"]]


def test_a_severe_weakness_still_blocks_a_proceed():
    """The `any_severe` guard, named separately because it is the easiest to lose."""
    mine = next(c for c in B.RECOMMENDATION_CASES
                if c["case_id"] == "rec-06-severe-weakness-blocks-proceed")
    results, _ = B.run_recommendations()
    assert next(r for r in results
                if r["case_id"] == mine["case_id"])["actual"] == "Needs further evaluation"


# --------------------------------------------------------------------------- #
#  Case construction — the harness itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", B.cases(), ids=lambda c: c["case_id"])
def test_every_case_builds_a_runnable_interview(case):
    definition, state = B.build_interview(case)
    definition.require_valid()
    assert state.phase == "complete"
    target = case["expect"].get("target_skill") or case["skill"]["id"]
    assert target in {s.id for s in definition.skills}

    transcript = T.build(state, definition)
    questions = transcript.for_skill(target)
    if case["expect"].get("discussion_status") == "not_discussed":
        # Two routes, and the benchmark exercises both:
        #   * the skill is listed on the published version and never asked, or
        #   * it was asked and every turn came back unusable — an empty answer,
        #     or one the injection scanner flagged, which cannot become evidence.
        assert not questions or not any(q.answered for q in questions), (
            "a not_discussed case must have no question for the skill, or no usable turn"
        )
    else:
        assert questions
        assert any(q.answered for q in questions)


@pytest.mark.parametrize(
    "case",
    [c for c in B.cases() if "depth_reached" in c["expect"]],
    ids=lambda c: c["case_id"],
)
def test_depth_reached_is_computed_from_the_transcript_not_the_label(case):
    definition, state = B.build_interview(case)
    transcript = T.build(state, definition)
    target = case["expect"].get("target_skill") or case["skill"]["id"]
    assert transcript.depth_reached_for(target) == case["expect"]["depth_reached"]


def test_an_undiscussed_skill_scores_nothing_without_any_model_call():
    """The floor, with the model removed entirely."""
    case = next(c for c in B.cases() if c["expect"].get("criteria_all_zero"))
    definition, state = B.build_interview(case)
    transcript = T.build(state, definition)
    target = case["expect"]["target_skill"]
    skill = next(s for s in definition.skills if s.id == target)

    assert E.discussion_status_for(skill, transcript, []) == "not_discussed"
    row = E.assess_skill(skill, [], transcript, "Senior", E.EvaluationReport(),
                         judge=lambda *a, **k: pytest.fail("the judge must not be called"))
    assert row.score == 0
    assert all(v == 0 for v in row.criteria().values())
    assert row.remarks == "Not discussed in interview"


# --------------------------------------------------------------------------- #
#  Mode B — the real model. Opt-in.
# --------------------------------------------------------------------------- #
#: Checks that were 100% correct in EVERY one of three identical full runs.
#: These are the guardrails and the deterministic derivations, and the gate has
#: no tolerance for them: a failure here is a regression, not variance.
STABLE_CHECKS: frozenset[str] = frozenset({
    "discussion_status", "undiscussed_scores_zero", "remarks_exact", "mentioned_cap",
    "criterion_vocabulary", "dimension_vocabulary", "quote_verbatim",
    "no_evidence_matching", "no_criterion_above", "evidence_on_target",
    "no_evidence_on", "second_skill_status", "independent_rows", "depth_reached",
    "skill_present", "provider", "pair:stt_penalty:Clarity",
    "pair:stt_penalty:Communication",
})

#: Material failures observed across three identical runs of the full benchmark:
#: 4, 8 and 6. Three cases failed in all three; six were intermittent. The
#: ceiling is a REGRESSION TRIPWIRE with headroom over the observed maximum, not
#: a quality claim — see EVALUATOR_CALIBRATION.md and BUILD_STATUS.md.
#:
#: An allowlist of case ids was tried first and abandoned: it assumes the failing
#: set is stable, and it is not. A gate that goes red for a different reason on
#: every run teaches people to ignore it.
OBSERVED_MATERIAL_CEILING = 10


@pytest.mark.live
def test_the_guardrails_never_fail_and_calibration_stays_in_its_measured_band():
    """The calibration gate, set from what three identical runs actually did.

    Two tiers, because the run-to-run behaviour genuinely has two tiers.

    **Zero tolerance.** Every check in `STABLE_CHECKS` was correct in all three
    runs: no undiscussed skill scored, no evidence on a skill the question did
    not test, no fabricated quote, no protected content quoted, no criterion
    named from the dimension list, no injected demand honoured, and no
    transcription artefact costing a candidate a point. A failure here is a
    regression and fails the build.

    **Measured band.** `depth_demonstrated`, the criterion ranges and the skill
    totals move between identical runs — 0.895 / 0.816 / 0.842 on demonstrated
    depth, and 4 / 8 / 6 material errors overall. That instability is itself the
    headline finding of this phase, so the assertion is a tripwire above the
    observed maximum rather than a claim that the calibration is good.

    Neither tier is a product requirement. They are what the evaluator measurably
    does today, written down so a change to it is visible.
    """
    from services import config

    if not config.llm_is_live():
        pytest.skip("no provider configured")

    result = B.run_live()
    B.write(result, "evaluator_benchmark_live.json")
    material = [f for f in result["findings"] if f["severity"] == B.CRITICAL]

    guardrail_failures = [f for f in material if f["check"] in STABLE_CHECKS]
    assert not guardrail_failures, (
        "a check that has never failed just failed:\n" + "\n".join(
            f"  {f['case_id']} {f['check']}: expected {f['expected']!r}, "
            f"got {f['actual']!r} [{f['failure_class']}] {f['reason']}"
            for f in guardrail_failures
        )
    )

    assert len(material) <= OBSERVED_MATERIAL_CEILING, (
        f"{len(material)} material errors, above the observed band of 4-8:\n"
        + "\n".join(f"  {f['case_id']} {f['check']}" for f in material)
    )
    assert result["telemetry"]["silent_model_fallback"] is False
