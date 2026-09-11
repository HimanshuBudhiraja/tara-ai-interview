"""The two vocabularies, and the line between them.

A real `openai/gpt-4.1-mini` call returned `"Reasoning"` for
`supports_criterion` and the whole question's evidence was thrown away. The
model was not being unreasonable: the extractor prompt listed six evidence
DIMENSIONS — one of them `reasoning` — immediately above five scoring CRITERIA,
and asked for a value from the second list without ever saying they were
different lists.

This file pins the fix from both ends: the prompt has to make the distinction
unmissable, and code has to enforce the five criteria per item — recovering the
one confusion that is deterministic to recover, and refusing everything else.
"""
from __future__ import annotations

import pytest

from packages.schemas import (
    EVIDENCE_EXTRACTION,
    EVIDENCE_EXTRACTION_ACCEPT,
    SKILL_ASSESSMENT,
    SchemaError,
    validate,
)
from packages.types.evaluation import CRITERIA, DIMENSION_SUPPORTS, DIMENSIONS
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV
from tests import fixtures_candidates as F


# --------------------------------------------------------------------------- #
#  The model-facing contract
# --------------------------------------------------------------------------- #
def test_the_prompt_separates_the_two_vocabularies_by_name():
    system = EV.SYSTEM
    assert "EVIDENCE DIMENSIONS" in system
    assert "SCORING CRITERIA" in system
    assert "THEY ARE NOT INTERCHANGEABLE" in system.upper()
    # The exact confusion the live model made, called out in the exact words.
    assert 'There is no criterion called' in system
    assert '"Reasoning"' in system


def test_the_prompt_lists_every_dimension_and_every_criterion():
    system = EV.SYSTEM
    for dimension in DIMENSIONS:
        assert dimension in system, dimension
    for criterion in CRITERIA:
        assert criterion in system, criterion


def test_the_prompt_gives_the_dimension_to_criterion_mapping():
    """So a model that is unsure has somewhere to look other than the wrong list."""
    for dimension, criterion in DIMENSION_SUPPORTS.items():
        assert f"{dimension}" in EV.SYSTEM
        assert criterion in EV.SYSTEM


def test_there_are_still_exactly_five_criteria():
    assert CRITERIA == ("Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication")
    assert "Reasoning" not in CRITERIA
    assert len(DIMENSIONS) == 6


# --------------------------------------------------------------------------- #
#  The schema
# --------------------------------------------------------------------------- #
def test_the_schema_sent_to_the_provider_still_spells_out_every_vocabulary():
    """The model must keep learning the allowed values from the schema too."""
    item = EVIDENCE_EXTRACTION["properties"]["evidence"]["items"]["properties"]
    assert item["depth_dimension"]["enum"] == list(DIMENSIONS)
    assert item["supports_criterion"]["enum"] == list(CRITERIA)
    assert item["evidence_type"]["enum"] == [
        "supported", "partial", "contradicted", "missing", "unclear"
    ]
    assert item["evidence_strength"]["enum"] == ["strong", "moderate", "weak"]


def test_the_schema_we_accept_checks_the_envelope_and_leaves_items_to_code():
    """Ask for everything; refuse only over the shape.

    An enum inside an array is all-or-nothing: one item borrowing a value from a
    neighbouring list used to destroy every good item beside it. The items are
    checked one at a time in `validate_evidence` instead.
    """
    item = EVIDENCE_EXTRACTION_ACCEPT["properties"]["evidence"]["items"]
    assert item["required"] == EVIDENCE_EXTRACTION[
        "properties"]["evidence"]["items"]["required"]
    for name, prop in item["properties"].items():
        assert "enum" not in prop, name
        assert prop["type"] == "string", name
    # And the two are otherwise the same document.
    assert set(item["properties"]) == set(
        EVIDENCE_EXTRACTION["properties"]["evidence"]["items"]["properties"]
    )


def test_the_scoring_schema_is_untouched():
    """Where the criteria become numbers, nothing was loosened."""
    for criterion in CRITERIA:
        assert SKILL_ASSESSMENT["properties"][criterion] == {
            "type": "integer", "minimum": 0, "maximum": 5
        }
    assert SKILL_ASSESSMENT["properties"]["discussion_status"]["enum"] == [
        "discussed", "mentioned", "not_discussed"
    ]
    assert "Reasoning" not in SKILL_ASSESSMENT["properties"]


#: The two real responses that broke live runs, verbatim in the fields that
#: broke them: a criterion named with a dimension's word, and a dimension named
#: with an evidence type's word.
LIVE_FAILURES = [
    ("criterion took a dimension's word", {
        "turn_id": "t1", "candidate_quote": "we key the capture",
        "depth_dimension": "reasoning", "evidence_type": "supported",
        "evidence_strength": "strong", "supports_criterion": "Reasoning",
    }),
    ("dimension took an evidence type's word", {
        "turn_id": "t1", "candidate_quote": "we key the capture",
        "depth_dimension": "missing", "evidence_type": "missing",
        "evidence_strength": "weak", "supports_criterion": "Depth",
    }),
]


