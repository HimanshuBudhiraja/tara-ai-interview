"""Reconnecting after a dropped call: the brief, the clock, and the fallbacks."""
from __future__ import annotations

from services.assessment import agent_builder_retell as rx
from services.assessment import resume as R

SNAP = {
    "agent": {
        "title": "Pricing call", "type_label": "Sales role-play", "persona": {"name": "Priya"},
        "questions": [{"text": "What changed in your budget this year?"},
                      {"text": "How would you justify the price increase to finance?"},
                      {"text": "What would a two year commitment need to include?"}],
    },
}
T = [
    {"role": "agent", "text": "So, what changed in your budget this year?"},
    {"role": "user", "text": "We had a hiring freeze and finance wants clear savings."},
    {"role": "agent", "text": "How would you justify the price increase to finance, then?"},
    {"role": "user", "text": "I'd show the two hundred hours we saved last quarter and"},
]


def test_the_brief_knows_what_is_covered_where_it_stopped_and_the_time():
    b = R.build(SNAP, T, minutes_used=7.4, target_minutes=20)
    assert b["covered"] == ["What changed in your budget this year?", "How would you justify the price increase to finance?"]
    assert b["remaining"] == ["What would a two year commitment need to include?"]
    assert "may not have finished" in b["where"] and "two hundred hours" in b["where"]
    assert b["used"] == 7 and b["left"] == 13
    ctx = R.render(b)
    assert ctx.startswith("RECONNECTED") and "do not ask again" in ctx and "Still to cover" in ctx
    assert "middle of your answer" in R.fallback_opening(b)


def test_an_unanswered_question_is_asked_again():
    b = R.build(SNAP, T[:3], 3, 20)
    assert "had not answered yet" in b["where"]
    assert R.fallback_opening(b).endswith("How would you justify the price increase to finance, then?")


def test_the_model_only_adds_points_and_the_rejoin_line_and_can_fail():
    def good(system, user, max_tokens, workload):
        assert workload == "question_suggester"
        return {"points": ["Hiring freeze this year", "Saved two hundred hours last quarter"],
                "opening": "Sorry, we lost each other there. You were telling me about the hours you saved. Go on."}

    ctx, opening, b = R.for_reconnect(SNAP, T, 7, 20, good)
    assert opening.startswith("Sorry, we lost each other") and "Hiring freeze" in ctx
    assert b["covered"] == R.build(SNAP, T, 7, 20)["covered"]           # the model never decides coverage

    def bad(*a, **k):
        raise RuntimeError("model down")
    ctx2, opening2, _ = R.for_reconnect(SNAP, T, 7, 20, bad)
    assert "middle of your answer" in opening2 and "RECONNECTED" in ctx2

    def off_script(*a, **k):
        return {"points": [], "opening": "Hi! I'm Tara, an AI model. Let's start fresh."}
    _, opening3, _ = R.for_reconnect(SNAP, T, 7, 20, off_script)
    assert "Tara" not in opening3                                          # rejected, fallback used


def test_a_reconnect_gets_the_remaining_time_not_a_fresh_allowance():
    row = {"agent": {**SNAP["agent"], "opening_line": "Hello!", "closing_line": "Bye.", "instructions": "x",
                     "description": "d", "rubric": [], "depth": "Probing", "voice": "adrian"},
           "cfg": {"tone": "Realistic", "depth": "Probing", "voice": "adrian", "ending": "Tara decides"},
           "fields": {"role": "AE", "skills": "Negotiation"}}
    body = rx.web_call_body(row, "agent_x", reconnect={"context": "RECONNECTED ...", "opening": "Sorry, we got cut off.", "minutes_used": 12})
    v = body["retell_llm_dynamic_variables"]
    target, cap = rx.lengths(row["cfg"])
    assert v["target_minutes"] == str(target - 12) and v["max_minutes"] == str(cap - 12)
    assert body["agent_override"]["agent"]["max_call_duration_ms"] == (cap - 12 + 2) * 60_000
    assert v["opening_line"] == "Sorry, we got cut off." and v["resume_context"].startswith("RECONNECTED")


def test_a_topic_the_participant_raised_counts_as_covered():
    t = [{"role": "agent", "text": "Your quote is higher than last year."},
         {"role": "user", "text": "Before numbers, what changed in your budget this year?"},
         {"role": "agent", "text": "A hiring freeze."}]
    assert R.covered_items(["What changed in your budget this year?", "What would a two year commitment need?"], t) == [True, False]
