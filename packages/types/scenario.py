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

from packages.types.simulation import (
    CandidateBrief,
    EvaluationConfig,
    InteractionConfig,
    ScenarioGuardrails,
    ScriptSpec,
    SituationSpec,
)

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

    #: What this character is actually worried about, under the thing they say
    #: they are worried about. NEVER volunteered — it comes out only if the
    #: subject asks the right question. This field is the assessment: a persona
    #: with no hidden concern can be handled by anyone who listens politely.
    hidden_concerns: list[str] = field(default_factory=list)
    #: What has to happen for this character to calm down, harden, or walk. The
    #: alternative is a persona that escalates because the model felt like it,
    #: which makes two runs of one scenario different exercises.
    escalation_behaviour: str = ""

    #: Dials, 1-5. They exist so one authored character can be reused across
    #: difficulties without rewriting it — the same CFO at aggressiveness 2 and
    #: at 5 is the same person having a better or worse day.
    aggressiveness: int = 3
    cooperation: int = 3
    escalation_tendency: int = 3

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
        for label, dial in (
            ("aggressiveness", self.aggressiveness),
            ("cooperation", self.cooperation),
            ("escalation tendency", self.escalation_tendency),
        ):
            if not 1 <= dial <= 5:
                problems.append(f"Persona {label} must sit between 1 and 5.")
        return problems

    def dial_rules(self) -> list[str]:
        """The dials, rendered as behaviour the model can act on.

        Numbers mean nothing to a language model — "aggressiveness: 4" is read
        as flavour. The band it falls in, written as an instruction, is not.
        """
        out: list[str] = []
        if self.aggressiveness >= 4:
            out.append("You are sharp and confrontational. You interrupt and you do not soften.")
        elif self.aggressiveness <= 2:
            out.append("You are even-tempered. You raise things plainly rather than pushing.")
        if self.cooperation >= 4:
            out.append("You answer what you are asked and you help the conversation along.")
        elif self.cooperation <= 2:
            out.append("You give short answers and volunteer nothing. Make them ask.")
        if self.escalation_tendency >= 4:
            out.append("You reach for escalation quickly — a manager, cancelling, walking away.")
        elif self.escalation_tendency <= 2:
            out.append("You stay in the conversation rather than threatening to leave it.")
        return out


# --------------------------------------------------------------------------- #
#  The spine
# --------------------------------------------------------------------------- #
#: Cues that describe an ABSENCE. A classifier asked whether an answer "covered"
#: `does not offer a discount` will almost never say yes — there is nothing in
#: the words to match — so a negative cue can never be satisfied. It then sits
#: in the threshold forever and stalls the beat, which is how a strong performer
#: ends up credited with nothing.
#:
#: The schema already has the right home for these: a thing the subject must NOT
#: do is a red flag. Caught at authoring time because the failure is silent —
#: the scenario runs, the scene plays, and only the scores are wrong.
_NEGATIVE_OPENERS = (
    "does not", "doesn't", "did not", "didn't", "never", "avoids", "avoid",
    "no ", "not ", "refrains", "without offering", "fails to",
)


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
        for cue in self.looking_for:
            lowered = cue.strip().lower()
            if any(lowered.startswith(neg) for neg in _NEGATIVE_OPENERS):
                problems.append(
                    f"Beat '{self.id}' has a negative signal: \"{cue}\". A signal "
                    "describes something the subject SAYS, and an absence can never "
                    "be matched — it would sit in the threshold and stall the beat. "
                    "Move it to red_flags."
                )
        if self.threshold > len(self.looking_for):
            problems.append(
                f"Beat '{self.id}' needs {self.required_signals} signals but only "
                f"has {len(self.looking_for)} — it can never be satisfied."
            )
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

    #: Which library agent runs this. The scenario configures an agent; it
    #: never replaces one. An unknown type is caught at validation rather than
    #: at the first turn.
    agent_type: str = "customer_service"
    difficulty: str = "medium"

    policy: AssessmentPolicy = field(default_factory=AssessmentPolicy)
    persona: PersonaSpec = field(default_factory=PersonaSpec)
    beats: list[BeatSpec] = field(default_factory=list)

    situation: SituationSpec = field(default_factory=SituationSpec)
    candidate: CandidateBrief = field(default_factory=CandidateBrief)
    script: ScriptSpec = field(default_factory=ScriptSpec)
    interaction: InteractionConfig = field(default_factory=InteractionConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    guardrails: ScenarioGuardrails = field(default_factory=ScenarioGuardrails)

    #: Which knowledge base grounds this simulation, and which version of it.
    #: Pinned, so a report can be read knowing which price list the buyer was
    #: arguing from.
    knowledge_base_id: str = ""
    knowledge_base_version: int = 0

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
        problems.extend(self.situation.validate())
        problems.extend(self.candidate.validate())
        problems.extend(self.script.validate())
        problems.extend(self.interaction.validate())
        problems.extend(self.evaluation.validate())
        problems.extend(self.guardrails.validate())
        if self.difficulty not in ("easy", "medium", "hard"):
            problems.append(f"Unknown difficulty '{self.difficulty}'.")

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
        from packages.types.simulation import CompetencySpec

        policy = AssessmentPolicy(**(data.pop("policy", {}) or {}))
        persona = PersonaSpec(**(data.pop("persona", {}) or {}))
        beats = [BeatSpec(**b) for b in (data.pop("beats", []) or [])]
        situation = SituationSpec(**(data.pop("situation", {}) or {}))
        candidate = CandidateBrief(**(data.pop("candidate", {}) or {}))
        script = ScriptSpec(**(data.pop("script", {}) or {}))
        interaction = InteractionConfig(**(data.pop("interaction", {}) or {}))
        guardrails = ScenarioGuardrails(**(data.pop("guardrails", {}) or {}))

        raw_eval = dict(data.pop("evaluation", {}) or {})
        competencies = [CompetencySpec(**c) for c in raw_eval.pop("competencies", []) or []]
        evaluation = EvaluationConfig(**raw_eval)
        evaluation.competencies = competencies

        known = set(ScenarioDefinition.__dataclass_fields__)
        defn = ScenarioDefinition(**{k: v for k, v in data.items() if k in known})
        defn.policy = policy
        defn.persona = persona
        defn.beats = beats
        defn.situation = situation
        defn.candidate = candidate
        defn.script = script
        defn.interaction = interaction
        defn.evaluation = evaluation
        defn.guardrails = guardrails
        return defn