@pytest.mark.parametrize("label, item", LIVE_FAILURES, ids=[r[0] for r in LIVE_FAILURES])
def test_a_real_failed_response_no_longer_dies_at_the_gateway(label, item):
    """Both live failures got as far as the envelope check and stopped there.

    They must now pass it, so the per-item validator can deal with each one on
    its own terms — recovering the first, rejecting the second.
    """
    validate({"evidence": [item]}, EVIDENCE_EXTRACTION_ACCEPT)


@pytest.mark.parametrize("label, item", LIVE_FAILURES, ids=[r[0] for r in LIVE_FAILURES])
def test_the_same_responses_are_still_refused_by_the_schema_we_asked_for(label, item):
    """The request was not weakened. Only what we agree to read was."""
    with pytest.raises(SchemaError):
        validate({"evidence": [item]}, EVIDENCE_EXTRACTION)


def test_the_envelope_is_still_enforced():
    for bad in ({}, {"evidence": "lots"}, {"evidence": [{"turn_id": "t1"}]}):
        with pytest.raises(SchemaError):
            validate(bad, EVIDENCE_EXTRACTION_ACCEPT)


# --------------------------------------------------------------------------- #
#  Resolution: recover what is unambiguous, refuse the rest
# --------------------------------------------------------------------------- #
def _resolve(value: str, dimension: str = "reasoning"):
    report = EV.ExtractionReport()
    return EV.resolve_criterion(value, dimension, report), report


@pytest.mark.parametrize("criterion", list(CRITERIA))
def test_a_canonical_criterion_passes_through_untouched(criterion):
    resolved, report = _resolve(criterion)
    assert resolved == criterion
    assert report.repairs == []


@pytest.mark.parametrize(
    "dimension, criterion",
    sorted(DIMENSION_SUPPORTS.items()),
)
def test_a_dimension_name_is_recovered_to_the_criterion_it_supports(dimension, criterion):
    """The live failure, in both the casing the model used and the raw one."""
    for spelling in (dimension, dimension.replace("_", " ").title().replace(" ", "-"),
                     dimension.title()):
        resolved, report = _resolve(spelling, dimension)
        assert resolved == criterion, spelling
        assert report.repairs, f"{spelling} was corrected silently"
        assert report.repairs[0]["now"] == criterion


def test_reasoning_becomes_problem_solving_and_never_a_sixth_criterion():
    resolved, report = _resolve("Reasoning")
    assert resolved == "Problem-Solving"
    assert resolved in CRITERIA
    assert report.repairs == [
        {"field": "supports_criterion", "was": "Reasoning", "now": "Problem-Solving"}
    ]


@pytest.mark.parametrize("spelling", ["problem solving", "PROBLEM-SOLVING", "problem-solving"])
def test_a_criterion_spelled_loosely_is_normalised_not_rejected(spelling):
    resolved, report = _resolve(spelling)
    assert resolved == "Problem-Solving"
    assert report.repairs


@pytest.mark.parametrize("value", ["Insight", "Confidence", "Seniority", "5", "Reason"])
def test_an_unrelated_value_is_refused_rather_than_defaulted(value):
    """It used to become "Depth". An unrecognised string must not quietly turn
    into a real criterion on a document used to make a hiring decision."""
    resolved, _ = _resolve(value)
    assert resolved is None


def test_an_empty_criterion_falls_back_to_the_items_own_dimension():
    resolved, report = _resolve("", "edge_cases")
    assert resolved == "Depth"
    assert report.repairs[0]["was"] == "(empty)"


# --------------------------------------------------------------------------- #
#  End to end through validate_evidence
# --------------------------------------------------------------------------- #
def _question_and_skill():
    from services.evaluation import transcript as T

    fixture = F.strong_senior()
    definition = F.definition(7)
    transcript = T.build(fixture.session(), definition)
    question = transcript.questions[0]
    skill = next(s for s in definition.skills if s.id == question.skill_id)
    return question, skill


def _raw(turn, **overrides):
    item = {
        "turn_id": turn.turn_id,
        "candidate_quote": turn.answer.split(".")[0].strip(),
        "depth_dimension": "reasoning",
        "evidence_type": "supported",
        "evidence_strength": "strong",
        "supports_criterion": "Problem-Solving",
    }
    item.update(overrides)
    return item


def test_one_bad_criterion_no_longer_costs_the_whole_questions_evidence():
    """The regression the live run exposed, at the level it actually hurt."""
    question, skill = _question_and_skill()
    turns = question.usable_turns()
    raw = [_raw(turns[0]), _raw(turns[0], supports_criterion="Reasoning")]
    if len(turns) > 1:
        raw.append(_raw(turns[1]))

    accepted, report = EV.validate_evidence(raw, question, skill)
    assert len(accepted) == len(raw)
    assert all(item.supports_criterion in CRITERIA for item in accepted)
    assert len(report.repairs) == 1


def test_an_unrecoverable_criterion_loses_only_its_own_item():
    question, skill = _question_and_skill()
    turn = question.usable_turns()[0]
    accepted, report = EV.validate_evidence(
        [_raw(turn), _raw(turn, supports_criterion="Vibes")], question, skill
    )
    assert len(accepted) == 1
    assert len(report.rejected) == 1
    assert "not one of the five criteria" in report.rejected[0]["reason"]


