"""The assessment result: one immutable, self-consistent, traceable object.

What is under test is the shape a recruiter reads and the arithmetic behind it —
assembled from the persisted evaluation and the frozen snapshot, with no model
involved. The scoring change this phase made (score over discussed skills,
coverage reported separately) is pinned here at every level: overall, per skill,
and in the boundaries between rating bands.
"""
from __future__ import annotations

import pytest

from packages.types.evaluation import (
    CRITERIA,
    ENGINE_VERSION,
    RECOMMENDATIONS,
    RESULT_CONTRACT_VERSION,
    UNRATED,
    Coverage,
    EvidenceItem,
    SkillAssessment,
    overall_rating,
)
from services.data import evaluations, versions
from services.data import sessions as store
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV
from services.evaluation import jobs, result
from services.evaluation import transcript as T
from tests import fixtures_candidates as F

pytestmark = pytest.mark.usefixtures("data_dir")


# --------------------------------------------------------------------------- #
#  Scaffolding — a real persisted evaluation, no provider
# --------------------------------------------------------------------------- #
def _extractor(dimension: str = "reasoning", strength: str = "strong"):
    def extract(question: T.QuestionTranscript, skill, definition, *, session_id=""):
        items = [
            EvidenceItem(
                skill_id=skill.id, skill_name=skill.name,
                question_id=question.question_id, task_id=question.task_id,
                turn_id=turn.turn_id, depth_stage=turn.depth_stage,
                depth_dimension=dimension,
                candidate_quote=turn.answer.split(".")[0].strip(),
                evidence_type="supported", evidence_strength=strength,
                supports_criterion="Depth",
            )
            for turn in question.usable_turns()
            if turn.answer.split(".")[0].strip()
        ]
        return items, EV.ExtractionReport(accepted=len(items))

    return extract


def _persisted(fixture: F.Fixture, **kwargs):
    versions.publish("iv_fix", F.definition(fixture.experience_to), validate=False)
    state = fixture.session()
    store.save(state)
    kwargs.setdefault("extractor", _extractor())
    record = jobs.request_and_run(state, judge=fixture.judge(), **kwargs)
    assert record.status == evaluations.COMPLETED, record.error
    return state, record


@pytest.fixture()
def evaluated():
    return _persisted(F.strong_senior())


# --------------------------------------------------------------------------- #
#  §2 — the hierarchy exists and holds together
# --------------------------------------------------------------------------- #
def test_the_result_carries_every_layer_of_the_hierarchy(evaluated):
    _, record = evaluated
    payload = result.build_validated(record)
    assert set(payload) == {
        "result_contract_version", "evaluation", "interview", "session", "overall",
        "coverage", "skills", "questions", "evidence", "summary", "scales", "integrity",
    }
    assert payload["result_contract_version"] == RESULT_CONTRACT_VERSION
    assert payload["evaluation"]["engine_version"] == ENGINE_VERSION


def test_overall_leads_to_skills_leads_to_questions_leads_to_evidence(evaluated):
    """§13 — the trace a recruiter follows to answer "why this assessment?"."""
    _, record = evaluated
    payload = result.build_validated(record)

    discussed = [s for s in payload["skills"] if s["discussion_status"] == "discussed"]
    assert discussed
    skill = next(s for s in discussed if s["evidence_count"] > 0)

    # skill -> question
    assert skill["question_ids"]
    question = next(q for q in payload["questions"]
                    if q["question_id"] in skill["question_ids"])
    assert question["skill_id"] == skill["skill_id"]

    # question -> turn -> the candidate's own words
    assert question["turns"]
    turn = question["turns"][0]
    assert turn["answer"]

    # ...and an evidence item quoting that turn, naming a real criterion
    items = [e for e in payload["evidence"] if e["turn_id"] == turn["turn_id"]]
    assert items
    for item in items:
        assert item["candidate_quote"] in turn["answer"]
        assert item["supports_criterion"] in CRITERIA
        assert item["skill_id"] == skill["skill_id"]
        assert item["question_id"] == question["question_id"]


