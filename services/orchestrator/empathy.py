"""How Tara sounds human without ever judging how the candidate sounds.

Two hard rules from the spec, and they are in tension on purpose:

  "TARA may behave warmly but never score emotion."
  "Never scores accent, emotion, or appearance."

So affect is read for ONE purpose — choosing an acknowledgement — and is never
passed to anything that evaluates. The phrasebook is authored rather than
generated: warmth should be predictable, identical for every candidate in the
same situation, and auditable. A model improvising sympathy is a fairness risk.

Deliberately absent: praise. "Great answer", "that's excellent", "perfect" all
signal to the candidate how they are doing, which changes their behaviour
mid-interview and is unfair to whoever gets the less enthusiastic phrasing.
Acknowledgement says "I heard you". It does not say "you did well".
"""
from __future__ import annotations

import random

from services import config

# --- Acknowledgements: "I heard you", never "you did well" ------------------ #
_ACK_NEUTRAL = [
    "Got it.",
    "Okay, thank you.",
    "Understood.",
    "Right, thank you.",
    "That's helpful, thank you.",
]

_ACK_NERVOUS = [
    "Thank you — and please take your time, there's no rush here.",
    "That's completely fine, take the time you need.",
    "Thanks for that. There's no wrong way to answer these.",
]

_ACK_FRUSTRATED = [
    "I hear you, thank you.",
    "That's fair, thank you for saying so.",
    "Understood — thank you for being straight with me.",
]

_ACK_UPBEAT = [
    "Thank you.",
    "Good, thank you.",
    "Okay, thanks for walking me through that.",
]

_ACK_LONG_ANSWER = [
    "Thank you, that's a lot of useful detail.",
    "Thanks for taking me through all of that.",
]

_ACK_BY_AFFECT = {
    "neutral": _ACK_NEUTRAL,
    "flat": _ACK_NEUTRAL,
    "nervous": _ACK_NERVOUS,
    "frustrated": _ACK_FRUSTRATED,
    "upbeat": _ACK_UPBEAT,
}

# --- Transitions between items ---------------------------------------------- #
_TRANSITIONS = [
    "Let's move on.",
    "Next one.",
    "Moving on.",
    "Here's the next one.",
    "Let's try a different situation.",
]

_TRANSITIONS_SAME_COMPETENCY = [
    "Staying on that theme for a moment.",
    "One more in the same area.",
]

# --- Responses to non-answers ----------------------------------------------- #
_SKIP_ACK = [
    "That's absolutely fine — not everyone has hit that situation.",
    "No problem at all, that's a fair answer in itself.",
    "That's okay. Plenty of people haven't run into that one.",
]

_NUDGE_FIRST = [
    "Take your time — I'm here whenever you're ready.",
    "No rush. I'll wait.",
]

_NUDGE_SECOND = [
    "Would it help if I said the question again?",
    "I can repeat that if it's easier — just say the word.",
]

_HOLD = [
    "Take your time.",
    "No rush at all.",
]

_META_REPLIES = {
    "audio": "I can hear you fine. Go ahead whenever you're ready.",
    "progress": "We're moving along nicely — a few more to go.",
    "default": "Happy to help — but let's stay with the question for now.",
}


def _pick(options: list[str], seed_text: str) -> str:
    """Deterministic per utterance so a replayed session reproduces exactly.

    Same session, same answer, same acknowledgement. That matters for the audit
    trail: a run must be reconstructable, and a random phrasebook would make
    every replay differ from the recorded one.
    """
    rng = random.Random(seed_text)
    return rng.choice(options)


def acknowledge(affect: str, depth: str, answer: str) -> str:
    """A short line that shows the answer landed. Never evaluative."""
    if not config.EMPATHY_ENABLED:
        return ""
    if depth == "substantive" and len(answer.split()) > 110:
        return _pick(_ACK_LONG_ANSWER, answer)
    options = _ACK_BY_AFFECT.get(affect, _ACK_NEUTRAL)
    return _pick(options, answer)


def transition(same_competency: bool, seed: str) -> str:
    if same_competency:
        return _pick(_TRANSITIONS_SAME_COMPETENCY, seed)
    return _pick(_TRANSITIONS, seed)


def skip_acknowledgement(seed: str) -> str:
    return _pick(_SKIP_ACK, seed)


def nudge(streak: int, seed: str) -> str:
    return _pick(_NUDGE_FIRST if streak <= 1 else _NUDGE_SECOND, seed)


def hold(seed: str) -> str:
    return _pick(_HOLD, seed)


def meta_reply(text: str) -> str:
    lowered = text.lower()
    if "hear" in lowered or "audio" in lowered or "mic" in lowered:
        return _META_REPLIES["audio"]
    if "long" in lowered or "many" in lowered or "left" in lowered or "doing" in lowered:
        return _META_REPLIES["progress"]
    return _META_REPLIES["default"]


def greeting(name: str, role_title: str) -> str:
    """What Tara says before the first question.

    Two things it deliberately does NOT do.

    It does not say how many questions there are. A number turns a
    conversation into a countdown: candidates start pacing themselves against
    it, answer the fifth question with one eye on the sixth, and treat a
    follow-up as falling behind. The number is also not honest — the interview
    is adaptive, so a candidate who answers thinly gets more turns than one
    who does not, and quoting a figure we then exceed reads as moving the
    goalposts.

    And it does not hand straight over to the first question. Greeting then
    immediately interrogating is the thing that makes an automated interview
    feel automated; a person eases in. The closing line is that easing-in, and
    it is why `start` can still send the greeting and the first question as
    one spoken block without it landing like an ambush.
    """
    first = (name or "").strip().split(" ")[0]
    hello = f"Hi {first}! " if first else "Hi! "
    return (
        f"{hello}I'm Tara. I'll be having a conversation with you to understand your "
        f"experience, skills, and how you approach real-world situations related to "
        f"the {role_title} role. "
        # The three things a candidate most needs permission to do, said before
        # the first question rather than discovered halfway through. People
        # rush, answer the letter of the question, and leave out the reasoning
        # that is the actual evidence — unless they are told not to.
        "There are no trick questions. Take a moment to think before answering, "
        "and feel free to explain your reasoning or share examples from your "
        "experience. "
        "If you'd like me to repeat something or put it another way, just ask. "
        "Let's start with something straightforward."
    )


def closing(name: str, pool_closing: str) -> str:
    """How the interview ends.

    An interview that simply stops is the single most unsettling way to end
    one: the candidate is left wondering whether it finished, whether they
    were cut off, and whether anybody will look at it. So the close does three
    things a person would do — name them, say it is genuinely over, and say
    what happens next.

    "A person reviews this" is not reassurance padding. It is true, it is the
    thing candidates most want to know about an AI interview, and it is stated
    everywhere else in the product; ending without it would be the one moment
    that implies otherwise.
    """
    first = (name or "").strip().split(" ")[0]
    body = (pool_closing or "That's everything. Thank you for your time.").strip()
    # Address them by name without restating whatever the pool's own closing
    # says. A fixed "That's everything from me" lead in front of a pool closing
    # that also opens "That's everything" produced exactly that stutter.
    if first and body:
        body = f"{first}, {body[0].lower()}{body[1:]}"
    return (
        body.rstrip()
        + " Your answers go to the hiring team, and a person reviews them "
        "before any decision is made. You can close this window now — "
        "there's nothing else you need to do."
    )
