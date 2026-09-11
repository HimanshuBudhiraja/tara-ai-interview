"""Session state — the whole interview in one serialisable object.

Persisted after every turn, so a dropped connection resumes exactly where it
left off and no server-side slot is held open while a candidate is silent.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Phase = Literal["created", "greeting", "asking", "probing", "closing", "complete", "abandoned"]
SpeechKind = Literal["greeting", "question", "probe", "ack", "repeat", "clarify", "closing", "hold"]


@dataclass
class Utterance:
    """One thing that was said, by either side."""

    speaker: Literal["tara", "candidate"]
    text: str
    at: float = field(default_factory=time.time)
    kind: SpeechKind | None = None
    item_id: str | None = None
    # Populated on candidate turns from the read_answer call — evidence, not a score.
    read: dict[str, Any] | None = None


@dataclass
class ItemRecord:
    """Everything gathered on one pool item."""

    item_id: str
    competency: str
    prompt: str
    asked_at: float = field(default_factory=time.time)
    answers: list[str] = field(default_factory=list)
    probes_asked: list[str] = field(default_factory=list)
    # How many times Tara has re-said this question (repeat requests, silence
    # re-asks). Bounded, so a candidate with broken audio is never trapped.
    reasks: int = 0
    # Clarification requests on this item. Also bounded — a candidate stuck in
    # "what do you mean?" needs a different question, not the same one again.
    clarifies: int = 0
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    closed_at: float | None = None

    @property
    def probe_count(self) -> int:
        return len(self.probes_asked)


@dataclass
class SessionState:
    session_id: str
    candidate_name: str
    candidate_id: str
    role: str
    invite_token: str = ""
    # Which configured interview this session is running. Empty for sessions
    # created before interviews were configurable.
    interview_id: str = ""
    # Which PUBLISHED VERSION of it. Fixed when the session starts and never
    # moved, so a recruiter publishing v2 mid-morning cannot change the
    # questions or the criteria under a candidate who started on v1.
    # 0 means a session from before versioning; those resolve from the live
    # configuration, which is exactly the behaviour versioning replaces.
    interview_version: int = 0
    phase: Phase = "created"
    channel: Literal["voice", "text"] = "voice"
    # Which pilot batch this interview belongs to, stamped once when the session
    # is created and never moved. Empty for anything outside a pilot run, which
    # is what keeps ordinary use out of the pilot's numbers.
    pilot_run_id: str = ""

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    asked_item_ids: list[str] = field(default_factory=list)
    current_item_id: str | None = None
    records: dict[str, ItemRecord] = field(default_factory=dict)
    transcript: list[Utterance] = field(default_factory=list)

    #: The candidate's authority over THIS session, minted when it starts and
    #: handed back as an HttpOnly cookie. Bound to one session, so knowing a
    #: session id is not enough to read a transcript or take a turn — see
    #: `services.security.principal.candidate_scope`.
    session_grant: str = ""
    consent_recording: bool = False
    accommodations: dict[str, Any] = field(default_factory=dict)
    # Turn ids the client has already had answered, with the reply each one
    # produced. A retried POST or an answer re-sent after a socket flap is the
    # SAME turn, and replaying it would put a second copy of one answer into the
    # transcript — or, worse, record it against the question that answer caused
    # Tara to move on to. Bounded: see `candidate._remember_turn`.
    applied_turns: dict[str, Any] = field(default_factory=dict)
    # Consecutive silences on the current item, so nudges escalate rather than loop.
    silence_streak: int = 0

    # ---------------------------------------------------------------- #
    @staticmethod
    def new(
        candidate_name: str,
        candidate_id: str,
        role: str,
        invite_token: str = "",
        interview_id: str = "",
        interview_version: int = 0,
    ) -> "SessionState":
        return SessionState(
            session_id=uuid.uuid4().hex,
            candidate_name=candidate_name.strip(),
            candidate_id=candidate_id,
            role=role,
            invite_token=invite_token,
            interview_id=interview_id,
            interview_version=interview_version,
        )

    @property
    def current(self) -> ItemRecord | None:
        return self.records.get(self.current_item_id) if self.current_item_id else None

    def say(self, text: str, kind: SpeechKind, item_id: str | None = None) -> None:
        self.transcript.append(Utterance("tara", text, kind=kind, item_id=item_id))
        self.updated_at = time.time()

    def heard(self, text: str, read: dict[str, Any] | None = None, item_id: str | None = None) -> None:
        self.transcript.append(Utterance("candidate", text, item_id=item_id, read=read))
        self.updated_at = time.time()

    # -- serialisation ------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["records"] = {k: asdict(v) for k, v in self.records.items()}
        d["transcript"] = [asdict(u) for u in self.transcript]
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SessionState":
        records = {k: ItemRecord(**v) for k, v in (d.pop("records", {}) or {}).items()}
        transcript = [Utterance(**u) for u in (d.pop("transcript", []) or [])]
        known = set(SessionState.__dataclass_fields__)
        state = SessionState(**{k: v for k, v in d.items() if k in known})
        state.records = records
        state.transcript = transcript
        return state