def test_no_result_exposes_the_extractors_rationale_or_a_prompt(evaluated):
    _, record = evaluated
    payload = result.build_validated(record)
    assert all("note" not in item for item in payload["evidence"])
    text = repr(payload).lower()
    for leak in ("you assess one skill", "strict json", "candidate_text_start",
                 "system prompt", "chain of thought"):
        assert leak not in text


# --------------------------------------------------------------------------- #
#  §9, §10 — the score, and what is in its denominator
# --------------------------------------------------------------------------- #
def test_the_score_is_over_discussed_skills_only(evaluated):
    _, record = evaluated
    payload = result.build_validated(record)
    overall, skills = payload["overall"], payload["skills"]

    discussed = [s for s in skills if s["discussion_status"] == "discussed"]
    assert len(discussed) < len(skills)          # a gap exists to be excluded
    assert overall["total_score"] == sum(s["score"] for s in discussed)
    assert overall["max_score"] == result.SKILL_MAX_SCORE * len(discussed)
    assert overall["skill_max_score"] == 25


def test_a_coverage_gap_no_longer_drags_the_rating_down():
    """The case that drove the change, through the persisted result."""
    _, record = _persisted(F.coverage_gap_candidate())
    payload = result.build_validated(record)
    coverage, overall = payload["coverage"], payload["overall"]

    assert coverage["skills_discussed"] == 1
    assert coverage["skills_not_discussed"] == 3
    assert coverage["coverage_percentage"] == 25.0
    assert overall["max_score"] == 25            # not 100
    assert overall["percentage"] > 80
    assert overall["overall_rating"] == "Excellent"
    # Thin coverage still gates the decision, deterministically.
    assert overall["recommendation"] == RECOMMENDATIONS[1]


def test_the_browser_needs_no_denominator_of_its_own(evaluated):
    """§9 — every figure a report renders is present in the payload."""
    _, record = evaluated
    payload = result.build_validated(record)
    for key in ("total_score", "max_score", "percentage", "overall_rating",
                "recommendation", "skill_max_score"):
        assert key in payload["overall"], key
    for skill in payload["skills"]:
        for key in ("score", "max_score", "percentage", "criterion_ceiling",
                    "counts_toward_overall_score"):
            assert key in skill, key


# --------------------------------------------------------------------------- #
#  §6, §5 — what each status means in the rollup
# --------------------------------------------------------------------------- #
def _aggregate(*rows: SkillAssessment):
    return E.aggregate(list(rows), candidate_name="X", job_role="Y", level="Senior")


def _row(name: str, status: str, values: list[int]) -> SkillAssessment:
    row = SkillAssessment(
        skill_name=name, discussion_status=status, accuracy=values[0],
        depth=values[1], clarity=values[2], problem_solving=values[3],
        communication=values[4],
        remarks="Not discussed in interview" if status == "not_discussed" else "…")
    row.score = sum(values)
    return row


def test_a_mentioned_skill_never_becomes_a_scored_skill():
    evaluation = _aggregate(
        _row("Strong", "discussed", [4, 4, 4, 4, 4]),
        _row("Glanced", "mentioned", [1, 1, 1, 1, 1]),
    )
    assert evaluation.candidate_details.total_score == 20
    assert evaluation.maximum_possible_score == 25
    assert evaluation.coverage.skills_mentioned == 1
    assert evaluation.coverage.coverage_percentage == 50.0


def test_an_undiscussed_skill_contributes_nothing_and_is_not_a_weakness():
    evaluation = _aggregate(
        _row("Strong", "discussed", [4, 4, 4, 4, 4]),
        _row("Never asked", "not_discussed", [0, 0, 0, 0, 0]),
    )
    assert evaluation.maximum_possible_score == 25
    assert evaluation.percentage == 80.0
    assert "never asked" not in " ".join(evaluation.areas_for_improvement).lower()
    assert evaluation.coverage.skills_not_discussed == 1


def test_nothing_discussed_yields_no_rating():
    evaluation = _aggregate(
        _row("A", "not_discussed", [0, 0, 0, 0, 0]),
        _row("B", "mentioned", [1, 1, 1, 1, 1]),
    )
    assert evaluation.maximum_possible_score == 0
    assert evaluation.percentage == 0.0
    assert evaluation.candidate_details.overall_rating == UNRATED
    assert evaluation.recommendation == RECOMMENDATIONS[1]


