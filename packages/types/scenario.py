"""`ScenarioDefinition` — the role-play contract.

`InterviewDefinition` answers "what do we ask this person?". This answers a
different question: "what situation do we put this person IN, and what does
handling it well look like?".

Why a second contract rather than a question type
-------------------------------------------------
A situational judgement test is a *low-fidelity* simulation, and the literature
says so plainly: the candidate is never placed in the work setting and is never
asked to perform the behaviour, only to read a stem and pick an option. Pooled
predictive validity sits around .26. Assessment centres — where the person
actually performs, live, against a counterparty — carry incremental validity
over both cognitive ability and SJTs years out, and are rare only because a
human assessor per candidate does not scale.

A role-play run by a machine is the high-fidelity version at the low-fidelity
price. That is the whole product thesis, and it needs the person to DO the
thing, which means the unit of content is a situation with a counterparty in
it — not a question with an answer key.

What stays the same as the Q&A path
-----------------------------------
Everything that makes a score defensible:

  * The scenario is **authored before anyone sits it** and frozen with the
    version. Beats, their required signals and their scoring key are written by
    a subject-matter expert, exactly as an SJT item is.
  * **The model never authors assessment content.** It speaks in character
    between authored beats — nothing more. The beat spine is the item; the
    improvisation is delivery.
  * **The counterparty is never told the scoring key.** A persona that knows
    the rubric starts steering the subject into it, and a simulation that leads
    the witness measures the simulation.

What is new
-----------
`AssessmentPolicy`. One engine serves three products — Hiring Readiness, Sales
Readiness, CS Readiness — and almost everything that differs between them is
policy rather than mechanism: who sees the score, how many attempts there are,
whether the subject is a candidate or an employee. Branching on the surface
inside the turn loop would fork the engine three ways and guarantee the three
drift apart. Branching on a policy object keeps one loop.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: The three products this engine serves.
#:
#: Named for what is being established, not for the department that buys it —
#: the same CS scenario is used to hire an agent and to certify one, and only
#: the policy around it differs.
SURFACES = ("hiring", "sales", "cs")

#: Who the subject is. This is not cosmetic: a candidate has no employment
#: relationship, which changes what may be stored, for how long, and what has
#: to be disclosed before recording.
SUBJECTS = {"hiring": "candidate", "sales": "employee", "cs": "employee"}

#: What the subject may see about their own performance.
#:
#:   hidden  a selection decision. The subject is told the session is complete
#:           and nothing else. Showing a candidate their score turns a rejection
#:           into an argument and teaches the next candidate what to say.
#:   gated   a certification. Pass or fail, plus the rubric lines they missed —
#:           enough to fix it before the retake, not enough to game it.
#:   full    practice. Everything, immediately. The feedback loop IS the value;
#:           a practice tool that withholds feedback is a test.
FEEDBACK_VISIBILITY = ("hidden", "gated", "full")

#: `attempts = 0` means unlimited. Practice has to be unlimited or people stop
#: practising; selection has to be one-shot or it is not a measurement.
UNLIMITED = 0


@dataclass
class AssessmentPolicy:
    """What this scenario is FOR, expressed as rules the engine can read.

    The defaults are the safe ones — a one-shot, nothing-disclosed selection
    event — because a policy field that nobody set should not accidentally show
    a candidate their score.
    """

    surface: str = "hiring"
    feedback_visibility: str = "hidden"
    #: How many times one person may sit this scenario. 0 = unlimited.
    attempts: int = 1
    #: Minimum wait between attempts. A retake ten seconds after a failure is
    #: the same performance with better luck, not a second measurement.
    cooldown_hours: int = 0
    #: Whether the scenario may be used to make a selection decision. Kept
    #: separate from `surface` because it is the field that pulls in the
    #: obligations — adverse-impact monitoring, retention, disclosure — and a
    #: reviewer should be able to find every selection-grade artefact by
    #: filtering on one boolean rather than inferring it.
    selection_grade: bool = True

    @property
    def subject(self) -> str:
        return SUBJECTS.get(self.surface, "candidate")

    @property
    def shows_score_to_subject(self) -> bool:
        return self.feedback_visibility in ("gated", "full")

    @property
    def unlimited_attempts(self) -> bool:
        return self.attempts == UNLIMITED

    def attempt_allowed(self, attempts_taken: int) -> bool:
        return self.unlimited_attempts or attempts_taken < self.attempts

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.surface not in SURFACES:
            problems.append(f"Surface must be one of {', '.join(SURFACES)}.")
        if self.feedback_visibility not in FEEDBACK_VISIBILITY:
            problems.append(
                f"Feedback visibility must be one of {', '.join(FEEDBACK_VISIBILITY)}."
            )
        if self.attempts < 0:
            problems.append("Attempts cannot be negative.")
        # The combination that would quietly turn a hiring gate into a coaching
        # tool, or a coaching tool into an undisclosed hiring gate.
        if self.selection_grade and self.feedback_visibility == "full":
            problems.append(
                "A selection-grade scenario cannot show full feedback: the second "
                "candidate to sit it would know the scoring key."
            )
        if self.selection_grade and self.unlimited_attempts:
            problems.append(
                "A selection-grade scenario cannot allow unlimited attempts — "
                "an unlimited retake measures persistence, not the skill."
            )
        return problems


# --------------------------------------------------------------------------- #
#  The counterparty
# --------------------------------------------------------------------------- #
@dataclass
class PersonaSpec:
    """Who Tara plays. Authored, never inferred.

    `wants` and `knows` are what make the persona hold together across turns
    without being told the scoring key. A counterparty with a goal behaves
    consistently; a counterparty with a rubric behaves like a rubric.
    """

    name: str = ""
    role: str = ""
    #: How they come across. One line, in plain words — "clipped, already
    #: annoyed, not shouting" beats a list of adjectives for a voice model.
    disposition: str = ""
    #: What this person is trying to get out of the conversation.
    wants: str = ""
    #: What they know and would say if asked. Anything not here, the persona
    #: does not know — which is what stops it inventing account details that
    #: contradict the scenario.
    knows: list[str] = field(default_factory=list)
    #: Things this character would never say or do, on top of the global
    #: guardrails. Scenario-specific: a customer who has already been told the
    #: refund policy must not forget it halfway through.
    never: list[str] = field(default_factory=list)
    #: The first thing they say once the scene opens.
    opening_line: str = ""
    voice: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.name.strip():
            problems.append("The persona needs a name.")
        if not self.opening_line.strip():
            problems.append(
                f"Persona '{self.name or '?'}' needs an opening line — the scene "
                "has to start with the counterparty speaking, not with silence."
            )
        if not self.wants.strip():
            problems.append(
                f"Persona '{self.name or '?'}' needs a goal. A counterparty with "
                "nothing to want cannot stay in character for five minutes."
            )
        return problems


# --------------------------------------------------------------------------- #
#  The spine
# --------------------------------------------------------------------------- #
@dataclass
class BeatSpec:
    """One authored moment the scene must reach.

    This is the assessed unit — the SJT item, expressed as something that
    happens rather than something that is asked. "The customer reveals they
    were charged twice and threatens to cancel" is a beat. Whatever the subject
    says in response is the response option, except they have to write it
    themselves, out loud, under time pressure.

    `looking_for` is the scoring key, in the same shape the Q&A path already
    uses, so the existing answer classifier reads a beat exactly as it reads a
    question and nothing new has to be calibrated.
    """

    id: str
    #: What the counterparty does here, in the author's words. Given to the
    #: persona as an instruction for this turn. Never spoken verbatim.
    intent: str
    #: Which skill this beat evidences. One, for the same reason a question has
    #: one primary skill: a beat counted against two skills satisfies two
    #: floors with one piece of evidence.
    skill_id: str = ""
    #: The signals a good response contains. The scoring key.
    looking_for: list[str] = field(default_factory=list)
    #: Signals that count against — the trap this beat is built around. An SJT
    #: item without a plausible wrong answer is not discriminating anything.
    red_flags: list[str] = field(default_factory=list)
    #: Authored lines the counterparty falls back to when a generated line is
    #: rejected by the guardrails, or when there is no model at all. The scene
    #: degrades to authored dialogue, never to silence.
    fallback_lines: list[str] = field(default_factory=list)
    #: How many exchanges this beat gets before the scene moves on regardless.
    #: Every beat terminates. A subject who cannot get past the objection must
    #: still reach the end of the scenario — an interview that strands someone
    #: on turn three has measured the strand, not the skill.
    max_turns: int = 3
    #: How many `looking_for` signals must be covered for the beat to count as
    #: handled. 0 means "any one of them".
    required_signals: int = 1
    #: Beats an author marks optional are skipped when the budget runs short.
    optional: bool = False

    @property
    def threshold(self) -> int:
        if self.required_signals > 0:
            return min(self.required_signals, max(1, len(self.looking_for)))
        return 1

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.intent.strip():
            problems.append(f"Beat '{self.id}' has no intent — nothing happens in it.")
        if not self.looking_for:
            problems.append(
                f"Beat '{self.id}' has no scoring key. A beat nobody can pass or "
                "fail is set dressing; mark it optional or give it signals."
            )
        if self.max_turns < 1:
            problems.append(f"Beat '{self.id}' must allow at least one exchange.")
        return problems


@dataclass
class ScenarioDefinition:
    """The frozen, publishable role-play. The analogue of `InterviewDefinition`.

    Same two guarantees as its Q&A sibling, for the same reasons: a published
    scenario never changes (editing produces a new version), and a subject is
    pinned to the version they started, so a session recorded in March can be
    replayed and re-scored against the key it was judged on.
    """

    scenario_id: str = ""
    version: int = 0
    title: str = ""
    #: What the subject is told before the scene starts. Their side of the
    #: situation — the role they are playing, what they are walking into.
    #: Everything the counterparty knows that this does not say is a surprise,
    #: which is the point.
    briefing: str = ""
    role: str = ""
    role_title: str = ""
    language: str = "en"

    policy: AssessmentPolicy = field(default_factory=AssessmentPolicy)
    persona: PersonaSpec = field(default_factory=PersonaSpec)
    beats: list[BeatSpec] = field(default_factory=list)

    #: Reuses `SkillSpec` from the interview contract deliberately. A skill is
    #: the same object whether it was evidenced by an answer or by a handled
    #: beat — that is what lets one person's hiring role-play and their later
    #: certification sit on the same skill profile.
    skills: list[Any] = field(default_factory=list)

    #: Wall-clock ceiling. A role-play with no clock runs until someone gives up.
    max_duration_min: int = 8
    #: Total exchanges across the whole scene, whatever the per-beat caps allow.
    turn_budget: int = 24
    #: Whether the counterparty's lines may be generated at all. Off means the
    #: scene runs entirely on authored fallbacks — useful for a scenario under
    #: legal review, and the reason the engine must work without a model.
    allow_generated_dialogue: bool = True

    opening: str = ""
    closing: str = "That's the end of the scenario. Thank you."

    # ------------------------------------------------------------------ #
    def beat(self, beat_id: str) -> BeatSpec | None:
        return next((b for b in self.beats if b.id == beat_id), None)

    def beat_index(self, beat_id: str) -> int:
        for i, b in enumerate(self.beats):
            if b.id == beat_id:
                return i
        return -1

    def next_beat(self, beat_id: str | None) -> BeatSpec | None:
        """The beat after this one, or the first if there is no current beat."""
        if beat_id is None:
            return self.beats[0] if self.beats else None
        idx = self.beat_index(beat_id)
        if idx < 0 or idx + 1 >= len(self.beats):
            return None
        return self.beats[idx + 1]

    def skill_ids(self) -> list[str]:
        seen: list[str] = []
        for b in self.beats:
            if b.skill_id and b.skill_id not in seen:
                seen.append(b.skill_id)
        return seen

    def beats_for_skill(self, skill_id: str) -> list[BeatSpec]:
        return [b for b in self.beats if b.skill_id == skill_id]

    # ------------------------------------------------------------------ #
    #  Validation — before publish, never during a session
    # ------------------------------------------------------------------ #
    def validate(self) -> list[str]:
        """Every problem at once, in author-readable language."""
        problems: list[str] = []
        if not self.title.strip():
            problems.append("The scenario needs a title.")
        if not self.briefing.strip():
            problems.append(
                "The scenario needs a briefing. Dropping someone into a scene "
                "with no idea who they are measures confusion."
            )
        problems.extend(self.policy.validate())
        problems.extend(self.persona.validate())

        if not self.beats:
            problems.append("A scenario with no beats is a conversation, not an assessment.")
        seen: set[str] = set()
        for b in self.beats:
            if b.id in seen:
                problems.append(f"Duplicate beat id '{b.id}'.")
            seen.add(b.id)
            problems.extend(b.validate())

        # The turn budget has to be able to reach the last required beat, or the
        # scenario is guaranteed to time out before it finishes assessing.
        floor = sum(1 for b in self.beats if not b.optional)
        if self.turn_budget < floor:
            problems.append(
                f"Turn budget {self.turn_budget} cannot reach {floor} required "
                "beats — the scene would end before the last one happens."
            )
        if self.max_duration_min < 1:
            problems.append("A scenario needs at least a minute to run in.")
        return problems

    # -- serialisation ------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ScenarioDefinition":
        data = dict(d)
        policy = AssessmentPolicy(**(data.pop("policy", {}) or {}))
        persona = PersonaSpec(**(data.pop("persona", {}) or {}))
        beats = [BeatSpec(**b) for b in (data.pop("beats", []) or [])]
        known = set(ScenarioDefinition.__dataclass_fields__)
        defn = ScenarioDefinition(**{k: v for k, v in data.items() if k in known})
        defn.policy = policy
        defn.persona = persona
        defn.beats = beats
        return defn
