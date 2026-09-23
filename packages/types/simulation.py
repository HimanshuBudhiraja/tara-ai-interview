"""The configuration groups a simulation is assembled from.

These are the fields a configurer fills in. Each group maps to one part of the
Scenario Builder and to one section of the assembled prompt, and they are kept
apart from `AgentSpec` on one rule: changing any of these produces a different
*simulation*; changing the agent produces a different *kind of assessment*.

Everything here is frozen into the published scenario version, so a session can
be replayed against the exact configuration it ran under — including the
guardrails, which are as much part of what was measured as the questions are.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DIFFICULTIES = ("easy", "medium", "hard")
CHANNELS = ("voice", "chat")
#: Where the scene's opening words come from. "Standard" is the platform's
#: authored introduction; "custom" is the administrator's own script, read
#: verbatim. There is no third mode where a model writes the introduction: the
#: briefing is the instruction set for the exercise, and a generated one would
#: mean two people sat measurably different assessments.
INTRO_MODES = ("standard", "custom")


# --------------------------------------------------------------------------- #
#  Situation
# --------------------------------------------------------------------------- #
@dataclass
class SituationSpec:
    """What is going on. Section 5 of the prompt.

    Split into named fields rather than one blob because each one answers a
    different question the agent has to be able to act on, and because a
    configurer given a single "description" box writes three sentences and
    leaves out the objective every time.
    """

    background: str = ""
    current_situation: str = ""
    business_context: str = ""
    #: The counterparty's own context — who they are in this, what has already
    #: happened to them. Distinct from the persona, which is who they ARE.
    counterparty_context: str = ""
    objective: str = ""
    expected_outcome: str = ""
    known_challenges: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.current_situation.strip():
            problems.append(
                "The situation needs a current state — what is happening right now "
                "is what the person walks into."
            )
        if not self.objective.strip():
            problems.append(
                "The situation needs an objective. Without one there is nothing to "
                "have handled well or badly."
            )
        return problems


@dataclass
class CandidateBrief:
    """The subject's side. Section 9.

    What they are told, which is deliberately less than the counterparty knows.
    The gap between the two is where the assessment lives: a subject who has
    been told the customer's hidden concern has nothing left to discover.
    """

    role: str = ""
    objective: str = ""
    #: Facts the subject has going in. Anything the counterparty knows that is
    #: not here is something the subject has to surface.
    information_available: list[str] = field(default_factory=list)
    #: What good looks like, in observable terms. Feeds the evaluation, and is
    #: never shown to the subject or to the agent.
    expected_behaviours: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        if not self.objective.strip():
            return ["The subject needs an objective — otherwise they are just chatting."]
        return []


# --------------------------------------------------------------------------- #
#  Script
# --------------------------------------------------------------------------- #
@dataclass
class ScriptSpec:
    """Everything said to the subject outside the scene itself. Section 8.

    The platform does not force one introduction. An administrator running a
    known cohort through a known exercise has context the platform does not,
    and a standard welcome that contradicts what their people were told in the
    room is worse than no welcome.

    Whichever mode is chosen, these lines are authored and spoken verbatim —
    they are the only part of the experience guaranteed identical for everyone,
    which is exactly what makes the rest of it comparable.
    """

    intro_mode: str = "standard"
    welcome: str = ""
    scenario_instructions: str = ""
    candidate_instructions: str = ""
    #: Said when the scene moves on — between beats, or into the close.
    transition: str = ""
    closing: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.intro_mode not in INTRO_MODES:
            problems.append(f"Introduction mode must be one of {', '.join(INTRO_MODES)}.")
        if self.intro_mode == "custom" and not (self.welcome.strip() or self.scenario_instructions.strip()):
            problems.append(
                "Custom introduction selected but no script written — the subject "
                "would be dropped into the scene with nothing said to them."
            )
        return problems


# --------------------------------------------------------------------------- #
#  Interaction
# --------------------------------------------------------------------------- #
@dataclass
class InteractionConfig:
    """How the session runs. Section 11.

    `max_turns` and `session_timeout_sec` are both here and both enforced,
    because they fail differently: a subject who talks in circles hits the turn
    cap, and one who walks away from the microphone hits the clock. A session
    with only one of the two has a way to run forever.
    """

    channel: str = "voice"
    #: Multiplier on the voice's natural pace. Bounded by the same range the
    #: interview path uses — outside it delivery starts to affect how well
    #: someone can respond, which would make the pace part of the assessment.
    speech_rate: float = 0.95
    #: Words per agent turn. Overrides the agent's default when set above zero.
    max_response_words: int = 0
    max_turns: int = 24
    session_timeout_sec: int = 900

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.channel not in CHANNELS:
            problems.append(f"Channel must be one of {', '.join(CHANNELS)}.")
        if not 0.75 <= self.speech_rate <= 1.1:
            problems.append("Speech rate must sit between 0.75 and 1.1.")
        if self.max_turns < 1:
            problems.append("A session needs at least one turn.")
        if self.session_timeout_sec < 60:
            problems.append("A session timeout under a minute will end scenes mid-sentence.")
        return problems


# --------------------------------------------------------------------------- #
#  Evaluation
# --------------------------------------------------------------------------- #
@dataclass
class CompetencySpec:
    """One thing being measured, and how much it counts."""

    id: str
    label: str
    weight: float = 1.0
    #: What this competency looks like when someone has it. The scoring key at
    #: the competency level; beats carry the turn-level version.
    observable_behaviours: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.weight <= 0:
            problems.append(f"Competency '{self.id}' has no weight — remove it or weight it.")
        if not self.observable_behaviours:
            problems.append(
                f"Competency '{self.id}' has no observable behaviours. A competency "
                "nobody can point at in a transcript cannot be defended in a review."
            )
        return problems


@dataclass
class EvaluationConfig:
    """Section 12's configurable half. The framework comes from the agent."""

    competencies: list[CompetencySpec] = field(default_factory=list)
    scoring_scale: int = 4
    #: Named on the report so it states the rule it was produced under. Beats
    #: the subject never reached are excluded from the denominator — they are
    #: missing evidence, not a zero, and nobody loses points for running out of time.
    unreached_policy: str = "excluded"

    def weights(self) -> dict[str, float]:
        total = sum(c.weight for c in self.competencies) or 1.0
        return {c.id: c.weight / total for c in self.competencies}

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.competencies:
            problems.append("Nothing is being evaluated.")
        seen: set[str] = set()
        for c in self.competencies:
            if c.id in seen:
                problems.append(f"Duplicate competency '{c.id}'.")
            seen.add(c.id)
            problems.extend(c.validate())
        return problems