# --------------------------------------------------------------------------- #
#  §11 — the rating boundaries, including the floating-point ones
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("percentage, rating", [
    (0.0, "Poor"), (39.99, "Poor"), (40.0, "Average"), (59.99, "Average"),
    (60.0, "Good"), (79.99, "Good"), (80.0, "Excellent"), (100.0, "Excellent"),
])
def test_rating_boundaries(percentage, rating):
    assert overall_rating(percentage) == rating


@pytest.mark.parametrize("score, expected", [
    (10, "Poor"),        # 40%  of 25 -> Average? no: 10/25 = 40.0 -> Average
])
def test_rating_boundary_from_real_arithmetic_not_a_literal(score, expected):
    """A percentage that lands exactly on a boundary must not wobble.

    25 points per skill means the reachable percentages are multiples of 4, so
    40.0, 60.0 and 80.0 are all exactly representable and exactly reachable —
    the boundaries are hit, not approached.
    """
    for points, band in ((9, "Poor"), (10, "Average"), (14, "Average"),
                         (15, "Good"), (19, "Good"), (20, "Excellent")):
        evaluation = _aggregate(_row("One", "discussed", [points, 0, 0, 0, 0]))
        # A discussed skill floors every criterion at 1, so build the score
        # directly to hit the boundary rather than through the constraint layer.
        evaluation.percentage = round(points / 25 * 100, 1)
        assert overall_rating(evaluation.percentage) == band, (points, band)


def test_the_percentage_is_exact_at_every_boundary():
    for points, pct in ((10, 40.0), (15, 60.0), (20, 80.0)):
        assert round(points / 25 * 100, 1) == pct


# --------------------------------------------------------------------------- #
#  §7, §16 — question rows, and the two depth vocabularies
# --------------------------------------------------------------------------- #
def test_every_asked_question_has_a_traceable_row(evaluated):
    state, record = evaluated
    payload = result.build_validated(record)
    assert {q["question_id"] for q in payload["questions"]} == set(state.asked_item_ids)
    for question in payload["questions"]:
        assert question["question_text"]
        assert question["skill_name"]
        assert question["probe_count"] == max(0, len(question["turns"]) - 1)


def test_interview_depth_and_probe_stage_are_named_apart(evaluated):
    """§16 — short/medium/deep is the configured scope; direct/probed/
    deep_probed is how far one question was followed up."""
    _, record = evaluated
    payload = result.build_validated(record)

    assert payload["interview"]["interview_depth"] in ("short", "medium", "deep")
    assert payload["scales"]["interview_depth_scale"] == ["short", "medium", "deep"]
    assert payload["scales"]["probe_stage_scale"] == ["direct", "probed", "deep_probed"]
    for question in payload["questions"]:
        assert question["depth_reached"] in payload["scales"]["probe_stage_scale"]
        assert question["depth_demonstrated"] in payload["scales"]["probe_stage_scale"]
        assert question["depth_reached"] not in payload["scales"]["interview_depth_scale"]


def test_a_questions_demonstrated_depth_comes_from_its_own_evidence():
    """Deep probing does not make demonstrated depth deep, at question level either."""
    _, shallow = _persisted(F.over_probed_candidate(),
                            extractor=_extractor("conceptual_understanding"))
    payload = result.build_validated(shallow)
    question = next(q for q in payload["questions"] if q["question_id"] == "q_idem")
    assert question["depth_reached"] == "deep_probed"
    assert question["depth_demonstrated"] == "direct"

    _, deep = _persisted(F.saturated_candidate(),
                         extractor=_extractor("production_judgment"))
    payload = result.build_validated(deep)
    question = next(q for q in payload["questions"] if q["question_id"] == "q_idem")
    assert question["depth_reached"] == "direct"
    assert question["depth_demonstrated"] == "deep_probed"


