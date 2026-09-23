"""The role-play loop — the second turn engine.

`engine.py` runs the question lifecycle: select → deliver → capture →
probe-or-advance → assemble. This runs the scene lifecycle:

    open → in-character turn → read → hold-or-advance → close

The two are siblings, not layers. They share the session store, the guardrails,
the answer classifier and the evidence shape; they differ in what a turn *is*.
In an interview Tara asks and the candidate answers. In a role-play nobody asks
anything — a situation happens, and what the subject does about it is the
evidence.

Three properties are worth stating because they shaped the file.

**The beat spine is authored; only the dialogue is generated.** Which moments
occur, in what order, and what a good response to each contains are written by a
subject-matter expert and frozen with the version. The model chooses words
inside a beat and never chooses the beats. That is what keeps a role-play an
assessment instrument rather than a conversation that happened to be recorded —
two people sitting the same scenario meet the same situation.

**Speaking and judging are different calls.** The counterparty writes the line;
the answer classifier reads the subject's turn against the authored key. The
improvising model is never asked whether the subject did well, and is never
shown what "well" means. This is the same split the Q&A path already uses
between `followup_generator` and `answer_classifier`, and it is the reason a
creative counterparty cannot inflate a score.

**Every beat terminates.** A beat has a turn cap, the scene has a turn budget,
and both are enforced here rather than trusted to the model. A subject who never
gets past the objection still reaches the end of the scenario — a session that
strands someone on beat two has measured the stranding.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from packages.types.agent import AgentSpec, get_agent
from packages.types.knowledge import KnowledgeBase
from packages.types.scenario import BeatSpec, ScenarioDefinition
from services.ai import prompt_assembly
from services.ai.brain import get_llm
from services.ai.workloads import counterparty
from services.orchestrator import guardrails

RolePhase = Literal["created", "briefing", "in_scene", "closing", "complete", "abandoned"]
RoleSpeech = Literal["briefing", "in_character", "narration", "closing", "hold"]


# --------------------------------------------------------------------------- #
#  Red-flag detection
#
#  Deterministic on purpose. A red flag is shown to a REVIEWER as "this is what
#  they said, here is why it was flagged", so it has to be explainable without
#  re-running a model, and identical on a replay two years later. It is a
#  pointer for a human, never a subtraction from a score.
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[a-z']+")


def _stem(word: str) -> str:
    return word[:6]


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall((text or "").lower()) if len(w) > 2}


def red_flags_in(said: str, red_flags: list[str]) -> list[str]:
    """Verbatim red-flag matches — the net under the classifier, not the catch.

    Authored flags describe a behaviour in the third person ("asks the customer
    to check their own bank statement first") while a subject speaks in the
    first ("can you check your bank statement?"). Word overlap between those two
    is near zero, so this alone would miss almost every real flag — which is
    exactly what the first weak-persona run showed.

    So the semantic read does the work (see `_read`, which passes the flags
    through the same classifier as the scoring key) and this stays as a cheap
    deterministic backstop for the phrasings that DO land verbatim, and for
    runs with no model behind them at all. Two thirds of a flag's content words
    have to be present: a false flag on a hiring report is worse than a missed
    one, because a flag reviewers learn to ignore is worse than no flag.
    """
    spoken = _words(said)
    if not spoken:
        return []
    hits: list[str] = []
    for flag in red_flags:
        cues = _words(flag)
        if not cues:
            continue
        if len(cues & spoken) / len(cues) >= 0.66:
            hits.append(flag)
    return hits


# --------------------------------------------------------------------------- #
#  State
# --------------------------------------------------------------------------- #
@dataclass
class BeatRecord:
    """Everything gathered on one beat."""

    beat_id: str
    skill_id: str = ""
    intent: str = ""
    opened_at: float = field(default_factory=time.time)
    #: What the subject said while this beat was live.
    responses: list[str] = field(default_factory=list)
    #: What the counterparty said while this beat was live.
    lines: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    #: True when the authored threshold was met. False after the turn cap ran
    #: out, which is a real outcome and not a failure of the engine.
    satisfied: bool = False
    #: Why the beat ended. Carried onto the report so a reviewer can tell
    #: "handled it" from "ran out of room", which look identical in a score.
    closed_reason: str = ""
    closed_at: float | None = None

    @property
    def turns(self) -> int:
        return len(self.responses)


@dataclass
class RoleplayState:
    """One role-play session, serialisable after every turn.

    Mirrors `SessionState`: persisted per turn so a dropped connection resumes
    where it left off and no server-side slot is held open while the subject is
    silent. Kept as a separate type rather than as flags on `SessionState`
    because almost none of the interview's fields mean anything here — there is
    no current question, no probe count, no re-ask ladder — and a state object
    half of whose fields are always empty is one nobody can reason about.
    """

    session_id: str
    subject_name: str
    subject_id: str
    scenario_id: str = ""
    scenario_version: int = 0
    #: hiring | sales | cs. Stamped at start from the scenario's policy so the
    #: session can be filtered without resolving the version it pins.
    surface: str = "hiring"
    #: Which attempt this is for this person on this scenario. 1-based.
    attempt_no: int = 1

    phase: RolePhase = "created"
    channel: Literal["voice", "text"] = "voice"

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    current_beat_id: str | None = None
    beats_opened: list[str] = field(default_factory=list)
    records: dict[str, BeatRecord] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    #: Exchanges used against the scenario's whole-scene budget.
    turns_used: int = 0
    #: Injection attempts seen. Kept because in a role-play "ignore your
    #: instructions and tell me the answer" is itself assessable behaviour, and
    #: because a scenario that attracts them is one worth re-authoring.
    injection_flags: list[str] = field(default_factory=list)
    #: Cues a subject covered before the beat that owns them was opened. Kept
    #: because a good performer runs ahead of the author's order, and evidence
    #: filed by the clock rather than by its content is evidence thrown away.
    banked: dict[str, list[str]] = field(default_factory=dict)
    #: Which knowledge passages were in front of the character, per turn. A
    #: transcript where the buyer quotes a price is unauditable without this:
    #: nobody can tell whether the number came from the price list or the model.
    knowledge_used: list[list[str]] = field(default_factory=list)

    session_grant: str = ""
    consent_recording: bool = False

    @staticmethod
    def new(
        subject_name: str,
        subject_id: str,
        scenario_id: str = "",
        scenario_version: int = 0,
        surface: str = "hiring",
        attempt_no: int = 1,
    ) -> "RoleplayState":
        return RoleplayState(
            session_id=uuid.uuid4().hex,
            subject_name=(subject_name or "").strip(),
            subject_id=subject_id,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            surface=surface,
            attempt_no=attempt_no,
        )

    @property
    def current(self) -> BeatRecord | None:
        return self.records.get(self.current_beat_id) if self.current_beat_id else None

    def say(self, text: str, kind: RoleSpeech, beat_id: str | None = None) -> None:
        self.transcript.append(
            {"speaker": "tara", "text": text, "kind": kind, "beat_id": beat_id, "at": time.time()}
        )
        self.updated_at = time.time()

    def heard(self, text: str, read: dict[str, Any] | None = None, beat_id: str | None = None) -> None:
        self.transcript.append(
            {"speaker": "subject", "text": text, "beat_id": beat_id, "read": read, "at": time.time()}
        )
        self.updated_at = time.time()

    def recent(self, n: int = 6) -> list[dict[str, str]]:
        """The last few exchanges, for the counterparty's short memory.

        Bounded rather than the whole transcript: a persona given twenty turns
        of history starts summarising the conversation back, which no real
        person does.
        """
        return [
            {"speaker": t.get("speaker", ""), "text": t.get("text", "")}
            for t in self.transcript[-n:]
        ]

    # -- serialisation ------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["records"] = {k: asdict(v) for k, v in self.records.items()}
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "RoleplayState":
        data = dict(d)
        records = {k: BeatRecord(**v) for k, v in (data.pop("records", {}) or {}).items()}
        banked = dict(data.pop("banked", {}) or {})
        known = set(RoleplayState.__dataclass_fields__)
        state = RoleplayState(**{k: v for k, v in data.items() if k in known})
        state.records = records
        state.banked = banked
        return state


@dataclass
class RoleplayReply:
    """One thing Tara says in the scene, plus what the subject's screen needs."""

    text: str
    kind: RoleSpeech
    beat_id: str | None = None
    ends: bool = False
    progress: dict[str, Any] = field(default_factory=dict)
    #: True when the line came from the authored fallback bank rather than the
    #: model. Surfaced so a reviewer watching a replay can tell a scripted
    #: moment from an improvised one without reading the audit log.
    authored: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "beat_id": self.beat_id,
            "ends": self.ends,
            "progress": self.progress,
            "authored": self.authored,
        }