# --------------------------------------------------------------------------- #
#  Guardrails
# --------------------------------------------------------------------------- #
@dataclass
class ScenarioGuardrails:
    """The configurable half. Section 10's second block.

    Everything here NARROWS what the agent may do. Nothing here widens it: the
    permanent guardrails on `AgentSpec` are a property with no setter, so a
    configurer composing a simulation can tighten the rules and cannot reach
    the floor. Handing a scenario author a switch labelled "prompt injection
    protection: off" is how an assessment platform ends up with a scenario that
    can be talked out of its own scoring key.
    """

    restricted_topics: list[str] = field(default_factory=list)
    allowed_topics: list[str] = field(default_factory=list)
    #: Which knowledge sources this simulation may draw on. Empty means all of
    #: the selected base. A discovery scenario that withholds the price list is
    #: the ordinary case.
    allowed_sources: list[str] = field(default_factory=list)
    #: Redundant with the permanent floor, and deliberately so: they are the two
    #: a configurer most often wants to confirm are on, and a setting that reads
    #: as absent is one people assume is off.
    no_coaching: bool = True
    no_hints: bool = True
    #: What ends the scene early, beyond the turn and time caps.
    exit_conditions: list[str] = field(default_factory=list)
    #: Extra rules for this character, in the author's words.
    persona_boundaries: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.no_coaching or not self.no_hints:
            problems.append(
                "Coaching and hints cannot be switched on in an assessment: an agent "
                "that helps has told the subject the answer."
            )
        overlap = set(self.restricted_topics) & set(self.allowed_topics)
        if overlap:
            problems.append(
                f"Topics both allowed and restricted: {', '.join(sorted(overlap))}."
            )
        return problems

    def rules(self) -> list[str]:
        """The configured rules, as prompt lines."""
        out: list[str] = []
        if self.allowed_topics:
            out.append(
                "Stay on these topics: " + "; ".join(self.allowed_topics) + "."
            )
        if self.restricted_topics:
            out.append(
                "Never discuss, raise or engage with: "
                + "; ".join(self.restricted_topics) + "."
            )
        out.extend(self.persona_boundaries)
        return out