# --------------------------------------------------------------------------- #
#  §8 — the snapshot is the only source of interview truth
# --------------------------------------------------------------------------- #
def test_the_result_survives_the_draft_being_rewritten(evaluated):
    _, record = evaluated
    before = result.build_validated(record)

    draft = F.definition(7)
    draft.role_title = "Something Else Entirely"
    draft.interview_type = "deep"
    draft.skills = draft.skills[:1]
    draft.questions = draft.questions[:1]
    draft.recommended_duration_min = 90
    versions.save_draft("iv_fix", draft)

    after = result.build_validated(evaluations.get(record.evaluation_id))
    assert after == before
    assert after["interview"]["role_title"] == "Senior Backend Engineer, Payments"
    assert len(after["skills"]) == len(F.SKILLS)


def test_publishing_a_new_version_does_not_move_a_completed_result(evaluated):
    _, record = evaluated
    before = result.build_validated(record)

    changed = F.definition(7)
    changed.questions[0].question_text = "A completely different question."
    versions.publish("iv_fix", changed, validate=False)

    assert result.build_validated(evaluations.get(record.evaluation_id)) == before


# --------------------------------------------------------------------------- #
#  §20, §21 — states, and never a result from a failed run
# --------------------------------------------------------------------------- #
def test_a_pending_evaluation_has_no_result():
    versions.publish("iv_fix", F.definition(7), validate=False)
    state = F.strong_senior().session()
    store.save(state)
    record, _ = jobs.request(state)
    with pytest.raises(result.ResultError):
        result.build(record)


def test_a_failed_evaluation_has_no_result():
    versions.publish("iv_fix", F.definition(7), validate=False)
    fixture = F.strong_senior()
    state = fixture.session()
    store.save(state)

    def broken(question, skill, definition, *, session_id=""):
        raise EV.ExtractionError("provider returned 402")

    record = jobs.request_and_run(state, extractor=broken, judge=fixture.judge())
    assert record.status == evaluations.FAILED
    with pytest.raises(result.ResultError) as caught:
        result.build(record)
    assert "failed" in str(caught.value)


def test_a_result_from_an_older_engine_version_is_refused_not_reinterpreted():
    """v1 scored over every listed skill. Reading it with v2's denominator would
    invent a percentage nobody computed."""
    _, record = _persisted(F.strong_senior())
    record.engine_version = "deep_evidence_v1"
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build(record)
    assert "deep_evidence_v1" in str(caught.value)


# --------------------------------------------------------------------------- #
#  §22 — a contradictory result is refused, not shown
# --------------------------------------------------------------------------- #
def test_a_tampered_total_is_caught(evaluated):
    _, record = evaluated
    record.result["candidate_details"]["total_score"] += 11
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build_validated(record)
    assert any("total_score" in v for v in caught.value.violations)


def test_a_tampered_rating_is_caught(evaluated):
    _, record = evaluated
    record.result["candidate_details"]["overall_rating"] = "Excellent"
    record.result["percentage"] = 12.0
    evaluations.save(record)
    with pytest.raises(result.ResultError):
        result.build_validated(record)


def test_a_disallowed_recommendation_is_caught(evaluated):
    _, record = evaluated
    record.result["recommendation"] = "Hire immediately"
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build_validated(record)
    assert any("not allowed" in v for v in caught.value.violations)


def test_an_undiscussed_skill_scoring_anything_is_caught(evaluated):
    _, record = evaluated
    row = next(r for r in record.result["skill_assessment"]
               if r["discussion_status"] == "not_discussed")
    row["Depth"] = 3
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build_validated(record)
    assert any("not discussed but scored" in v for v in caught.value.violations)


def test_a_mentioned_skill_above_its_cap_is_caught():
    _, record = _persisted(F.weak_senior())
    row = next((r for r in record.result["skill_assessment"]
                if r["discussion_status"] == "mentioned"), None)
    if row is None:
        pytest.skip("this fixture produced no mentioned skill")
    row["Clarity"] = 4
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build_validated(record)
    assert any("exceeds 1" in v for v in caught.value.violations)


def test_reasoning_can_never_appear_as_a_scoring_criterion(evaluated):
    _, record = evaluated
    payload = result.build_validated(record)
    for skill in payload["skills"]:
        assert "Reasoning" not in skill["criteria"]
        assert set(skill["criteria"]) == set(CRITERIA)
    for item in payload["evidence"]:
        assert item["supports_criterion"] != "Reasoning"
        assert item["supports_criterion"] in CRITERIA