# --------------------------------------------------------------------------- #
#  The engine
# --------------------------------------------------------------------------- #
class RoleplayEngine:
    """Runs any simulation the library can describe.

    The agent and the knowledge base are resolved per call from the scenario
    rather than held on the instance, for the same reason the interview
    orchestrator resolves its definition per turn: one server runs many
    sessions at once, and each belongs to exactly one configuration.
    """

    def __init__(self, knowledge: dict[str, KnowledgeBase] | None = None) -> None:
        self.llm = get_llm()
        self._knowledge = knowledge or {}

    def agent_for(self, defn: ScenarioDefinition) -> AgentSpec:
        return get_agent(defn.agent_type)

    def knowledge_for(self, defn: ScenarioDefinition) -> KnowledgeBase | None:
        return self._knowledge.get(defn.knowledge_base_id) if defn.knowledge_base_id else None

    # ---------------------------------------------------------------- #
    #  Progress
    #
    #  Counts REQUIRED beats only, and counts a beat that ran out of turns as
    #  done. The rail tracks where the scene is, not how well it is going —
    #  a progress bar that stalls when someone is struggling tells them they
    #  are struggling, which is feedback nobody authored.
    # ---------------------------------------------------------------- #
    def _progress(self, state: RoleplayState, defn: ScenarioDefinition) -> dict[str, Any]:
        required = [b for b in defn.beats if not b.optional]
        done = sum(1 for b in required if b.id in state.records and state.records[b.id].closed_at)
        return {
            "beats_total": len(required),
            "beats_done": done,
            "turns_used": state.turns_used,
            "turn_budget": defn.turn_budget,
        }

    # ---------------------------------------------------------------- #
    #  Open the scene
    # ---------------------------------------------------------------- #
    def open(self, state: RoleplayState, defn: ScenarioDefinition) -> RoleplayReply:
        """The briefing, then the counterparty's first line.

        The briefing is authored and spoken verbatim. It is the only part of a
        role-play that is the same for everyone by construction, and it has to
        be: it is the instruction set for the exercise, and a subject who was
        briefed differently sat a different assessment.
        """
        state.phase = "briefing"
        state.surface = defn.policy.surface
        briefing = self._briefing_text(state, defn)
        state.say(briefing, "briefing")

        first = defn.beats[0] if defn.beats else None
        if first is None:
            return self._close(state, defn, reason="no_beats")

        self._open_beat(state, first)
        opening = defn.persona.opening_line.strip()
        state.say(opening, "in_character", first.id)
        state.phase = "in_scene"
        return RoleplayReply(
            text=opening,
            kind="in_character",
            beat_id=first.id,
            progress=self._progress(state, defn),
            authored=True,
        )

    def _briefing_text(self, state: RoleplayState, defn: ScenarioDefinition) -> str:
        """What the subject hears before the scene starts.

        Custom mode is spoken verbatim and nothing is added to it. An
        administrator running a known cohort through a known exercise has
        context the platform does not, and a standard welcome bolted onto their
        script is how a subject ends up being told two different things about
        what they are doing.
        """
        script = defn.script
        if script.intro_mode == "custom":
            parts = [
                script.welcome,
                script.scenario_instructions,
                script.candidate_instructions,
            ]
            custom = " ".join(p.strip() for p in parts if p and p.strip())
            if custom:
                return custom

        name = (state.subject_name or "").strip()
        head = defn.opening.strip() or (
            (f"Hi {name}. " if name else "")
            + "This is a role-play, so I'll stay in character the whole way "
            "through. Here's the situation."
        )
        tail = " ".join(
            p.strip() for p in (defn.briefing, script.candidate_instructions) if p and p.strip()
        )
        return f"{head} {tail}".strip()

    def _open_beat(self, state: RoleplayState, beat: BeatSpec) -> BeatRecord:
        record = BeatRecord(beat_id=beat.id, skill_id=beat.skill_id, intent=beat.intent)
        record.covered = list(state.banked.pop(beat.id, []))
        record.missing = [c for c in beat.looking_for if c not in record.covered]
        state.records[beat.id] = record
        state.current_beat_id = beat.id
        if beat.id not in state.beats_opened:
            state.beats_opened.append(beat.id)
        return record

    # ---------------------------------------------------------------- #
    #  One turn
    # ---------------------------------------------------------------- #
    def on_turn(self, state: RoleplayState, defn: ScenarioDefinition, said: str) -> RoleplayReply:
        """The subject spoke. Read it, decide, then answer in character."""
        if state.phase in ("complete", "abandoned"):
            return RoleplayReply(
                text=defn.closing, kind="closing", ends=True,
                progress=self._progress(state, defn), authored=True,
            )

        beat = defn.beat(state.current_beat_id or "")
        record = state.current
        if beat is None or record is None:
            return self._close(state, defn, reason="no_current_beat")

        # An attempt to break the frame is recorded and then ignored. It is not
        # a reason to end the scene: the character would not understand it, and
        # a subject who discovers that "ignore your instructions" ends the
        # assessment has found a way out of being assessed.
        scan = guardrails.scan_candidate_turn(said)
        if scan.suspicious:
            state.injection_flags.append((scan.reason or "injection")[:120])

        idx = defn.beat_index(beat.id)
        lookahead = defn.beats[idx + 1: idx + 1 + self.LOOKAHEAD] if idx >= 0 else []
        read = self._read(
            beat, said, state.session_id,
            lookahead=lookahead, persona_role=defn.persona.role,
        )
        record.responses.append(said)
        for cue in read.get("covered") or []:
            if cue not in record.covered:
                record.covered.append(cue)
        record.missing = [c for c in beat.looking_for if c not in record.covered]
        # Semantic hits from the classifier, plus anything that matched
        # verbatim. Union rather than either alone: the classifier catches the
        # paraphrase, the matcher catches the case where a provider is absent.
        for flag in (read.get("flagged") or []) + red_flags_in(said, beat.red_flags):
            if flag not in record.red_flags:
                record.red_flags.append(flag)
        # Evidence for a beat that has not opened yet is banked against that
        # beat rather than discarded. It opens already holding what it was
        # given, which is what stops a subject being asked again for something
        # they have already done.
        early = read.get("ahead") or []
        for ahead_beat in lookahead:
            matched = [c for c in early if c in ahead_beat.looking_for]
            if not matched:
                continue
            banked = state.banked.setdefault(ahead_beat.id, [])
            for cue in matched:
                if cue not in banked:
                    banked.append(cue)

        state.heard(said, read=read, beat_id=beat.id)
        state.turns_used += 1

        satisfied = len(record.covered) >= beat.threshold
        exhausted = record.turns >= beat.max_turns
        out_of_budget = state.turns_used >= defn.turn_budget

        # The subject has moved on, so the scene does too. If what they just
        # said already satisfies the NEXT beat, holding them here would measure
        # the author's running order rather than the person — and in a
        # discovery conversation, driving it is precisely what good looks like.
        nxt_beat = defn.next_beat(beat.id)
        overtaken = bool(
            nxt_beat
            and not satisfied
            and len(state.banked.get(nxt_beat.id, [])) >= nxt_beat.threshold
        )

        if satisfied or exhausted or overtaken:
            record.satisfied = satisfied
            record.closed_reason = (
                "handled" if satisfied else "overtaken" if overtaken else "turn_cap"
            )
            record.closed_at = time.time()
            nxt = nxt_beat
            if nxt is None or out_of_budget:
                return self._close(
                    state, defn, reason="budget" if out_of_budget and nxt else "scene_end"
                )
            self._open_beat(state, nxt)
            return self._speak(state, defn, nxt, said)

        if out_of_budget:
            record.closed_reason = "budget"
            record.closed_at = time.time()
            return self._close(state, defn, reason="budget")

        # Still inside the beat — the character presses on the same thing.
        return self._speak(state, defn, beat, said)

    # ---------------------------------------------------------------- #
    #: How many beats ahead a turn may be credited against. Bounded rather than
    #: unlimited: every extra cue in the classifier call is one more thing it can
    #: match loosely, and a turn credited against the closing beat on turn one
    #: would be evidence of nothing.
    LOOKAHEAD = 2

    @staticmethod
    def _read_frame(beat: BeatSpec, persona_role: str = "") -> str:
        """How the turn is described to the classifier.

        Passing the beat's intent alone as "the question" was a real bug, and a
        quiet one. The classifier judges an answer IN THE CONTEXT of a question,
        so framing the turn as beat two and then asking it to match beat three's
        signals made it reject them all — correctly, on its own terms: they had
        nothing to do with the question it was shown. The measured effect was
        total, not partial. Every lookahead cue came back unmatched, and a
        subject who ran ahead of the author's order scored zero.

        The frame names the moment for context and then says plainly that the
        judgement is about what the person said, not about which moment it
        belongs to. That is what lets one call carry the whole cue set.
        """
        who = f" The other person is playing: {persona_role}." if persona_role else ""
        return (
            f"A live workplace role-play.{who} At this moment: {beat.intent} "
            "Judge ONLY what the person being assessed just said, against each "
            "signal below, regardless of which moment of the conversation that "
            "signal belongs to."
        )

    def _read(
        self,
        beat: BeatSpec,
        said: str,
        session_id: str,
        lookahead: list[BeatSpec] | None = None,
        persona_role: str = "",
    ) -> dict[str, Any]:
        """Classify the turn against the beat's authored key AND its red flags.

        One call, both lists, then partitioned back apart. The identical call
        the Q&A path makes, with the beat's intent standing in for the question
        — which is not a shortcut. It means a beat and a question are read by
        the same calibrated classifier, so evidence gathered in a role-play and
        evidence gathered in an interview are comparable, which is the whole
        reason one person can carry one skill profile across both.

        Red flags go through the same call rather than a second one for two
        reasons. Latency: the subject is mid-conversation and a second round
        trip is a second silence. And fidelity: a flag is a paraphrase problem
        exactly like a signal is, and the classifier is the unit already
        calibrated to read paraphrase.

        The partition matters. A flag that stayed in `covered` would count
        toward the beat's threshold, so doing the wrong thing convincingly
        would advance the scene — which is the precise inversion of what a red
        flag means.

        `lookahead` carries the NEXT beats' cues into the same call, and this
        is the fix for the bug that made a strong performer score zero. A good
        subject does not follow the author's running order: they ask the
        discovery question in the opening beat and close in the middle. Reading
        only the open beat threw all of that away — the evidence was filed by
        the clock instead of by its content, and the scene ran out of beats
        while the subject was doing everything right. Cues that belong to a
        later beat are returned separately and credited to the beat that owns
        them, so nothing a subject says is lost because they said it early.
        """
        cues = list(beat.looking_for)
        flags = list(beat.red_flags)
        ahead = [c for b in (lookahead or []) for c in b.looking_for if c not in cues]
        try:
            read = self.llm.read_answer(
                self._read_frame(beat, persona_role),
                said,
                cues + flags + ahead,
                session_id=session_id,
            ) or {}
        except Exception:
            # A classifier outage must slow a scene down, never end it. An
            # unread turn covers nothing, which costs the subject nothing: the
            # beat simply runs to its turn cap.
            return {
                "covered": [], "missing": list(cues), "flagged": [],
                "ahead": [], "depth": "unknown",
            }

        raw = read.get("covered") or []
        read["covered"] = [c for c in raw if c in cues]
        read["flagged"] = [c for c in raw if c in flags]
        # Lookahead matches get their own slot rather than staying in `covered`.
        # They must not count toward THIS beat's threshold — and the caller
        # cannot recover them afterwards, because `covered` has already been
        # narrowed to this beat's cues by the line above.
        read["ahead"] = [c for c in raw if c in ahead]
        read["missing"] = [c for c in cues if c not in read["covered"]]
        return read

    def _speak(
        self, state: RoleplayState, defn: ScenarioDefinition, beat: BeatSpec, said: str
    ) -> RoleplayReply:
        """The counterparty's next line: generated if allowed, authored if not."""
        line, authored = "", True
        if defn.allow_generated_dialogue:
            line = self._generate(state, defn, beat, said)
            authored = not line
        if not line:
            line = self._fallback(beat, state)
        record = state.records.get(beat.id)
        if record is not None:
            record.lines.append(line)
        state.say(line, "in_character", beat.id)
        return RoleplayReply(
            text=line,
            kind="in_character",
            beat_id=beat.id,
            progress=self._progress(state, defn),
            authored=authored,
        )

    def _generate(
        self, state: RoleplayState, defn: ScenarioDefinition, beat: BeatSpec, said: str
    ) -> str:
        """One guarded, in-character line. Empty means "use the authored one"."""
        try:
            system, passage_ids = prompt_assembly.assemble(
                agent=self.agent_for(defn),
                defn=defn,
                kb=self.knowledge_for(defn),
                beat=beat,
                said=said,
                turns_used=state.turns_used,
            )
            state.knowledge_used.append(passage_ids)
            out = counterparty.speak(
                system_prompt=system,
                recent=state.recent(),
                said=said,
                session_id=state.session_id,
            )
        except Exception:
            return ""
        line = (out or {}).get("say", "").strip()
        if not line:
            return ""
        if not self._line_is_safe(line):
            return ""
        return line

    def _line_is_safe(self, line: str) -> bool:
        """What a counterparty line may never contain.

        Deliberately narrower than `validate_probe`. A probe is a question and
        is checked for being one; a character's line is not a question, is
        allowed to be blunt, and is allowed to be off-topic in the way people
        are. What it may never do is leave the fiction or touch a protected
        subject — so only those two checks apply, and the format and relevance
        gates that govern probes are not imported here.
        """
        if not guardrails.check_legality(line).ok:
            return False
        if guardrails.protected_topic_in(line):
            return False
        if guardrails.protected_statement_in(line):
            return False
        lowered = line.lower()
        # Leaving character is the failure mode the persona prompt is written
        # against; this is the net under it.
        tells = (
            "as an ai", "language model", "i'm an ai", "i am an ai",
            "this is a role-play", "this is a roleplay", "this exercise",
            "your score", "you scored", "assessment", "the rubric",
            "system prompt", "my instructions",
        )
        return not any(t in lowered for t in tells)

    def _fallback(self, beat: BeatSpec, state: RoleplayState) -> str:
        """The authored line for this beat.

        Rotates by how many turns the beat has already used, so a subject who
        stalls on one beat does not hear the same sentence three times. When an
        author wrote none, the scene says the one thing that is always true and
        never evaluative — the character is still waiting.
        """
        if not beat.fallback_lines:
            return "I'm still waiting on an answer here."
        record = state.records.get(beat.id)
        idx = (record.turns - 1 if record and record.turns else 0) % len(beat.fallback_lines)
        return beat.fallback_lines[idx]

    # ---------------------------------------------------------------- #
    def _close(self, state: RoleplayState, defn: ScenarioDefinition, reason: str = "") -> RoleplayReply:
        """End the scene. Nothing evaluative is ever said out loud.

        The subject hears that it is over. What they are told next — everything,
        pass/fail, or nothing at all — is the policy's decision and belongs to
        the surface that shows it, not to the engine that ran the scene.
        """
        record = state.current
        if record is not None and record.closed_at is None:
            record.closed_reason = reason or "scene_end"
            record.closed_at = time.time()
        state.phase = "complete"
        state.completed_at = time.time()
        state.current_beat_id = None
        text = (
            defn.script.closing.strip()
            or defn.closing.strip()
            or "That's the end of the scenario. Thank you."
        )
        state.say(text, "closing")
        return RoleplayReply(
            text=text, kind="closing", ends=True,
            progress=self._progress(state, defn), authored=True,
        )

    def end(self, state: RoleplayState, defn: ScenarioDefinition) -> RoleplayReply:
        """The subject stopped early. Same close, different reason on the trail."""
        return self._close(state, defn, reason="ended_early")


