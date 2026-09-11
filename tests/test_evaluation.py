"""The evidence-driven evaluation engine.

No provider is called: the judge is stubbed from each fixture's declared intent,
so what is under test is the deterministic half — stage derivation, evidence
validation, the discussion-status and score constraints, aggregation and the
recommendation rules.

The claim running through the file is the one the whole engine exists to make:
**how far the interview probed and how well the candidate did are different
things**, and the engine must never confuse them.
"""
from __future__ import annotations

import pytest

from packages.types.evaluation import (
    CRITERIA,
    UNRATED,
    RECOMMENDATIONS,
    EvidenceItem,
    SkillAssessment,
    experience_level,
    overall_rating,
    stage_index,
)
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV
from services.evaluation import transcript as T
from tests import fixtures_candidates as F


def _evaluate(fixture: F.Fixture, evidence=None):
    definition = F.definition(fixture.experience_to)
    transcript = T.build(fixture.session(), definition)
    items = evidence if evidence is not None else _plausible_evidence(fixture, transcript)
    return E.evaluate(transcript, definition, items, judge=fixture.judge())


def _plausible_evidence(
    fixture: F.Fixture, transcript: T.InterviewTranscript
) -> list[EvidenceItem]:
    """Evidence a fair extractor would produce, built from the real turns.

    Quotes are taken from the transcript so they are genuinely verbatim — the
    same thing the validator demands of the model.
    """
    dimension_by_stage = {
        "direct": "conceptual_understanding",
        "probed": "reasoning",
        "deep_probed": "edge_cases",
    }
    items: list[EvidenceItem] = []
    for question in transcript.questions:
        for turn in question.usable_turns():
            words = turn.answer.split()
            if len(words) < F.__dict__.get("_MIN", 8):
                continue
            strength = "strong" if len(words) >= 45 else "moderate" if len(words) >= 18 else "weak"
            items.append(EvidenceItem(
                skill_id=question.skill_id,
                skill_name=next(n for i, n, _, _ in F.SKILLS if i == question.skill_id),
                question_id=question.question_id,
                task_id=question.task_id,
                turn_id=turn.turn_id,
                depth_stage=turn.depth_stage,
                depth_dimension=dimension_by_stage[turn.depth_stage],
                candidate_quote=" ".join(words[:14]),
                evidence_type="supported",
                evidence_strength=strength,
                supports_criterion="Depth",
            ))
    return items


# --------------------------------------------------------------------------- #
#  Depth: reached vs demonstrated
# --------------------------------------------------------------------------- #
def test_a_candidate_answering_only_the_direct_question_shows_direct_depth():
    evaluation, _ = _evaluate(F.strong_junior())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    assert row.depth_evaluation.depth_reached == "direct"
    assert row.depth_evaluation.depth_demonstrated == "direct"


def test_strong_evidence_through_two_rungs_shows_probed_depth():
    evaluation, _ = _evaluate(F.mixed_candidate())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    assert row.depth_evaluation.depth_reached == "probed"
    assert stage_index(row.depth_evaluation.depth_demonstrated) >= stage_index("probed")


def test_reaching_the_deepest_stage_with_weak_evidence_does_not_earn_deep_depth():
    """The mistake the engine exists to prevent: stage reached read as capability."""
    evaluation, _ = _evaluate(F.over_probed_candidate())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")

    assert row.depth_evaluation.depth_reached == "deep_probed", "the interview did probe twice"
    assert row.depth_evaluation.depth_demonstrated == "direct", (
        "weak evidence was credited as deep just because the interview went there"
    )
    assert row.score < 15


