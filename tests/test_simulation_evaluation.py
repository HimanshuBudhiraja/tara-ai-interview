"""The generic simulation evaluation: real quotes, weights, NOT_ASSESSED, integrity."""
from __future__ import annotations

import copy

import pytest

from services.evaluation import simulation as sim

TRANSCRIPT = [
    {"role": "agent", "text": "Thanks for making time. What brings you to us today?"},
    {"role": "user", "text": "We are losing deals because our quotes take three days to prepare.", "t": 5.2},
    {"role": "agent", "text": "Why does that matter now?"},
    {"role": "user", "text": "Our biggest customer told us they will move to a competitor next quarter.", "t": 14.0},
    {"role": "agent", "text": "That's a high price for us."},
    {"role": "user", "text": "I understand. If we commit to two years, can you hold the current rate?", "t": 30.5},
]


def snapshot(purpose="L&D", rubric=None):
    return {
        "agent_id": "ab_x", "version": 3,
        "fields": {"role": "Account executive"},
        "cfg": {"purpose": purpose},
        "agent": {
            "title": "Renewal negotiation", "type_label": "Sales role-play",
            "persona": {"name": "Priya"},
            "rubric": rubric or [
                {"name": "Negotiation", "anchor": "Trades concessions for value.", "weight": 40},
                {"name": "Discovery", "anchor": "Uncovers the real driver.", "weight": 30},
                {"name": "Closing", "anchor": "Asks for the commitment.", "weight": 30},
            ],
        },
    }


class FakeLLM:
    """Plays the three model calls; records what it was asked."""

    def __init__(self, evidence, judged, narrative=None):
        self.evidence, self.judged, self.narrative, self.calls = evidence, judged, narrative, []

    def __call__(self, system, user, max_tokens, workload):
        self.calls.append(user)
        if '"evidence"' in user:
            return {"evidence": self.evidence}
        if '"skills"' in user:
            return {"skills": self.judged}
        return self.narrative or {"did_well": [{"text": "You named the real driver.", "evidence_ids": ["E2"]}],
                                  "improve": [{"text": "Unsupported claim.", "evidence_ids": ["E99"]}],
                                  "try_next": []}


EVIDENCE = [
    {"skill": "Negotiation", "turn": "P3", "quote": "If we commit to two years, can you hold the current rate?",
     "criterion": "Problem solving", "polarity": "strength", "why": "Trades term for price."},
    {"skill": "Discovery", "turn": "P2", "quote": "Our biggest customer told us they will move to a competitor",
     "criterion": "Depth", "polarity": "strength", "why": "Names the urgency."},
    {"skill": "Discovery", "turn": "P1", "quote": "we lose deals because quotes are slow",  # paraphrase: not in the transcript
     "criterion": "Accuracy", "polarity": "strength", "why": "x"},
]
JUDGED = [
    {"name": "Negotiation", "criteria": {"Accuracy": 4, "Depth": 4, "Clarity": 5, "Problem solving": 5, "Communication": 4}},
    {"name": "Discovery", "criteria": {"Accuracy": 3, "Depth": 4, "Clarity": 3, "Problem solving": None, "Communication": 3}},
    {"name": "Closing", "criteria": {"Accuracy": 5, "Depth": 5, "Clarity": 5, "Problem solving": 5, "Communication": 5}},
]


def test_weights_are_honoured_and_unassessed_skills_are_not_zero():
    llm = FakeLLM(EVIDENCE, JUDGED)
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=llm)
    by = {s["name"]: s for s in r["skills"]}
    assert by["Negotiation"]["score"] == 88 and by["Discovery"]["score"] == 65
    # Closing never came up: no evidence, so NOT_ASSESSED, even though the judge was eager.
    assert by["Closing"]["status"] == sim.NOT_ASSESSED and by["Closing"]["score"] is None
    # Weighted over what was assessed: (88*40 + 65*30) / 70
    assert r["overall"] == round((88 * 40 + 65 * 30) / 70, 1)
    assert r["weight_coverage"] == 0.7
    assert r["skills_not_assessed"] == ["Closing"]


def test_a_paraphrased_quote_is_dropped_never_scored():
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=FakeLLM(EVIDENCE, JUDGED))
    assert r["unverified_quotes_dropped"] == 1
    assert all(sim.verify_quote(e["quote"], next(t["text"] for t in sim.participant_turns(TRANSCRIPT) if t["id"] == e["turn"]))
               for e in r["evidence"])
    assert r["evidence"][0]["t"] in (5.2, 14.0, 30.5)   # evidence keeps its timestamp


def test_an_uncited_narrative_claim_is_removed():
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=FakeLLM(EVIDENCE, JUDGED))
    assert r["narrative"]["did_well"] and r["narrative"]["improve"] == []


@pytest.mark.parametrize("purpose,starts", [("General", "Recommended"), ("Hiring", "Proceed to next round"), ("HR", "Review"), ("L&D", "Proficient")])
def test_purpose_sets_the_outcome_wording(purpose, starts):
    r = sim.evaluate(snapshot(purpose), TRANSCRIPT, complete=FakeLLM(EVIDENCE, JUDGED))
    assert r["recommendation"].startswith(starts) and r["purpose"] == purpose


def test_low_coverage_is_never_a_hiring_verdict():
    r = sim.evaluate(snapshot("Hiring", rubric=[
        {"name": "Negotiation", "anchor": "", "weight": 20}, {"name": "Closing", "anchor": "", "weight": 80}]),
        TRANSCRIPT, complete=FakeLLM(EVIDENCE[:1], JUDGED))
    assert r["weight_coverage"] == 0.2 and r["recommendation"].startswith("Needs further evaluation")


def test_weights_that_do_not_total_100_are_renormalised_and_flagged():
    r = sim.evaluate(snapshot(rubric=[{"name": "Negotiation", "anchor": "", "weight": 1},
                                      {"name": "Discovery", "anchor": "", "weight": 1}]),
                     TRANSCRIPT, complete=FakeLLM(EVIDENCE, JUDGED))
    assert r["weights_renormalised"] and sum(s["weight"] for s in r["skills"]) == 100


def test_no_participant_speech_is_not_an_evaluation():
    with pytest.raises(sim.EvaluationError):
        sim.evaluate(snapshot(), [{"role": "agent", "text": "Hello?"}], complete=FakeLLM([], []))


def test_integrity_catches_a_tampered_result():
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=FakeLLM(EVIDENCE, JUDGED))
    assert sim.integrity(r, TRANSCRIPT) == []
    bad = copy.deepcopy(r)
    bad["evidence"][0]["quote"] = "I will sign today for any price at all"
    bad["overall"] = 99
    bad["skills"][2]["score"] = 10
    problems = sim.integrity(bad, TRANSCRIPT)
    assert any("does not match the transcript" in p for p in problems)
    assert any("overall score" in p for p in problems)
    assert any("not assessed but has a score" in p for p in problems)


def test_the_result_records_what_produced_it():
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=FakeLLM(EVIDENCE, JUDGED), flow={"nodes": []})
    assert r["versions"]["scenario"] == "ab_x v3" and r["versions"]["evaluation"] == sim.ENGINE_VERSION
    assert r["versions"]["rubric"] and r["versions"]["flow"]


def test_the_extractor_never_sees_the_scoring_scale():
    llm = FakeLLM(EVIDENCE, JUDGED)
    sim.evaluate(snapshot(), TRANSCRIPT, complete=llm)
    assert "0-5" not in llm.calls[0] and "Do not score" in llm.calls[0]