# --------------------------------------------------------------------------- #
#  Evidence
#
#  What the scoring engine consumes. Deliberately NOT a score: this reports what
#  was covered, what was missed, and what was flagged, per skill, and leaves the
#  judgement to the unit whose job that is.
# --------------------------------------------------------------------------- #
def evidence(state: RoleplayState, defn: ScenarioDefinition) -> dict[str, Any]:
    """Per-skill evidence from a finished (or abandoned) scene."""
    per_skill: dict[str, dict[str, Any]] = {}
    for beat in defn.beats:
        record = state.records.get(beat.id)
        skill = beat.skill_id or "_unassigned"
        bucket = per_skill.setdefault(
            skill,
            {
                "skill_id": skill,
                "beats_total": 0,
                "beats_reached": 0,
                "beats_handled": 0,
                "covered": [],
                "missing": [],
                "red_flags": [],
                # Beats the subject never got to, because the budget ran out or
                # they ended early. Named separately because an unreached beat
                # is missing evidence, not evidence of a miss — the same rule
                # the Q&A path applies to unanswered questions.
                "not_reached": [],
            },
        )
        bucket["beats_total"] += 1
        if record is None:
            bucket["not_reached"].append(beat.id)
            continue
        bucket["beats_reached"] += 1
        if record.satisfied:
            bucket["beats_handled"] += 1
        for c in record.covered:
            if c not in bucket["covered"]:
                bucket["covered"].append(c)
        for m in record.missing:
            if m not in bucket["missing"]:
                bucket["missing"].append(m)
        for f in record.red_flags:
            if f not in bucket["red_flags"]:
                bucket["red_flags"].append(f)

    return {
        "session_id": state.session_id,
        "scenario_id": defn.scenario_id,
        "scenario_version": defn.version,
        "surface": defn.policy.surface,
        "attempt_no": state.attempt_no,
        "complete": state.phase == "complete",
        "turns_used": state.turns_used,
        "injection_flags": list(state.injection_flags),
        "per_skill": list(per_skill.values()),
    }