def test_reaching_the_deepest_stage_with_strong_evidence_does_earn_it():
    """The claim in the name, asserted on evidence the test states outright.

    It used to lean on `_plausible_evidence`, which grades strength by word
    count — so what the deepest rung was worth depended on how long the answer
    happened to be. Since a stage deeper than `direct` now requires `strong`
    evidence, the fixture's arbitrary 45-word cutoff was deciding the outcome.
    The evidence is built here instead, so the test says what it means.
    """
    fixture = F.strong_senior()
    definition = F.definition(fixture.experience_to)
    transcript = T.build(fixture.session(), definition)
    deep_turn = transcript.for_skill("skl_idem")[0].usable_turns()[-1]

    strong_deep = [EvidenceItem(
        skill_id="skl_idem", skill_name="Idempotent design", question_id="q_idem",
        turn_id=deep_turn.turn_id, depth_stage=deep_turn.depth_stage,
        depth_dimension="production_judgment",
        candidate_quote=deep_turn.answer.split(".")[0].strip(),
        evidence_type="supported", evidence_strength="strong",
        supports_criterion="Depth",
    )]
    evaluation, _ = E.evaluate(
        transcript, definition, strong_deep, judge=fixture.judge()
    )
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    assert row.depth_evaluation.depth_reached == "deep_probed"
    assert row.depth_evaluation.depth_demonstrated == "deep_probed"


def test_only_strong_evidence_carries_a_stage_deeper_than_direct():
    """The threshold added after measurement, stated as a rule.

    Moderate evidence still establishes that the candidate knows the concept. It
    does not establish that they applied it, weighed it, or reasoned about what
    it costs — those are the claims a single generously-tagged item was setting
    for a whole skill.
    """
    fixture = F.strong_senior()
    definition = F.definition(fixture.experience_to)
    transcript = T.build(fixture.session(), definition)
    turn = transcript.for_skill("skl_idem")[0].usable_turns()[0]

    def evidence(dimension: str, strength: str):
        return [EvidenceItem(
            skill_id="skl_idem", skill_name="Idempotent design", question_id="q_idem",
            turn_id=turn.turn_id, depth_stage="direct", depth_dimension=dimension,
            candidate_quote=turn.answer.split(".")[0].strip(),
            evidence_type="supported", evidence_strength=strength,
            supports_criterion="Depth",
        )]

    for dimension, stage in (("reasoning", "probed"),
                             ("production_judgment", "deep_probed")):
        assert E.depth_demonstrated_from(evidence(dimension, "strong")) == stage
        assert E.depth_demonstrated_from(evidence(dimension, "moderate")) == "direct"

    # Moderate evidence of the concept itself is still worth the concept.
    assert E.depth_demonstrated_from(
        evidence("conceptual_understanding", "moderate")
    ) == "direct"


def test_a_skill_settled_in_one_answer_is_not_penalised_for_ending_early():
    """Evidence saturation: the runtime stopped because it had what it needed."""
    saturated, _ = _evaluate(F.saturated_candidate())
    probed, _ = _evaluate(F.strong_senior())

    a = next(r for r in saturated.skill_assessment if r.skill_id == "skl_idem")
    b = next(r for r in probed.skill_assessment if r.skill_id == "skl_idem")

    assert a.depth_evaluation.depth_reached == "direct"
    assert b.depth_evaluation.depth_reached == "deep_probed"
    # One follow-up fewer must not cost the candidate anything.
    assert a.score >= b.score - 1


def test_a_longer_interview_does_not_raise_the_score():
    """More turns is not more evidence. The over-probed candidate was asked more
    and demonstrated less."""
    long_thin, _ = _evaluate(F.over_probed_candidate())
    short_strong, _ = _evaluate(F.saturated_candidate())

    long_turns = sum(len(a) for a in F.over_probed_candidate().answers.values())
    short_turns = sum(len(a) for a in F.saturated_candidate().answers.values())
    assert long_turns > short_turns

    assert short_strong.candidate_details.total_score > long_thin.candidate_details.total_score


def test_depth_reached_and_depth_demonstrated_are_reported_separately():
    evaluation, _ = _evaluate(F.over_probed_candidate())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    payload = row.to_dict()["depth_evaluation"]
    assert "depth_reached" in payload and "depth_demonstrated" in payload
    assert payload["depth_reached"] != payload["depth_demonstrated"]