def test_recovery_never_touches_the_evidence_dimension():
    """The dimension is what the evidence IS. Nothing repairs that."""
    question, skill = _question_and_skill()
    turn = question.usable_turns()[0]
    accepted, report = EV.validate_evidence(
        [_raw(turn, depth_dimension="Reasoning")], question, skill
    )
    assert accepted == []
    assert "unknown depth dimension" in report.rejected[0]["reason"]


# --------------------------------------------------------------------------- #
#  The other two vocabularies, given the same treatment
# --------------------------------------------------------------------------- #
def test_the_prompt_separates_evidence_types_too():
    """The second live failure: "missing" is an evidence type, not a dimension."""
    system = EV.SYSTEM
    assert "EVIDENCE TYPES" in system
    assert 'belongs to (C) and only to (C)' in system
    assert "It is not a dimension" in system


def test_the_prompt_asks_for_an_empty_list_rather_than_a_placeholder():
    """What the model was reaching for when it wrote "missing" as a dimension."""
    assert '{"evidence": []}' in EV.SYSTEM
    assert "Do NOT invent a placeholder item" in EV.SYSTEM


def test_an_evidence_type_in_the_dimension_field_loses_only_its_own_item():
    question, skill = _question_and_skill()
    turn = question.usable_turns()[0]
    accepted, report = EV.validate_evidence(
        [_raw(turn), _raw(turn, depth_dimension="missing", evidence_type="missing")],
        question, skill,
    )
    assert len(accepted) == 1
    assert len(report.rejected) == 1
    assert "unknown depth dimension 'missing'" in report.rejected[0]["reason"]
    # And it is NOT quietly repaired into a real dimension.
    assert report.repairs == []


def test_an_unknown_evidence_type_loses_only_its_own_item():
    question, skill = _question_and_skill()
    turn = question.usable_turns()[0]
    accepted, report = EV.validate_evidence(
        [_raw(turn), _raw(turn, evidence_type="reasoning")], question, skill
    )
    assert len(accepted) == 1
    assert "unknown evidence type 'reasoning'" in report.rejected[0]["reason"]


def test_an_empty_extraction_is_a_valid_answer():
    question, skill = _question_and_skill()
    accepted, report = EV.validate_evidence([], question, skill)
    assert accepted == [] and report.rejected == [] and report.accepted == 0


# --------------------------------------------------------------------------- #
#  A judgement with a gap in it (§16, and a real `thin` candidate failure)
# --------------------------------------------------------------------------- #
def test_a_judgement_missing_one_criterion_is_accepted_and_recorded():
    """A real `thin` persona's evaluation failed outright on one absent key.

    The judge returned four criteria and omitted `Depth`; the gateway's schema
    check rejected the whole response and the run failed — on the candidate
    whose evaluation is hardest to produce. `apply_constraints` was already
    built to cope, so the strictness was destroying responses the layer below
    could handle.
    """
    from packages.schemas import (
        SKILL_ASSESSMENT,
        SKILL_ASSESSMENT_ACCEPT,
        SchemaError,
        validate,
    )

    partial = {
        "discussion_status": "discussed", "Accuracy": 2, "Clarity": 2,
        "Problem-Solving": 1, "Communication": 2, "remarks": "Thin throughout.",
    }
    # Still refused by what we ASK for...
    with pytest.raises(SchemaError):
        validate(partial, SKILL_ASSESSMENT)
    # ...and accepted by what we agree to READ.
    validate(partial, SKILL_ASSESSMENT_ACCEPT)

    report = E.EvaluationReport()
    scores = E.apply_constraints(partial, "discussed", "Policy judgment", report)
    assert set(scores) == set(CRITERIA)
    # `discussed` floors every criterion at 1, so the gap does not become a zero.
    assert scores["Depth"] == 1
    assert any("Depth was absent" in note for note in report.adjustments)


def test_a_response_missing_most_criteria_is_not_a_judgement():
    """One gap is recoverable. Four is a row resting entirely on defaults."""
    from services.evaluation import transcript as T

    fixture = F.strong_senior()
    definition = F.definition(7)
    transcript = T.build(fixture.session(), definition)
    skill = next(s for s in definition.skills if s.id == "skl_idem")
    report = E.EvaluationReport()

    E.assess_skill(
        skill, [], transcript, "Senior", report,
        judge=lambda payload, session_id="": {
            "discussion_status": "discussed", "Accuracy": 3, "remarks": "Fine.",
        },
    )
    assert any("too few to constrain" in failure for failure in report.failures)


def test_a_judgement_with_every_criterion_records_no_absence():
    from services.evaluation import transcript as T

    fixture = F.strong_senior()
    definition = F.definition(7)
    transcript = T.build(fixture.session(), definition)
    skill = next(s for s in definition.skills if s.id == "skl_idem")
    report = E.EvaluationReport()

    E.assess_skill(skill, [], transcript, "Senior", report, judge=fixture.judge())
    assert not any("absent from the judgement" in note for note in report.adjustments)
    assert report.failures == []
