"""The domain model — the nouns the whole product is written in.

These are plain dataclasses, not ORM rows, on purpose. The durable store today
is a set of JSON files (see `services/data/`); the target store is Postgres
(`packages/types/schema.sql` holds that DDL). Keeping the entities independent
of either means the repositories can change underneath without the rest of the
product noticing.

Two rules run through the whole model and are worth stating once:

  * **A published interview never changes.** `Interview` is mutable and is what
    a recruiter edits; `InterviewVersion` is immutable and is what a candidate
    actually sits. Editing a published interview produces a new version rather
    than mutating the old one, so a session recorded in March can still be
    replayed and re-scored in June against the criteria it was judged on.

  * **Evidence and scores are separate rows.** `Evidence` is what the candidate
    said and which expected signals it carried; `SkillScore` is a judgement over
    that evidence. A report may only assemble scores that already exist — it
    never invents its own.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# --------------------------------------------------------------------------- #
#  Identifiers
# --------------------------------------------------------------------------- #
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


Priority = Literal["high", "medium", "low"]
Difficulty = Literal["easy", "medium", "hard"]
InterviewType = Literal["short", "medium", "long"]
InterviewStatus = Literal["draft", "published", "archived"]
VersionStatus = Literal["draft", "published"]
InvitationStatus = Literal["pending", "in_progress", "complete", "expired", "revoked"]
SessionPhase = Literal["created", "greeting", "asking", "probing", "closing", "complete", "abandoned"]


# --------------------------------------------------------------------------- #
#  Tenancy and people
# --------------------------------------------------------------------------- #
@dataclass
class Organization:
    id: str
    name: str
    created_at: float = field(default_factory=time.time)


@dataclass
class User:
    """A recruiter or admin. Candidates are NOT users — see `Candidate`."""

    id: str
    org_id: str
    email: str
    name: str = ""
    role: Literal["admin", "recruiter", "reviewer"] = "recruiter"
    created_at: float = field(default_factory=time.time)


@dataclass
class Job:
    """The role being hired for. One job can carry several interviews."""

    id: str
    org_id: str
    title: str
    description: str = ""          # the JD text the designer reads
    language: str = "en"
    experience_from: int = 0
    experience_to: int = 0
    created_by: str = ""
    created_at: float = field(default_factory=time.time)


# --------------------------------------------------------------------------- #
#  Assessment definition
# --------------------------------------------------------------------------- #
@dataclass
class Skill:
    """A competency the interview assesses.

    `priority` is a band, never a percentage. Recruiters think in "this is
    essential to the job", not in "this is 17% of the job", and a surfaced
    weighting invites an argument nobody can settle. The distribution selection
    needs is derived from the band, not typed by a person.
    """

    id: str
    name: str
    description: str = ""
    priority: Priority = "medium"
    proficiency_target: int = 2        # 0-4
    assessment_scope: str = ""         # what "assessed" means for this skill
    evaluated: bool = False            # in the interviewed set
    question_bank: str = ""            # which bank answers it; "" = no coverage yet


@dataclass
class Task:
    """A concrete thing the person does in the role.

    Tasks are the hinge of the whole design. A flat skill list is easy to wave
    through and impossible to check; "you will diagnose a billing failure from a
    vague report, and that needs troubleshooting and communication clarity" is
    something a hiring manager can confirm or correct on sight.
    """

    id: str
    description: str
    priority: Priority = "medium"
    outcome: str = ""


@dataclass
class TaskSkill:
    """Task → assesses → Skill. The many-to-many the designer must produce."""

    task_id: str
    skill_id: str


@dataclass
class Question:
    """One item, in the shape the runtime consumes.

    The runtime does not care whether this came from the authored JSON pool, a
    curated question bank, or the AI Question Generator — only that it satisfies
    this contract. That is what makes the generator swappable.
    """

    id: str
    question_text: str
    skill_id: str = ""
    task_id: str = ""
    competency: str = ""               # the bank / competency it belongs to
    difficulty: Difficulty = "medium"
    expected_signal: str = ""          # one line: what a strong answer shows
    looking_for: list[str] = field(default_factory=list)   # the cues themselves
    evaluation_criteria: list[str] = field(default_factory=list)
    probe_eligible: bool = True
    max_probes: int = 2
    time_budget_sec: int = 90
    probe_bank: list[str] = field(default_factory=list)    # authored fallbacks
    clarify: str = ""                  # authored restatement, candidate-facing
    modality: list[str] = field(default_factory=lambda: ["voice", "text"])
    source: Literal["authored", "generated", "bank"] = "authored"


@dataclass
class Interview:
    """The mutable container a recruiter works on.

    It holds no questions. Questions live on versions, because a question a
    candidate was asked must never change after they were asked it.
    """

    id: str
    org_id: str
    job_id: str
    title: str                          # internal; candidates never see it
    role: str                           # which question bank family it draws on
    status: InterviewStatus = "draft"
    current_version: int = 0            # highest published version, 0 = none
    draft_version: int = 0              # the version being edited
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class InterviewVersion:
    """An immutable snapshot of everything a candidate is judged against.

    `definition` is an `InterviewDefinition` (see `definition.py`) serialised —
    skills, tasks, questions, evaluation criteria, and the runtime limits. Once
    `status` is "published", nothing in it is rewritten. A recruiter edit opens
    the next version instead.
    """

    interview_id: str
    version: int
    definition: dict[str, Any]
    status: VersionStatus = "draft"
    checksum: str = ""                  # content hash — detects tampering
    published_at: float | None = None
    published_by: str = ""
    created_at: float = field(default_factory=time.time)
    notes: str = ""                     # what changed, for the version history

    @property
    def ref(self) -> str:
        return f"{self.interview_id}@v{self.version}"


# --------------------------------------------------------------------------- #
#  Candidates and sessions
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    id: str
    org_id: str
    name: str
    email: str = ""
    external_ref: str = ""              # the ATS's id for this person
    created_at: float = field(default_factory=time.time)


@dataclass
class Invitation:
    """The candidate's way in — and the pin that fixes which version they sit.

    `interview_version` is captured when the invitation is minted, not when the
    candidate opens it. Otherwise a recruiter publishing v2 on Tuesday changes
    the interview under a candidate who was invited on Monday.
    """

    token: str
    interview_id: str
    interview_version: int
    candidate_id: str
    candidate_name: str
    role: str
    status: InvitationStatus = "pending"
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and time.time() > self.expires_at)


@dataclass
class InterviewSession:
    """One candidate's run. Durable metadata only — the live turn state lives
    in the orchestrator's own session store (`services/data/sessions.py`)."""

    id: str
    interview_id: str
    interview_version: int
    candidate_id: str
    invitation_token: str = ""
    phase: SessionPhase = "created"
    channel: Literal["voice", "text"] = "voice"
    consent_recording: bool = False
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


