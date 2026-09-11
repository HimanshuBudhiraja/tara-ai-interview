"""A deterministic stand-in for the question generator.

Exists because the whole feature — generation, validation, persistence, editing,
regeneration, coverage, the review screen — has to be verifiable with no
provider. It is not a mock in the test-double sense: it produces realistic,
schema-valid, guardrail-passing questions from the slot it is given, so the code
paths it exercises are the real ones.

Two properties it is written to have:

  * **Deterministic.** The same slot produces the same question, so a test that
    passes today passes tomorrow and a regeneration is visibly a regeneration.
  * **Honest.** It never stands in for a real provider failure. Turning it on is
    an explicit choice (`TARA_QUESTION_STUB=1`); with it off and no key, a
    generation attempt fails with a retryable error rather than quietly
    succeeding with invented content.
"""
from __future__ import annotations

import hashlib
import re

from packages.types import new_id
from packages.types.definition import CriterionSpec, InterviewDefinition, QuestionSpec
from services.assessment.blueprint import BASE_ANSWER_SEC, Slot
from services.ai.workloads.question_writer import SlotResult

#: Question stems by type. Chosen so the generated text is plausibly spoken and
#: reads differently per type — a behavioural question and a technical one must
#: not come out as the same sentence with a different label.
#: Several per type, and one is used per position within a slot, so a slot
#: asked for five questions returns five different ones. A stub that repeats
#: itself would make the duplicate detector look broken.
_STEMS: dict[str, list[str]] = {
    "behavioral": [
        "Tell me about a time you had to {task_lower}. What did you actually do?",
        "Walk me through the last time you tried to {task_lower} and it went wrong.",
        "Describe a time you had to {task_lower} and someone disagreed with your approach.",
        "When did you last change how you {task_lower}? What prompted it?",
        "Tell me about a time you had to {task_lower} under more pressure than usual.",
    ],
    "situational": [
        "You're asked to {task_lower}, and it isn't going the way you expected. Walk me through what you do.",
        "Suppose you have to {task_lower} today and something is blocking it. How do you approach that?",
        "Someone asks you to {task_lower} with half the context missing. What do you ask for first?",
        "Two people disagree about how to {task_lower}. How do you settle it?",
        "You have an hour to {task_lower} and it normally takes a day. What do you cut?",
    ],
    "technical": [
        "How do you think about {skill_lower} when you {task_lower}? Talk me through your reasoning.",
        "What would you check first if you tried to {task_lower} and it failed intermittently?",
        "Where does {skill_lower} usually break down in practice, and how do you design around it?",
        "What trade-off do you accept when you {task_lower}, and what does it cost you?",
        "How would you know something had gone wrong when you {task_lower}, before anyone told you?",
    ],
    "task_based": [
        "Walk me through how you'd {task_lower}, and where you'd expect it to break.",
        "If you had to {task_lower} tomorrow, what's the first decision you'd make and what would it depend on?",
        "Take me through how you'd {task_lower} end to end. Where would you put the most care?",
        "What would have to be true before you'd be comfortable to {task_lower} again?",
        "If you had to {task_lower} under real load, what changes about how you'd do it?",
    ],
}

_CUES = [
    "names the specific decision they made",
    "explains the reasoning behind that decision",
    "describes a trade-off they accepted",
    "states what the outcome was",
]

_PROBES = [
    "What made you choose that over the alternative?",
    "How did you know it had worked?",
    "What would you do differently if it came up again?",
    "Who else did you need to bring in, and when?",
]


def _pick(options: list[str], seed: str) -> str:
    index = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % len(options)
    return options[index]


def generate_slot(
    slot: Slot,
    definition: InterviewDefinition,
    *,
    job_description: str = "",
    session_id: str = "_questions",
) -> SlotResult:
    """Same signature as the real generator, so nothing calling it knows."""
    skill = definition.skill(slot.skill_id)
    if skill is None:
        return SlotResult(slot.id, error=f"skill {slot.skill_id} is no longer in this interview")
    task = next((t for t in definition.tasks if t.id == slot.task_id), None)

    # Task labels are noun phrases ("Design payment capture"), so every stem
    # here is written to take one as a noun. "Take review a design" was what
    # happened when they were not.
    task_label = (task.label if task else skill.name).rstrip(".")
    task_lower = task_label[0].lower() + task_label[1:] if task_label else skill.name.lower()

    questions: list[QuestionSpec] = []
    for n in range(slot.count):
        seed = f"{slot.id}:{n}:{skill.name}"
        stems = _STEMS.get(slot.question_type, _STEMS["situational"])
        # Position within the slot picks the stem, so the questions a slot
        # returns differ from each other by construction.
        offset_base = int(hashlib.sha1(slot.id.encode("utf-8")).hexdigest(), 16)
        stem = stems[(offset_base + n) % len(stems)]
        text = stem.format(task_lower=task_lower, skill_lower=skill.name.lower())

        # A skill is often needed by more than one task, so a slot may sit on a
        # task whose name says nothing about the skill being assessed — "how
        # would you reconcile settlements", filed under Idempotent design. The
        # real generator is told both and weaves them together; the stub
        # anchors the skill explicitly so the question matches the heading it
        # appears under.
        # Compared on stems, not on the whole name: "Design review" is already
        # present in "review a peer design", and bolting the skill name onto
        # that reads as a machine talking. Only anchor when the skill's own
        # vocabulary is genuinely absent.
        skill_stems = {w[:6] for w in re.findall(r"[a-z]{4,}", skill.name.lower())}
        text_stems = {w[:6] for w in re.findall(r"[a-z]{4,}", text.lower())}
        if skill_stems and not (skill_stems & text_stems):
            text = text.rstrip("?.") + f", and where does {skill.name.lower()} come into it?"

        offset = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
        cues = [_CUES[(offset + i) % len(_CUES)] for i in range(3)]
        probes = [_PROBES[(offset + i) % len(_PROBES)] for i in range(3)]

        questions.append(
            QuestionSpec(
                id=new_id("q"),
                question_text=text,
                competency=slot.skill_id,
                skill_id=slot.skill_id,
                secondary_skill_ids=[],
                task_id=slot.task_id,
                question_type=slot.question_type,
                difficulty=(
                    slot.difficulties[n] if n < len(slot.difficulties) else slot.difficulty
                ),
                expected_signal=(
                    f"Shows they can {task_lower} and can say why they did it that way."
                ),
                looking_for=cues,
                evaluation_criteria=[
                    CriterionSpec(
                        id=new_id("crit"),
                        label=f"{skill.name}: decision quality",
                        description=(
                            skill.assessment_scope
                            or f"Whether the answer demonstrates {skill.name.lower()} in practice."
                        ),
                        importance=skill.priority,
                    ),
                    CriterionSpec(
                        id=new_id("crit"),
                        label="Evidence and specifics",
                        description=(
                            "Whether the answer is grounded in something that actually "
                            "happened rather than described in general terms."
                        ),
                        importance="medium",
                    ),
                ],
                probe_eligible=True,
                max_probes=2,
                time_budget_sec=BASE_ANSWER_SEC.get(
                    slot.difficulties[n] if n < len(slot.difficulties) else slot.difficulty, 90
                ),
                probe_bank=probes,
                clarify=(
                    f"I'm asking about how you'd approach {task_lower} in practice — "
                    f"whatever comes to mind is fine, there's no format I'm after."
                ),
                type="scenario",
                source="generated",
                slot_id=slot.id,
            )
        )
    return SlotResult(slot.id, questions=questions)
