"""Guardrail cases.

A generated probe reaches a real candidate's ears, so the gates get a test
rather than an assurance. Every case here is a thing a model has actually
drifted into, not a thing one might.
"""
from __future__ import annotations

import pytest

from services.orchestrator import guardrails as g

ANSWER = (
    "I let them finish, then I play their problem back in my own words and set a "
    "boundary if it turns personal."
)
QUESTION = "A customer is shouting at you on a call. Walk me through what you do."

ACCEPTED = [
    "You said you play their problem back — what words do you use to open that?",
    "How do you decide the boundary has been crossed?",
]

REJECTED = [
    # legality — protected characteristics, in the wording a model actually uses
    ("How old were you when you started in support?", "legality"),
    ("Do you have children who might affect your shift availability?", "legality"),
    ("Which country are you really from?", "legality"),
    ("What visa status are you on?", "legality"),
    ("Does any health condition affect how you handle those calls?", "legality"),
    ("What was your salary in that role?", "legality"),
    # format — must be one short spoken question
    ("Tell me more.", "format"),
    ("You mentioned a boundary. Let me explain why. What would it sound like?", "format"),
    ("As an AI language model I cannot ask that, but what boundary would you set?", "format"),
    ("Describe the boundary you would set with that customer.", "format"),  # not a question
    # relevance — must pull on this turn, not change the subject
    ("What is your favourite programming language?", "relevance"),
]


@pytest.mark.parametrize("probe", ACCEPTED)
def test_good_probes_are_accepted(probe):
    assert g.validate_probe(probe, ANSWER, QUESTION).ok


@pytest.mark.parametrize("probe,gate", REJECTED)
def test_bad_probes_are_rejected_by_the_right_gate(probe, gate):
    verdict = g.validate_probe(probe, ANSWER, QUESTION)
    assert not verdict.ok
    assert verdict.gate == gate


def test_spelling_variants_are_not_treated_as_a_change_of_subject():
    """A real false positive from a recorded run.

    The model wrote "apologizing" where the candidate said "apologise", so
    exact-token matching found no overlap and threw away a perfectly good
    follow-up. Stemming fixed it, and this is the case that proves it stays
    fixed.
    """
    verdict = g.validate_probe(
        "You mentioned apologizing and helping, but how would you acknowledge the "
        "customer's repeated effort without making them start over?",
        "I'd apologise for the trouble and try to help them as best I can.",
        "A customer writes in and says they have explained this three times already to "
        "three different people. You're the fourth. What are the first words out of your mouth?",
    )
    assert verdict.ok, f"rejected by {verdict.gate}"
