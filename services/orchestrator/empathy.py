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


def greeting(name: str, role_title: str, question_count: int) -> str:
    first = (name or "").strip().split(" ")[0]
    hello = f"Hi {first}, " if first else "Hello, "
    return (
        f"{hello}I'm Tara. I'll be running your interview for the {role_title} role today. "
        f"It's a conversation, not a test — I'll ask you around {question_count} questions about "
        "real support situations, and I may follow up on things you say. "
        "Answer in your own words, and take as long as you need. "
        "If you'd like me to repeat anything, just ask."
    )


def closing(name: str, pool_closing: str) -> str:
    first = (name or "").strip().split(" ")[0]
    lead = f"That's everything from me, {first}. " if first else "That's everything from me. "
    return lead + pool_closing