def _evidence(dimension: str, strength: str = "strong", evidence_type: str = "supported"):
    return [EvidenceItem(
        skill_id="skl_idem", skill_name="Idempotent design", question_id="q_idem",
        turn_id="q_idem#0", depth_stage="direct", depth_dimension=dimension,
        candidate_quote=F.IDEM_DEEP.split()[0],
        evidence_type=evidence_type, evidence_strength=strength,
        supports_criterion="Accuracy",
    )]


def _judge_returning(**extra):
    def judge(payload, *, session_id=""):
        return {
            "discussion_status": "discussed", "Accuracy": 4, "Depth": 4, "Clarity": 4,
            "Problem-Solving": 4, "Communication": 4, "remarks": "Good.", **extra,
        }
    return judge


def _idem_row(evidence, judge):
    fixture = F.strong_senior()
    definition = F.definition(fixture.experience_to)
    transcript = T.build(fixture.session(), definition)
    evaluation, report = E.evaluate(transcript, definition, evidence, judge=judge)
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    return row, report


@pytest.mark.parametrize("claimed", ["deep_probed", "probed", "direct"])
def test_the_judge_has_no_say_in_demonstrated_depth(claimed):
    """The contract changed here, and the reason is measured rather than argued.

    `depth_demonstrated` used to be advisory: the judge could argue it DOWN from
    what the evidence supported, on the theory that it might see an extractor
    over-tagging a dimension. A benchmark run against `openai/gpt-4.1-mini`
    measured what it actually did — answered from the interview's probe count in
    33 of 37 cases, and fired the downgrade on 10 of 13 depth failures, always
    downward, silently understating candidates.

    The concern it existed for already had a control, applied per item at
    extraction time against the actual quote: `evidence_strength`. Two controls
    on one concern, one of them measurably answering a different question, is
    one too many — so the judge's is gone and the deterministic one stays.
    """
    row, report = _idem_row(
        _evidence("practical_application"), _judge_returning(depth_demonstrated=claimed)
    )
    # `practical_application` supports `probed`, whatever the judge says in
    # either direction. The dimension here used to be `trade_offs`, which now
    # carries the deepest stage — the assertion is about the judge having no
    # vote, so the fixture moved to a dimension that still sits at probed.
    assert row.depth_evaluation.depth_demonstrated == "probed"
    assert any("unsolicited depth figure" in note for note in report.adjustments)


def test_weak_or_unsupported_evidence_still_cannot_carry_a_stage():
    """The control that remains: applied per item, against the quote."""
    for strength, evidence_type in (("weak", "supported"), ("strong", "partial")):
        row, _ = _idem_row(
            _evidence("production_judgment", strength, evidence_type),
            _judge_returning(),
        )
        assert row.depth_evaluation.depth_demonstrated == "direct", (strength, evidence_type)

    row, _ = _idem_row(_evidence("production_judgment"), _judge_returning())
    assert row.depth_evaluation.depth_demonstrated == "deep_probed"


# --------------------------------------------------------------------------- #
#  Experience calibration
# --------------------------------------------------------------------------- #
def test_the_same_answer_is_worth_more_to_a_junior_than_to_a_senior():
    """Not an inconsistency — the bar is different, and that is the point."""
    junior, _ = _evaluate(F.strong_junior())
    senior, _ = _evaluate(F.weak_senior())

    assert junior.candidate_details.experience_level == "Junior"
    assert senior.candidate_details.experience_level == "Senior"

    junior_idem = next(r for r in junior.skill_assessment if r.skill_id == "skl_idem")
    senior_idem = next(r for r in senior.skill_assessment if r.skill_id == "skl_idem")
    assert junior_idem.score > senior_idem.score


@pytest.mark.parametrize("years,expected", [
    (0, "Junior"), (2, "Junior"), (3, "Mid"), (5, "Mid"),
    (6, "Senior"), (8, "Senior"), (12, "Lead / Expert"),
])
def test_experience_bands(years, expected):
    assert experience_level(years) == expected


# --------------------------------------------------------------------------- #
#  Discussion status
# --------------------------------------------------------------------------- #
def test_a_skill_nobody_asked_about_is_not_discussed():
    evaluation, _ = _evaluate(F.strong_senior())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_k8s")

    assert row.discussion_status == "not_discussed"
    assert row.score == 0
    assert all(v == 0 for v in row.criteria().values())
    assert row.remarks == "Not discussed in interview"
    assert row.depth_evaluation.evidence_confidence == "insufficient"