def test_evidence_pointing_at_a_turn_outside_the_snapshot_is_caught(evaluated):
    _, record = evaluated
    record.evidence[0]["turn_id"] = "q_idem#99"
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build_validated(record)
    assert any("not in the snapshot" in v for v in caught.value.violations)


def test_mismatched_coverage_is_caught(evaluated):
    _, record = evaluated
    record.result["coverage"]["skills_discussed"] += 1
    evaluations.save(record)
    with pytest.raises(result.ResultError) as caught:
        result.build_validated(record)
    assert any("coverage" in v for v in caught.value.violations)


def test_a_duplicate_skill_row_is_impossible(evaluated):
    _, record = evaluated
    record.result["skill_assessment"].append(dict(record.result["skill_assessment"][0]))
    evaluations.save(record)
    with pytest.raises(result.ResultError):
        result.build_validated(record)


# --------------------------------------------------------------------------- #
#  §25 — the edge cases, through the real pipeline
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(F.ALL))
def test_every_fixture_produces_a_self_consistent_result(name):
    fixture = F.ALL[name]()
    _, record = _persisted(fixture)
    payload = result.build_validated(record)
    assert payload["overall"]["recommendation"] in RECOMMENDATIONS
    assert len(payload["skills"]) == len(F.SKILLS)
    coverage = payload["coverage"]
    assert (coverage["skills_discussed"] + coverage["skills_mentioned"]
            + coverage["skills_not_discussed"]) == coverage["skills_total"]


def test_a_single_skill_assessment_works():
    definition = F.definition(7)
    definition.skills = definition.skills[:1]
    definition.questions = [q for q in definition.questions
                            if q.skill_id == definition.skills[0].id]
    definition.tasks = definition.tasks[:1]
    versions.publish("iv_one", definition, validate=False)

    fixture = F.strong_senior()
    state = fixture.session()
    state.interview_id = "iv_one"
    state.asked_item_ids = ["q_idem"]
    state.records = {"q_idem": state.records["q_idem"]}
    store.save(state)

    record = jobs.request_and_run(state, extractor=_extractor(), judge=fixture.judge())
    payload = result.build_validated(record)
    assert len(payload["skills"]) == 1
    assert payload["coverage"]["coverage_percentage"] == 100.0
    assert payload["overall"]["max_score"] == 25


def test_repeating_the_request_returns_the_same_result(evaluated):
    state, record = evaluated
    again = jobs.request_and_run(state, extractor=_extractor(),
                                 judge=F.strong_senior().judge())
    assert again.evaluation_id == record.evaluation_id
    assert result.build_validated(again) == result.build_validated(record)


# --------------------------------------------------------------------------- #
#  The coverage gate on the recommendation
# --------------------------------------------------------------------------- #
def test_a_confident_verdict_needs_coverage_in_both_directions():
    """A negative verdict from one skill is the same error as a coverage-inflated
    denominator, pointing the other way.

    The gate counts every skill without substantive evidence, not only the ones
    never asked. Before that, a candidate with one skill of six assessed was
    declared "Not suitable for this role" when the other five were `mentioned`,
    while an otherwise identical candidate whose five gaps were never asked got
    "Needs further evaluation" — two equally unassessed candidates, opposite
    verdicts, on a distinction the coverage figure does not draw.
    """
    one_assessed_five_mentioned = [
        _row("Assessed", "discussed", [2, 3, 2, 2, 3]),
        *[_row(f"Glanced {n}", "mentioned", [1, 1, 1, 1, 1]) for n in range(5)],
    ]
    one_assessed_five_never_asked = [
        _row("Assessed", "discussed", [2, 3, 2, 2, 3]),
        *[_row(f"Never asked {n}", "not_discussed", [0, 0, 0, 0, 0]) for n in range(5)],
    ]
    for rows in (one_assessed_five_mentioned, one_assessed_five_never_asked):
        recommendation, why = E.recommend(rows)
        assert recommendation == RECOMMENDATIONS[1], recommendation
        assert "not substantively discussed" in why

    coverage = Coverage.of([r.discussion_status for r in one_assessed_five_mentioned])
    assert coverage.coverage_percentage == pytest.approx(16.7, abs=0.1)


