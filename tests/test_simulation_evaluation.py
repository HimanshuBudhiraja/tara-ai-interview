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
    # Five whole-number criteria summed to /25 (a criterion with no evidence counts 0), mapped to a level.
    assert by["Negotiation"]["total"] == 22 and by["Negotiation"]["level"] == 5 and by["Negotiation"]["level_label"] == "Expert"
    assert by["Discovery"]["total"] == 13 and by["Discovery"]["level"] == 3 and by["Discovery"]["level_label"] == "Intermediate"
    assert by["Negotiation"]["score"] == 88 and by["Discovery"]["score"] == 52
    assert by["Closing"]["discussion"] == sim.NOT_DISCUSSED and by["Closing"]["total"] == 0 and by["Closing"]["level"] is None
    # Closing never came up: no evidence, so NOT_ASSESSED, even though the judge was eager.
    assert by["Closing"]["status"] == sim.NOT_ASSESSED and by["Closing"]["score"] is None
    # Weighted over what was assessed: (88*40 + 52*30) / 70
    assert r["overall"] == round((88 * 40 + 52 * 30) / 70, 1)
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


@pytest.mark.parametrize("total,level", [(25, 5), (23, 5), (21, 5), (18, 4), (17, 4), (14, 3), (12, 3), (9, 2), (7, 2), (4, 1), (0, 1)])
def test_total_skill_score_maps_to_the_documented_levels(total, level):
    assert sim.level_of(total) == level


def test_a_mentioned_skill_gets_only_minimal_credit():
    judged = [dict(JUDGED[0], status="mentioned"), JUDGED[1]]
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=FakeLLM(EVIDENCE, judged))
    neg = next(x for x in r["skills"] if x["name"] == "Negotiation")
    assert neg["discussion"] == sim.MENTIONED and neg["total"] == sim.MENTIONED_CAP and neg["level"] == 1
    assert sim.integrity(r, TRANSCRIPT) == []
    bad = copy.deepcopy(r)
    next(x for x in bad["skills"] if x["name"] == "Negotiation")["total"] = 22
    assert any("don't agree" in p for p in sim.integrity(bad, TRANSCRIPT))


def test_the_authors_evaluation_guidance_reaches_the_extractor_and_the_judge():
    snap = snapshot()
    snap["agent"]["evaluation_context"] = "Penalise any invented revenue figure."
    llm = FakeLLM(EVIDENCE, JUDGED)
    sim.evaluate(snap, TRANSCRIPT, complete=llm)
    assert "Penalise any invented revenue figure." in llm.calls[0] and "Penalise any invented revenue figure." in llm.calls[1]


class FlakyJudge(FakeLLM):
    """The judge answers badly first (criteria scored as skills, then empty), then well."""

    def __init__(self, bad):
        super().__init__(EVIDENCE, JUDGED)
        self.bad = list(bad)

    def __call__(self, system, user, max_tokens, workload):
        if '"skills"' in user and self.bad:
            self.calls.append(user)
            return self.bad.pop(0)
        return super().__call__(system, user, max_tokens, workload)


def test_an_unusable_judge_answer_is_retried_never_scored_as_zero():
    llm = FlakyJudge([{"skills": [{"name": "Accuracy", "criteria": {"Accuracy": 1}}]}, {"skills": []}])
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=llm)
    by = {s["name"]: s for s in r["skills"]}
    assert by["Negotiation"]["total"] == 22 and by["Discovery"]["total"] == 13


def test_a_judge_that_never_answers_usably_fails_loudly(monkeypatch):
    monkeypatch.setattr(sim, "JUDGE_BACKOFF_SEC", (0, 0, 0))
    llm = FlakyJudge([{"skills": []}] * (sim.JUDGE_ATTEMPTS * sim.JUDGE_SAMPLES))
    with pytest.raises(sim.EvaluationError, match="no usable score"):
        sim.evaluate(snapshot(), TRANSCRIPT, complete=llm)


def test_a_judged_skill_missing_a_criterion_is_not_usable():
    assert sim._usable({"criteria": {"Accuracy": 4}}, ["Accuracy", "Depth"]) is None
    assert sim._usable({"criteria": {"accuracy": 4, "Depth": None}}, ["Accuracy", "Depth"]) == {"Accuracy": 4, "Depth": 0}


def test_scoring_runs_only_on_the_pinned_hosts(monkeypatch):
    from services import config
    from services.ai import gateway as g

    assert g._PROVIDERS[g.Workload.AGENT_SCORER] == config.AGENT_SCORER_PROVIDERS and "AkashML" not in config.AGENT_SCORER_PROVIDERS


def test_scores_are_the_median_of_independent_runs_and_status_is_the_majority():
    runs = [
        {"criteria": {"Accuracy": 4, "Depth": 4}, "status": "discussed", "rationale": "a"},
        {"criteria": {"Accuracy": 1, "Depth": 1}, "status": "mentioned", "rationale": "b"},
        {"criteria": {"Accuracy": 3, "Depth": 5}, "status": "discussed", "rationale": "c"},
    ]
    out = sim._combine(runs, ["Accuracy", "Depth"])
    assert out["criteria"] == {"Accuracy": 3, "Depth": 4} and out["status"] == "discussed" and out["samples"] == 3


def test_all_zero_for_a_skill_with_quotes_is_a_broken_answer_and_is_retried():
    zero = {"skills": [{"name": n, "status": "mentioned", "criteria": dict.fromkeys(sim.criteria_of({}), 0)}
                       for n in ("Negotiation", "Discovery")]}
    r = sim.evaluate(snapshot(), TRANSCRIPT, complete=FlakyJudge([zero, zero]))
    assert {s["name"]: s["total"] for s in r["skills"]}["Negotiation"] == 22


def test_two_sentences_of_one_turn_joined_by_the_model_still_verify_word_for_word():
    turn = "With Sabre we modernized a large estate. The problem was slow change. The case study reports 30 percent savings."
    q = "With Sabre we modernized a large estate. The case study reports 30 percent savings."
    assert sim.verify_quote(q, turn) and sim.shown_quote(q, turn) == "With Sabre we modernized a large estate. … The case study reports 30 percent savings."
    assert sim.verify_quote(sim.shown_quote(q, turn), turn)
    assert not sim.verify_quote("With Sabre we modernized a large estate. The case study reports 60 percent savings.", turn)