def test_a_not_discussed_skill_is_reported_as_coverage_not_as_a_weakness():
    """The contract changed here, deliberately.

    Coverage gaps used to be appended to `areas_for_improvement` with a
    disclaimer attached. That put "the interview did not ask about Kubernetes"
    in the same bulleted list as things the candidate did badly — and in a list
    a recruiter skims, the disclaimer is the first thing lost. Gaps are now a
    first-class `Coverage` figure and appear in no list of weaknesses.
    """
    evaluation, _ = _evaluate(F.strong_senior())
    improvements = " ".join(evaluation.areas_for_improvement).lower()
    assert "not covered by this interview" not in improvements
    assert "kubernetes" not in improvements

    coverage = evaluation.coverage
    assert coverage.skills_total == len(F.SKILLS)
    assert coverage.skills_not_discussed == 1
    assert coverage.skills_discussed == 3
    assert coverage.coverage_percentage == 75.0


def test_a_skill_only_glanced_at_is_mentioned_and_capped_at_one():
    fixture = F.Fixture(
        "Glancing", 6,
        answers={"q_idem": ["Yes, idempotency."], "q_recon": [F.RECON_GOOD],
                 "q_review": [F.REVIEW_GOOD]},
        judgement={"skl_idem": F._v(4, 4, 4, 4, 4, "Overgenerous."),
                   "skl_recon": F._v(4, 4, 4, 4, 4, "Fine."),
                   "skl_review": F._v(4, 4, 4, 4, 4, "Fine.")},
    )
    evaluation, report = _evaluate(fixture)
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")

    assert row.discussion_status == "mentioned"
    assert all(v <= 1 for v in row.criteria().values())
    assert any("capped at 1" in note for note in report.adjustments)


def test_a_discussed_skill_can_never_score_zero_on_a_criterion():
    # A fixture whose answer IS substantive, so the skill is genuinely
    # "discussed" — the floor only applies there.
    fixture = F.mixed_candidate()
    fixture.judgement["skl_idem"]["Accuracy"] = 0
    evaluation, report = _evaluate(fixture)
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")

    assert row.accuracy == 1
    assert any("raised to 1" in note for note in report.adjustments)


def test_every_criterion_stays_inside_zero_to_five():
    fixture = F.strong_senior()
    fixture.judgement["skl_idem"]["Depth"] = 9
    fixture.judgement["skl_idem"]["Accuracy"] = -3
    evaluation, _ = _evaluate(fixture)
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    assert 0 <= row.depth <= 5 and 0 <= row.accuracy <= 5


# --------------------------------------------------------------------------- #
#  Evidence integrity
# --------------------------------------------------------------------------- #
def _question(fixture: F.Fixture, question_id: str = "q_idem"):
    definition = F.definition(fixture.experience_to)
    transcript = T.build(fixture.session(), definition)
    question = next(q for q in transcript.questions if q.question_id == question_id)
    skill = next(s for s in definition.skills if s.id == question.skill_id)
    return question, skill


def test_a_verbatim_quote_is_accepted():
    question, skill = _question(F.strong_senior())
    quote = " ".join(F.IDEM_DEEP.split()[:10])
    items, report = EV.validate_evidence([{
        "turn_id": "q_idem#0", "candidate_quote": quote,
        "depth_dimension": "conceptual_understanding", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Accuracy",
    }], question, skill)
    assert len(items) == 1
    assert not report.rejected


def test_a_fabricated_quote_is_rejected():
    """The check that matters most: a sentence attributed to someone who never
    said it, inside a document used to make a hiring decision."""
    question, skill = _question(F.strong_senior())
    items, report = EV.validate_evidence([{
        "turn_id": "q_idem#0",
        "candidate_quote": "I have fifteen years of experience leading payments teams",
        "depth_dimension": "practical_application", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Depth",
    }], question, skill)
    assert items == []
    assert any("not in the transcript" in r["reason"] for r in report.rejected)