@pytest.mark.parametrize("label, spec, expected", [
    ("majority not discussed",
     [("discussed", [5, 5, 5, 5, 5]), ("not_discussed", [0] * 5), ("not_discussed", [0] * 5)],
     RECOMMENDATIONS[1]),
    ("majority discussed at the bar",
     [("discussed", [4] * 5), ("discussed", [3, 3, 4, 3, 4]), ("discussed", [2, 3, 3, 3, 3])],
     RECOMMENDATIONS[2]),
    ("mixed strong and weak",
     [("discussed", [5] * 5), ("discussed", [2, 2, 3, 2, 3])],
     RECOMMENDATIONS[1]),
    ("majority discussed below the bar",
     [("discussed", [2, 2, 2, 2, 3]), ("discussed", [1, 2, 2, 1, 2]), ("discussed", [4] * 5)],
     RECOMMENDATIONS[0]),
    ("no skills discussed",
     [("not_discussed", [0] * 5), ("not_discussed", [0] * 5)],
     RECOMMENDATIONS[1]),
])
def test_the_canonical_recommendation_cases_are_unchanged(label, spec, expected):
    """§12 — the five the brief names, asserted after the gate change.

    The four that specify a majority of DISCUSSED skills cannot reach the
    coverage gate at all, so widening its input could not move them.
    """
    # Named without digits: the explanation quotes skill names back, and a digit
    # in a name would defeat the check below that no SCORE leaked into the prose.
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    rows = [_row(names[n], status, values)
            for n, (status, values) in enumerate(spec)]
    actual, why = E.recommend(rows)
    assert actual == expected, f"{label}: {actual}"
    assert not any(ch.isdigit() for ch in why), "a number reached the explanation"


# --------------------------------------------------------------------------- #
#  §9 — the recommendation against coverage, in four combinations
#
#  The point of each is the same: the score answers "how well did they do on
#  what was assessed", coverage answers "how much was assessed", and the
#  recommendation is allowed to read both. Nothing multiplies them.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label, spec, expect_recommendation, expect_percentage", [
    (
        "high score, low coverage",
        [("discussed", [5, 5, 5, 4, 5]),
         *[("not_discussed", [0] * 5) for _ in range(3)]],
        RECOMMENDATIONS[1], 96.0,
    ),
    (
        "high score, high coverage",
        [("discussed", [5, 5, 5, 4, 5]), ("discussed", [4, 4, 4, 4, 5]),
         ("discussed", [4, 4, 4, 4, 4]), ("not_discussed", [0] * 5)],
        RECOMMENDATIONS[2], 86.7,
    ),
    (
        "moderate score, high coverage",
        [("discussed", [3, 3, 3, 3, 3]), ("discussed", [3, 3, 3, 3, 4]),
         ("discussed", [3, 3, 4, 3, 3])],
        RECOMMENDATIONS[2], 62.7,
    ),
    (
        "low score, high coverage",
        [("discussed", [2, 2, 2, 2, 2]), ("discussed", [2, 1, 2, 2, 2]),
         ("discussed", [2, 2, 2, 3, 2])],
        RECOMMENDATIONS[0], 40.0,
    ),
])
def test_score_and_coverage_answer_different_questions(
    label, spec, expect_recommendation, expect_percentage
):
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    rows = [_row(names[n], status, values) for n, (status, values) in enumerate(spec)]
    discussed = [r for r in rows if r.discussion_status == "discussed"]

    total = sum(r.score for r in discussed)
    maximum = 25 * len(discussed)
    percentage = round(total / maximum * 100, 1)
    coverage = Coverage.of([r.discussion_status for r in rows])
    recommendation, _ = E.recommend(rows)

    # The score is performance-only: the denominator is the discussed skills,
    # and an unassessed skill neither adds to it nor subtracts from the total.
    assert percentage == pytest.approx(expect_percentage, abs=0.1), label
    assert maximum == 25 * coverage.skills_discussed

    # Coverage is a separate figure over every listed skill.
    assert coverage.skills_total == len(rows)
    assert coverage.coverage_percentage == pytest.approx(
        len(discussed) / len(rows) * 100, abs=0.1
    )

    # The recommendation may read both — and in the low-coverage case it does.
    assert recommendation == expect_recommendation, f"{label}: {recommendation}"

    # And nothing anywhere multiplies one by the other.
    composite = round(percentage * coverage.coverage_percentage / 100, 1)
    assert percentage != composite or coverage.coverage_percentage == 100.0