@dataclass
class InterviewTurn:
    """One thing that was said, by either side."""

    id: str
    session_id: str
    index: int
    speaker: Literal["tara", "candidate"]
    text: str
    kind: str = ""                      # question | probe | ack | clarify | ...
    question_id: str = ""
    at: float = field(default_factory=time.time)


# --------------------------------------------------------------------------- #
#  Evaluation
# --------------------------------------------------------------------------- #
@dataclass
class Evidence:
    """What one question actually produced. Not a score.

    `covered` / `missing` are a reading of which expected signals the answer
    carried. They are labelled as a model's reading wherever they are shown,
    because the judgement belongs to the reviewer.
    """

    id: str
    session_id: str
    question_id: str
    skill_id: str = ""
    answer_text: str = ""
    probes_asked: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    quote: str = ""
    answered: bool = True               # False = never answered, NOT zero
    at: float = field(default_factory=time.time)


@dataclass
class SkillScore:
    """A judgement over evidence, on the definition's scoring scale.

    `confidence` is first-class: one thin answer and four probing exchanges
    should not read as equally certain, and a low-confidence score is flagged
    rather than quietly averaged in.
    """

    id: str
    session_id: str
    skill_id: str
    skill_name: str
    level: float                        # 0..scale
    scale: int = 5
    confidence: float = 0.0             # 0..1
    evidence_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    questions_answered: int = 0
    questions_unanswered: int = 0       # excluded from the denominator, not zeroed


@dataclass
class InterviewResult:
    """The rolled-up outcome of one session. Still not a hire decision."""

    id: str
    session_id: str
    interview_id: str
    interview_version: int
    composite: float = 0.0
    scale: int = 5
    band: str = ""                      # e.g. "Strong indication"
    skill_scores: list[str] = field(default_factory=list)   # SkillScore ids
    task_coverage: dict[str, float] = field(default_factory=dict)
    generated_at: float = field(default_factory=time.time)


@dataclass
class Report:
    """The recruiter-facing narrative, assembled from scores that already exist.

    The report generator may summarise, order, and explain. It may not produce a
    number the scoring engine did not produce — a report that scores is a second,
    unaudited scoring engine.
    """

    id: str
    session_id: str
    result_id: str
    summary: str = ""
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    recommended_followups: list[str] = field(default_factory=list)
    model: str = ""
    generated_at: float = field(default_factory=time.time)


@dataclass
class AuditEvent:
    """One append-only fact. Never updated, never deleted."""

    id: str
    at: float
    event: str
    actor: str = ""                     # user id, "candidate", or "system"
    org_id: str = ""
    subject_type: str = ""              # interview | session | invitation | ai
    subject_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def to_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