def test_a_paraphrase_is_not_a_quote():
    question, skill = _question(F.strong_senior())
    items, _ = EV.validate_evidence([{
        "turn_id": "q_idem#0",
        "candidate_quote": "Each capture call includes a unique identifier for deduplication",
        "depth_dimension": "conceptual_understanding", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Accuracy",
    }], question, skill)
    assert items == []


def test_evidence_from_a_turn_that_does_not_exist_is_rejected():
    question, skill = _question(F.strong_senior())
    items, report = EV.validate_evidence([{
        "turn_id": "q_idem#99", "candidate_quote": "anything",
        "depth_dimension": "reasoning", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Depth",
    }], question, skill)
    assert items == []
    assert any("does not exist" in r["reason"] for r in report.rejected)


def test_a_flagged_turn_cannot_become_evidence():
    """A turn that tried to instruct the system is not also a demonstration."""
    question, skill = _question(F.injection_candidate())
    flagged = [t for t in question.turns if t.flagged]
    assert flagged, "the injection fixture was not flagged by the scanner"

    items, report = EV.validate_evidence([{
        "turn_id": flagged[0].turn_id,
        "candidate_quote": "I stay calm",
        "depth_dimension": "conceptual_understanding", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Accuracy",
    }], question, skill)
    assert items == []
    assert any("not usable" in r["reason"] for r in report.rejected)


def test_an_injected_turn_does_not_reach_the_score():
    evaluation, _ = _evaluate(F.injection_candidate())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    # The fixture's judge would hand out 5s. The turn never reaches it, so the
    # skill reads as barely touched.
    assert row.discussion_status in ("mentioned", "not_discussed")
    assert row.score <= 5


def test_protected_topic_content_is_never_turned_into_evidence():
    fixture = F.Fixture(
        "Volunteered", 6,
        answers={"q_idem": ["I'm 52 and I've been doing this since before you were born, "
                            "and I use an idempotency key with the charge row."]},
        judgement={"skl_idem": F._v(3, 3, 3, 3, 3, "Fine.")},
    )
    question, skill = _question(fixture)
    items, report = EV.validate_evidence([{
        "turn_id": "q_idem#0", "candidate_quote": "I'm 52 and I've been doing this",
        "depth_dimension": "practical_application", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Depth",
    }], question, skill)
    assert items == []
    assert any("age" in r["reason"] for r in report.rejected)


def test_a_claim_of_expertise_is_not_evidence_of_expertise():
    evaluation, _ = _evaluate(F.claim_only_candidate())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")
    assert row.accuracy <= 2 and row.depth <= 2


def test_contradictory_evidence_lowers_confidence():
    fixture = F.contradictory_candidate()
    definition = F.definition(fixture.experience_to)
    transcript = T.build(fixture.session(), definition)

    evidence = [
        EvidenceItem(
            skill_id="skl_idem", skill_name="Idempotent design", question_id="q_idem",
            turn_id="q_idem#0", depth_stage="direct",
            depth_dimension="conceptual_understanding",
            candidate_quote=" ".join(F.CONTRADICTORY_A.split()[:8]),
            evidence_type="supported", evidence_strength="strong",
            supports_criterion="Accuracy",
        ),
        EvidenceItem(
            skill_id="skl_idem", skill_name="Idempotent design", question_id="q_idem",
            turn_id="q_idem#1", depth_stage="probed",
            depth_dimension="conceptual_understanding",
            candidate_quote=" ".join(F.CONTRADICTORY_B.split()[:8]),
            evidence_type="contradicted", evidence_strength="strong",
            supports_criterion="Accuracy",
        ),
    ]
    evaluation, _ = E.evaluate(transcript, definition, evidence, judge=fixture.judge())
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")

    assert row.depth_evaluation.evidence_confidence in ("low", "medium")
    assert row.accuracy <= 3