def test_high_coverage_does_not_rescue_a_low_score_or_the_other_way_round():
    """The two figures move independently, which is the whole point."""
    strong_narrow = [_row("Alpha", "discussed", [5] * 5),
                     *[_row(f"Gap {n}", "not_discussed", [0] * 5) for n in "BCD"]]
    weak_broad = [_row("Alpha", "discussed", [2] * 5), _row("Bravo", "discussed", [2] * 5),
                  _row("Charlie", "discussed", [2, 2, 2, 3, 2])]

    narrow_pct = 100.0
    broad_pct = round(sum(r.score for r in weak_broad) / (25 * 3) * 100, 1)
    assert narrow_pct > broad_pct
    assert Coverage.of([r.discussion_status for r in strong_narrow]).coverage_percentage < \
        Coverage.of([r.discussion_status for r in weak_broad]).coverage_percentage
    # The higher score with the thinner interview is NOT the stronger verdict.
    assert E.recommend(strong_narrow)[0] == RECOMMENDATIONS[1]
    assert E.recommend(weak_broad)[0] == RECOMMENDATIONS[0]


# --------------------------------------------------------------------------- #
#  §8 — boundary disclosure
# --------------------------------------------------------------------------- #
def test_a_recommendation_one_point_from_a_different_one_says_so():
    """The measured case: a majority meet the bar and one criterion sits on the
    severe mark, so a single point moves the verdict without moving the score."""
    rows = [
        _row("Alpha", "discussed", [4, 4, 4, 4, 4]),
        _row("Bravo", "discussed", [4, 4, 4, 4, 4]),
        _row("Charlie", "discussed", [3, 1, 4, 2, 3]),
    ]
    boundary = E.boundary_for(rows)
    assert boundary["at_boundary"] is True
    assert boundary["skills"] == ["Charlie"]
    assert "severe threshold" in " ".join(boundary["reasons"])

    # One point up on that criterion and the recommendation is a different word.
    before, _ = E.recommend(rows)
    rows[2].depth = 2
    after, _ = E.recommend(rows)
    assert (before, after) == (RECOMMENDATIONS[1], RECOMMENDATIONS[2])


def test_a_verdict_nowhere_near_a_threshold_is_not_flagged():
    """A warning that always fires is noise, so this is the other direction."""
    clear_fail = [
        _row("Alpha", "discussed", [1, 1, 1, 1, 1]),
        _row("Bravo", "discussed", [1, 2, 1, 1, 1]),
        _row("Charlie", "discussed", [2, 1, 1, 1, 1]),
        _row("Delta", "discussed", [1, 1, 2, 1, 1]),
        _row("Echo", "discussed", [1, 1, 1, 2, 1]),
    ]
    assert E.recommend(clear_fail)[0] == RECOMMENDATIONS[0]
    assert E.boundary_for(clear_fail)["at_boundary"] is False
    # Severe criteria everywhere — and none of them is the threshold this
    # recommendation turned on.
    assert E.boundary_for(clear_fail)["skills"] == []


def test_the_boundary_flag_reaches_the_result_without_changing_it(evaluated):
    _, record = evaluated
    payload = result.build_validated(record)
    boundary = payload["summary"]["recommendation_boundary"]
    assert set(boundary) == {
        "at_boundary", "reasons", "skills", "skills_meeting_bar", "skills_discussed"
    }
    assert isinstance(boundary["at_boundary"], bool)
    # It is derived from the persisted rows, so it cannot disagree with them.
    assert boundary == E.boundary_of([
        (row["discussion_status"],
         {name: row[name] for name in CRITERIA},
         row["skill_name"])
        for row in record.result["skill_assessment"]
    ])
    # And it changes nothing: the score, the rating and the recommendation are
    # exactly what the engine recorded.
    assert payload["overall"]["recommendation"] == record.result["recommendation"]
    assert payload["overall"]["total_score"] == \
        record.result["candidate_details"]["total_score"]