def test_confidence_is_not_capability():
    """"We do not know" is more useful than an invented low score."""
    evaluation, _ = _evaluate(F.coverage_gap_candidate())
    k8s = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_k8s")
    idem = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")

    assert k8s.depth_evaluation.evidence_confidence == "insufficient"
    assert idem.depth_evaluation.evidence_confidence in ("medium", "high")


# --------------------------------------------------------------------------- #
#  Transcript quality
# --------------------------------------------------------------------------- #
def test_speech_to_text_garbling_does_not_reduce_clarity_or_communication():
    """A bad transcript is not a bad communicator."""
    garbled, _ = _evaluate(F.stt_candidate())
    clean, _ = _evaluate(F.saturated_candidate())

    a = next(r for r in garbled.skill_assessment if r.skill_id == "skl_idem")
    b = next(r for r in clean.skill_assessment if r.skill_id == "skl_idem")
    assert a.clarity >= b.clarity - 1
    assert a.communication >= b.communication - 1


# --------------------------------------------------------------------------- #
#  Remarks
# --------------------------------------------------------------------------- #
def test_remarks_never_contain_a_numeric_score():
    fixture = F.strong_senior()
    fixture.judgement["skl_idem"]["remarks"] = (
        "Candidate scored 4 in Accuracy and 3 in Depth, which is 4/5 overall."
    )
    evaluation, report = _evaluate(fixture)
    row = next(r for r in evaluation.skill_assessment if r.skill_id == "skl_idem")

    assert "4/5" not in row.remarks
    assert "scored 4" not in row.remarks
    assert any("numeric score removed" in note for note in report.adjustments)


def test_every_recommendation_explanation_is_qualitative():
    for make in F.ALL.values():
        evaluation, _ = _evaluate(make())
        assert not any(ch.isdigit() for ch in evaluation.recommendation_explaination), (
            f"{make.__name__}: a number reached the recommendation explanation"
        )


# --------------------------------------------------------------------------- #
#  Aggregation
# --------------------------------------------------------------------------- #
def test_the_total_is_the_sum_of_every_criterion_across_every_listed_skill():
    evaluation, _ = _evaluate(F.strong_senior())
    expected = sum(sum(r.criteria().values()) for r in evaluation.skill_assessment)
    assert evaluation.candidate_details.total_score == expected


def test_the_maximum_counts_only_the_skills_that_were_discussed():
    """Replaces a test that pinned the opposite, and the reason is measured.

    The maximum used to be 25 x every listed skill. On the `coverage_gap`
    fixture — one skill answered superbly, three never asked — that produced
    21/100 = 21%, rated `Poor`: a verdict on the interview, reported as a verdict
    on the candidate. Meanwhile `recommend` called the very same interview a
    coverage gap "rather than a finding about the candidate".

    Skills that were mentioned or never asked now contribute to neither side of
    the fraction and are reported as `Coverage` instead.
    """
    evaluation, _ = _evaluate(F.strong_senior())
    discussed = [r for r in evaluation.skill_assessment
                 if r.discussion_status == "discussed"]
    assert len(discussed) < len(F.SKILLS)          # a gap exists to be excluded
    assert evaluation.maximum_possible_score == 5 * len(CRITERIA) * len(discussed)


def test_the_same_answers_no_longer_read_as_poor_because_of_a_coverage_gap():
    """The case that drove the change, asserted end to end."""
    evaluation, _ = _evaluate(F.coverage_gap_candidate())
    assert evaluation.coverage.skills_discussed == 1
    assert evaluation.coverage.skills_not_discussed == 3
    assert evaluation.maximum_possible_score == 25
    assert evaluation.percentage > 80
    assert evaluation.candidate_details.overall_rating == "Excellent"
    # And the thin coverage still gates the decision, deterministically.
    assert evaluation.recommendation == RECOMMENDATIONS[1]


def test_nothing_discussed_has_no_rating_rather_than_a_bad_one():
    rows = [
        SkillAssessment(skill_name="A", discussion_status="not_discussed",
                        remarks="Not discussed in interview"),
        SkillAssessment(skill_name="B", discussion_status="mentioned",
                        accuracy=1, depth=1, clarity=1, problem_solving=1,
                        communication=1, score=5, remarks="Glanced at."),
    ]
    evaluation = E.aggregate(rows, candidate_name="X", job_role="Y", level="Senior")
    assert evaluation.maximum_possible_score == 0
    assert evaluation.candidate_details.total_score == 0
    assert evaluation.percentage == 0.0
    assert evaluation.candidate_details.overall_rating == UNRATED
    assert evaluation.coverage.coverage_percentage == 0.0
    assert evaluation.recommendation == RECOMMENDATIONS[1]


def test_a_mentioned_skill_contributes_to_neither_side_of_the_score():
    """§6 — a mention must not be rolled up into a scored skill."""
    strong = SkillAssessment(
        skill_name="Strong", discussion_status="discussed",
        accuracy=4, depth=4, clarity=4, problem_solving=4, communication=4, score=20,
        remarks="Solid.")
    mentioned = SkillAssessment(
        skill_name="Glanced", discussion_status="mentioned",
        accuracy=1, depth=1, clarity=1, problem_solving=1, communication=1, score=5,
        remarks="In passing.")
    evaluation = E.aggregate([strong, mentioned], candidate_name="X",
                             job_role="Y", level="Senior")
    assert evaluation.candidate_details.total_score == 20     # not 25
    assert evaluation.maximum_possible_score == 25            # not 50
    assert evaluation.percentage == 80.0
    assert evaluation.coverage.skills_mentioned == 1
    assert evaluation.coverage.coverage_percentage == 50.0


@pytest.mark.parametrize("percentage,rating", [
    (0, "Poor"), (39.9, "Poor"), (40, "Average"), (59.9, "Average"),
    (60, "Good"), (79.9, "Good"), (80, "Excellent"), (100, "Excellent"),
])
def test_overall_rating_bands(percentage, rating):
    assert overall_rating(percentage) == rating


def test_aggregation_is_computed_not_asked_for():
    """The model returns criteria; every number above them is arithmetic."""
    fixture = F.strong_senior()
    evaluation, _ = _evaluate(fixture)
    discussed = [r for r in evaluation.skill_assessment
                 if r.discussion_status == "discussed"]
    recomputed = sum(sum(r.criteria().values()) for r in discussed)
    maximum = 5 * len(CRITERIA) * len(discussed)
    assert evaluation.candidate_details.total_score == recomputed
    assert evaluation.percentage == pytest.approx(recomputed / maximum * 100, abs=0.1)
    assert evaluation.candidate_details.overall_rating == overall_rating(
        recomputed / maximum * 100
    )


# --------------------------------------------------------------------------- #
#  Recommendation rules
# --------------------------------------------------------------------------- #
def _row(name: str, status: str, values: list[int]) -> SkillAssessment:
    row = SkillAssessment(
        skill_name=name, discussion_status=status,
        accuracy=values[0], depth=values[1], clarity=values[2],
        problem_solving=values[3], communication=values[4], remarks="x",
    )
    row.score = sum(values)
    return row


def test_rule_1_majority_not_discussed_needs_further_evaluation():
    rows = [_row("A", "discussed", [5] * 5),
            _row("B", "not_discussed", [0] * 5),
            _row("C", "not_discussed", [0] * 5)]
    recommendation, why = E.recommend(rows)
    assert recommendation == RECOMMENDATIONS[1]
    # The gate counts every skill without substantive evidence, not only the
    # ones never asked, so the wording says "not substantively discussed".
    assert "not substantively discussed" in why


def test_rule_3_majority_at_the_bar_proceeds():
    rows = [_row("A", "discussed", [4] * 5), _row("B", "discussed", [3] * 5),
            _row("C", "discussed", [4] * 5)]
    assert E.recommend(rows)[0] == RECOMMENDATIONS[2]


def test_rule_3_a_severely_weak_skill_blocks_proceeding():
    rows = [_row("A", "discussed", [4] * 5), _row("B", "discussed", [4] * 5),
            _row("C", "discussed", [1, 1, 3, 1, 3])]
    assert E.recommend(rows)[0] != RECOMMENDATIONS[2]


def test_rule_4_mixed_needs_further_evaluation():
    rows = [_row("A", "discussed", [4] * 5), _row("B", "discussed", [2] * 5)]
    assert E.recommend(rows)[0] == RECOMMENDATIONS[1]


def test_rule_5_majority_below_the_bar_is_not_suitable():
    rows = [_row("A", "discussed", [2] * 5), _row("B", "discussed", [2] * 5),
            _row("C", "discussed", [4] * 5)]
    assert E.recommend(rows)[0] == RECOMMENDATIONS[0]


def test_rule_6_nothing_discussed_needs_further_evaluation():
    rows = [_row("A", "not_discussed", [0] * 5)]
    recommendation, why = E.recommend(rows)
    assert recommendation == RECOMMENDATIONS[1]
    assert "gap in the conversation" in why


def test_undiscussed_skills_never_drag_the_recommendation_down():
    """A skill nobody asked about says nothing about the candidate."""
    strong_only = [_row("A", "discussed", [4] * 5), _row("B", "discussed", [4] * 5)]
    with_gap = strong_only + [_row("C", "not_discussed", [0] * 5)]
    assert E.recommend(strong_only)[0] == RECOMMENDATIONS[2]
    assert E.recommend(with_gap)[0] == RECOMMENDATIONS[2]


def test_the_recommendation_is_always_one_of_the_three_allowed_values():
    for make in F.ALL.values():
        evaluation, _ = _evaluate(make())
        assert evaluation.recommendation in RECOMMENDATIONS


# --------------------------------------------------------------------------- #
#  Output contract
# --------------------------------------------------------------------------- #
def test_the_output_matches_the_canonical_contract():
    evaluation, _ = _evaluate(F.strong_senior())
    payload = evaluation.to_dict()

    assert set(payload) == {
        "candidate_details", "skill_assessment",
        "strengths_and_improvement_areas", "recommendation",
        # Spelled this way on purpose — it is the existing contract. The
        # corrected spelling is offered alongside it, never instead of it.
        "recommendation_explaination",
        "recommendation_explanation",
        "coverage",
    }
    assert payload["recommendation_explanation"] == payload["recommendation_explaination"]
    assert set(payload["coverage"]) == {
        "skills_total", "skills_discussed", "skills_mentioned",
        "skills_not_discussed", "coverage_percentage",
    }
    assert set(payload["candidate_details"]) == {
        "name", "job_role", "experience_level", "total_score", "overall_rating",
    }
    row = payload["skill_assessment"][0]
    assert set(row) == {
        "skill_name", "discussion_status", "score", "remarks",
        "Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication",
        "depth_evaluation",
    }
    assert set(row["depth_evaluation"]) == {
        "depth_reached", "depth_demonstrated", "dimensions_demonstrated",
        "dimensions_missing", "evidence_confidence",
    }


def test_the_output_carries_no_score_the_candidate_could_see():
    """This phase produces an evaluation object. It goes nowhere near the
    candidate runtime."""
    evaluation, _ = _evaluate(F.strong_senior())
    assert evaluation.session_id
    # Provenance is internal, not part of the wire contract.
    assert "session_id" not in evaluation.to_dict()


@pytest.mark.parametrize("name", sorted(F.ALL))
def test_every_fixture_produces_a_well_formed_evaluation(name):
    evaluation, report = _evaluate(F.ALL[name]())
    payload = evaluation.to_dict()

    assert payload["recommendation"] in RECOMMENDATIONS
    assert len(payload["skill_assessment"]) == len(F.SKILLS)
    for row in payload["skill_assessment"]:
        assert row["discussion_status"] in ("discussed", "mentioned", "not_discussed")
        for criterion in CRITERIA:
            assert 0 <= row[criterion] <= 5
        if row["discussion_status"] == "not_discussed":
            assert all(row[c] == 0 for c in CRITERIA)
            assert row["remarks"] == "Not discussed in interview"
        elif row["discussion_status"] == "mentioned":
            assert all(row[c] <= 1 for c in CRITERIA)
        else:
            assert all(row[c] >= 1 for c in CRITERIA)
    assert not report.failures
