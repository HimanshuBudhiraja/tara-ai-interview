"""A configured interview — what the recruiter actually sets up.

The recruiter does not start from a list of competencies. They start from a job
description, and the flow is:

    job details + JD  →  outcomes → tasks → skills  →  review and adjust
                      →  which skills get interviewed, at what level
                      →  the questions those skills draw from
                      →  publish and invite

So an `InterviewConfig` holds the JD and everything inferred from it, plus the
recruiter's edits on top. The inferred half is a proposal; the recruiter's half
is the decision. Keeping both means the review screen can always show what TARA
suggested and what a person changed.

Deliberately NOT here: rubrics, score thresholds, and hire bands. Those belong
to the scoring engine, which this build doesn't include — configuring them here
would imply a verdict this system never produces.
"""
from __future__ import annotations

import functools
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.types import clamp_speech_rate, InterviewDefinition, derive_bank_min_items, derive_bank_weights
from packages.types.definition import (
    BankSpec,
    CriterionSpec,
    EvaluationSpec,
    QuestionSpec,
    RuntimeLimits,
    SkillSpec,
    TaskSpec,
)
from services import config
from services.ai.workloads.interview_designer import PRIORITY_RANK, Skill, Task
from services.data import jsonfile, skill_master


@dataclass
class CompanyInfo:
    """Context TARA speaks with — and the only thing it may answer about."""

    name: str = ""
    about: str = ""
    role_context: str = ""


@dataclass
class InterviewConfig:
    id: str
    title: str  # internal; candidates never see it
    role: str  # which authored pool this draws questions from
    status: str = "draft"  # draft | published

    # --- who owns it ---
    #
    # The one authoritative tenant anchor in the product. Versions, invitations,
    # candidate sessions and evaluations all resolve their owner through this
    # field rather than carrying a copy of it, because two copies of a tenant id
    # eventually disagree and the disagreement is a data breach.
    organization_id: str = ""
    created_by: str = ""

    # --- what the recruiter typed ---
    role_title: str = ""  # the job profile name the candidate sees
    company: CompanyInfo = field(default_factory=CompanyInfo)
    jd_text: str = ""
    experience_from: int = 0
    experience_to: int = 0
    # The Job this interview was designed from. Regeneration replays the job's
    # original input, not whatever the interview has been edited into since.
    job_id: str = ""
    additional_information: str = ""
    # Why the designer chose this shape. Shown on the review screen so the
    # recommendation is arguable rather than merely announced.
    design_rationale: str = ""
    # Set when the designer failed. The job details are kept — losing a pasted
    # job description because a provider was down is a bad trade — so the draft
    # reopens with the recruiter's input intact and a retry.
    design_failed: bool = False

    # --- the question pool, once it has been generated ---
    #
    # Stored as serialised `QuestionSpec`s rather than as a separate table,
    # because they are part of the assessment definition and are frozen with it
    # at publish. An interview drawing on an authored bank (the CSR screen) has
    # none of these and builds its questions from the pool instead.
    questions: list[dict[str, Any]] = field(default_factory=list)
    #: The blueprint that produced them, kept so a slot can be retried and a
    #: single question regenerated against the coverage it was created for.
    blueprint: dict[str, Any] = field(default_factory=dict)
    questions_generated_at: float | None = None

    # --- what TARA inferred from the JD, then the recruiter adjusted ---
    outcomes: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    extracted_at: float | None = None

    # --- how the conversation runs ---
    # --- the shape of the conversation, inferred then adjustable ---
    #: Which hiring stage this interview is for. Sets the starting interview
    #: type and difficulty; the recruiter can still change either afterwards.
    funnel_stage: str = "technical"
    interview_type: str = "medium"  # short | medium | long
    difficulty: str = "medium"
    recommended_duration_min: int = 20

    # --- versioning ---
    # The highest version published from this configuration. Candidates are
    # pinned to a version, never to this record, so editing after publish is
    # safe: it produces the NEXT version rather than changing the last one.
    published_version: int = 0

    skills_evaluated: int = 6  # how many skills are actually interviewed
    question_budget: int = 8  # questions asked per candidate
    #: How fast Tara speaks in this interview. See RuntimeLimits.speech_rate.
    speech_rate: float = 0.9
    max_probes_per_item: int = 2  # follow-up ceiling per question
    allow_generated_probes: bool = True
    language: str = "en"

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------ #
    @property
    def extracted(self) -> bool:
        return bool(self.skills)

    def evaluated_skills(self) -> list[Skill]:
        return [s for s in self.skills if s.evaluated]

    def skill(self, competency_id: str) -> Skill | None:
        return next((s for s in self.skills if s.competency_id == competency_id), None)

    def covered_skills(self) -> list[Skill]:
        """Evaluated skills the authored pool can actually ask about."""
        return [s for s in self.evaluated_skills() if s.pool_competency]

    def uncovered_skills(self) -> list[Skill]:
        """Evaluated skills with no authored questions — the QGE-shaped gap."""
        return [s for s in self.evaluated_skills() if not s.pool_competency]

    def _bank_pairs(self) -> list[tuple[str, str]]:
        return [(s.pool_competency, s.priority) for s in self.covered_skills()]

    def pool_weights(self) -> dict[str, float]:
        """Priority bands, turned into the distribution selection needs.

        A recruiter sets bands (high / medium / low) because that is how people
        think about a role; selection needs a distribution. Converting here —
        rather than making the recruiter type percentages — keeps the surfaced
        control honest and the arithmetic out of their way.

        The derivation itself lives in `packages.types.definition` and is shared
        with the published contract, so the console's preview and a live
        interview can never drift apart.
        """
        return derive_bank_weights(self._bank_pairs())

    def pool_min_items(self) -> dict[str, int]:
        return derive_bank_min_items(self._bank_pairs())

    # -- serialisation ------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["company"] = asdict(self.company)
        d["tasks"] = [asdict(t) for t in self.tasks]
        # `domain_label` is resolved here rather than left to each caller.
        # Two payload builders serialise skills — this one and the design
        # router's — and only one of them carried the label, so a screen fed
        # by the other rendered a bare `dom_…` id. Resolving it at the single
        # point both go through is what stops that recurring.
        d["skills"] = [
            {**asdict(s), "domain_label": skill_master.label_of(s.domain)}
            for s in self.skills
        ]
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "InterviewConfig":
        company = CompanyInfo(**(d.pop("company", {}) or {}))
        known_task = set(Task.__dataclass_fields__)
        known_skill = set(Skill.__dataclass_fields__)
        tasks = [
            Task(**{k: v for k, v in t.items() if k in known_task})
            for t in (d.pop("tasks", []) or [])
        ]
        skills = [
            Skill(**{k: v for k, v in s.items() if k in known_skill})
            for s in (d.pop("skills", []) or [])
        ]
        # Drafts written before tasks had ids are migrated in place, keeping
        # everything that already points at them working. Ids are minted on
        # first read and saved on the next write.
        for task in tasks:
            task.ensure_id()
        # Tolerate records written before a field existed.
        known = {f for f in InterviewConfig.__dataclass_fields__}
        cfg = InterviewConfig(**{k: v for k, v in d.items() if k in known})
        cfg.company = company
        cfg.tasks = tasks
        cfg.skills = skills
        return cfg


# --------------------------------------------------------------------------- #
#  Config → the canonical contract
#
#  This is the seam described in §7. Upstream of it sits whatever produced the
#  interview: a JD analysis, a recruiter's edits, and (today) an authored
#  question pool. Downstream of it sits the runtime, which sees only an
#  `InterviewDefinition` and cannot tell the difference.
#
#  When the Question Generator lands it produces `QuestionSpec`s here instead of
#  reading them off the pool. Nothing in the orchestrator changes.
# --------------------------------------------------------------------------- #
_INTERVIEW_TYPE_BY_BUDGET = ((5, "short"), (10, "medium"))


def infer_interview_type(question_budget: int) -> str:
    # "deep", not "long": the definition's vocabulary is (short, medium, deep)
    # and `require_valid` refuses anything else, so returning "long" here made a
    # large budget with no explicit type into an interview that could not be
    # published. The older "long" spelling survives in `packages/types/entities`
    # and the pre-V2 designer schema, neither of which reaches this path.
    for ceiling, name in _INTERVIEW_TYPE_BY_BUDGET:
        if question_budget <= ceiling:
            return name
    return "deep"


def build_definition(cfg: InterviewConfig, pool: Any = None) -> InterviewDefinition:
    """Freeze a recruiter's configuration plus its questions into one contract.

    Every question the interview is *permitted* to ask goes in, not the running
    order: selection stays deterministic and stays in the orchestrator, so the
    same definition still adapts to how a candidate actually answers.

    `pool` is optional. An interview designed from a job description has no
    authored question bank behind it yet, so it produces a definition with
    `questions = []` — a complete assessment structure that cannot be run until
    the Question Generator fills it. That is the intended state at the end of
    the design phase, and `validate()` refuses to publish it, which is correct.
    """
    evaluated = cfg.evaluated_skills()
    assessed = {s.competency_id for s in evaluated}

    # The definition is the contract for what this interview MEASURES, so it
    # carries the assessed skills only — and therefore its tasks have to be
    # projected onto that set. A task keeps the skills it evidences that are
    # actually being assessed, and a task that evidences none of them is left
    # out: a published `skill_ids` pointing at a skill the definition does not
    # carry is a reference the runtime cannot resolve, and a task assessing
    # nothing is weight the orchestrator would ground questions in for no
    # measurement. Nothing is lost — the draft keeps every inferred skill and
    # every task, and this is only the published projection of them.
    kept: list[tuple[Any, list[str]]] = []
    for task in cfg.tasks:
        mapped = [sid for sid in task.required_skills if sid in assessed]
        if mapped:
            kept.append((task, mapped))

    skills = [
        SkillSpec(
            id=s.competency_id,
            name=s.name,
            priority=s.priority,
            proficiency_target=s.proficiency_target,
            description=s.description,
            assessment_scope=s.assessment_scope,
            # A generated pool groups by skill, so the skill is its own bank.
            # That is what makes the runtime's priority → coverage machinery
            # work on generated questions with no change to the runtime.
            question_bank=s.pool_competency or (s.competency_id if cfg.questions else ""),
            domain=s.domain,
            task_ids=[
                f"task_{n}" for n, (_, mapped) in enumerate(kept)
                if s.competency_id in mapped
            ],
        )
        for s in evaluated
    ]
    tasks = [
        TaskSpec(
            id=f"task_{n}",
            name=t.name,
            description=t.description,
            priority=t.priority,
            outcome=t.outcome,
            skill_ids=mapped,
        )
        for n, (t, mapped) in enumerate(kept)
    ]

    banks = (
        [BankSpec(id=c.id, label=c.label, weight=c.weight, min_items=c.min_items)
         for c in pool.competencies]
        if pool is not None else []
    )
    questions = [
        QuestionSpec(
            id=i.id,
            question_text=i.prompt,
            competency=i.competency,
            skill_id=next(
                (s.id for s in skills if s.question_bank == i.competency), ""
            ),
            difficulty=i.difficulty,
            expected_signal="; ".join(i.looking_for[:2]),
            looking_for=list(i.looking_for),
            # The authored pool's cues double as its criteria. Emitted in the
            # same shape as a generated question's so there is exactly one
            # rubric type in the product, not two that drift.
            evaluation_criteria=[
                CriterionSpec(id=f"crit_{i.id}_{n}", label=cue, description=cue)
                for n, cue in enumerate(i.looking_for)
            ],
            probe_eligible=i.probe_eligible,
            max_probes=cfg.max_probes_per_item,
            time_budget_sec=i.time_estimate_sec,
            probe_bank=list(i.probe_bank),
            clarify=i.clarify,
            modality=list(i.modality),
            type=i.type,
            source="authored",
        )
        # Pool order is preserved deliberately: it is the last tie-break in
        # selection, so reordering here would silently change which question a
        # candidate meets third.
        for i in (pool.items if pool is not None else [])
    ]

    # A generated interview carries its own questions; a bank-backed one builds
    # them from the pool above. Never both — the source is one or the other.
    if cfg.questions:
        questions = [
            QuestionSpec(**{
                **{k: v for k, v in q.items()
                   if k in QuestionSpec.__dataclass_fields__ and k != "evaluation_criteria"},
                "evaluation_criteria": [
                    CriterionSpec(**c) if isinstance(c, dict)
                    else CriterionSpec(id=f"crit_{n}", label=str(c))
                    for n, c in enumerate(q.get("evaluation_criteria") or [])
                ],
            })
            for q in cfg.questions
        ]
        banks = [
            BankSpec(id=s.competency_id, label=s.name, weight=0.0, min_items=1)
            for s in cfg.evaluated_skills()
        ]

    criteria = sorted({c.label for q in questions for c in q.evaluation_criteria})

    return InterviewDefinition(
        interview_id=cfg.id,
        version=0,  # set by versions.publish()
        role=cfg.role,
        role_title=cfg.role_title or (pool.role_title if pool is not None else cfg.title),
        language=cfg.language,
        interview_type=cfg.interview_type or infer_interview_type(cfg.question_budget),
        recommended_duration_min=cfg.recommended_duration_min,
        difficulty=cfg.difficulty,
        job_id=cfg.job_id,
        experience_from=cfg.experience_from,
        experience_to=cfg.experience_to,
        skills=skills,
        tasks=tasks,
        questions=questions,
        banks=banks,
        evaluation=EvaluationSpec(criteria=criteria, scoring_scale=5),
        runtime=RuntimeLimits(
            question_budget=cfg.question_budget,
            max_probes_per_item=cfg.max_probes_per_item,
            max_reasks_per_item=config.MAX_REASKS_PER_ITEM,
            max_clarifies_per_item=config.MAX_CLARIFIES_PER_ITEM,
            allow_generated_probes=cfg.allow_generated_probes,
            rejoin_window_sec=config.REJOIN_WINDOW_SEC,
            speech_rate=clamp_speech_rate(cfg.speech_rate),
        ),
        closing=(pool.closing if pool is not None
                 else "That's everything. Thank you for your time."),
    )


# --------------------------------------------------------------------------- #
#  Store
# --------------------------------------------------------------------------- #
_PATH = config.DATA_DIR / "interviews.json"


def _read_all() -> dict[str, InterviewConfig]:
    if not _PATH.exists():
        return {}
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    return {k: InterviewConfig.from_dict(v) for k, v in raw.items()}


def _write_all(items: dict[str, InterviewConfig]) -> None:
    jsonfile.write_atomic(_PATH, {k: v.to_dict() for k, v in items.items()})


def _serialised(fn):
    """Serialise read-modify-write on `interviews.json`, and never tear it.

    Lower-traffic than the invitations — a recruiter edits one interview at a
    time — but the same shape of loss, and the same one-line guard.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        with jsonfile.guarded(_PATH):
            return fn(*args, **kwargs)

    return wrapper


def list_all() -> list[InterviewConfig]:
    return sorted(_read_all().values(), key=lambda i: i.created_at, reverse=True)


def get(interview_id: str) -> InterviewConfig | None:
    return _read_all().get(interview_id)


@_serialised
def save(cfg: InterviewConfig) -> InterviewConfig:
    # Every skill gets a domain proposed here rather than at the twenty call
    # sites that save an interview. `apply_to` never overwrites one that is
    # already set, so a recruiter's correction survives every later save — and
    # a skill renamed before it was ever given a domain gets a fresh proposal
    # from its new name, which is what you would want.
    for skill in cfg.skills:
        skill_master.apply_to(skill)
    cfg.updated_at = time.time()
    items = _read_all()
    items[cfg.id] = cfg
    _write_all(items)
    return cfg


@_serialised
def delete(interview_id: str) -> bool:
    items = _read_all()
    if interview_id not in items:
        return False
    del items[interview_id]
    _write_all(items)
    return True


def create(
    title: str, role: str, *, organization_id: str = "", created_by: str = ""
) -> InterviewConfig:
    """A blank draft. Skills arrive from the JD, not from a template.

    `organization_id` is the tenant anchor everything beneath this interview
    resolves ownership through, so it is set at creation from the authenticated
    principal and never from anything the client sent.
    """
    return save(
        InterviewConfig(
            id="iv_" + secrets.token_hex(5),
            title=title.strip() or "Untitled interview",
            role=role,
            organization_id=organization_id,
            created_by=created_by,
        )
    )


def publish(cfg: InterviewConfig, pool: Any, *, published_by: str = "recruiter", notes: str = ""):
    """Freeze the current configuration as the next published version.

    Returns the `InterviewVersion`. The config keeps living and can be edited
    afterwards — that is the point: the next edit becomes v(n+1), and everyone
    already invited keeps sitting v(n).
    """
    from services.data import versions  # local import: versions reads definitions

    definition = build_definition(cfg, pool)
    version = versions.publish(cfg.id, definition, published_by=published_by, notes=notes)
    cfg.status = "published"
    cfg.published_version = version.version
    save(cfg)
    return version


def ensure_default(pool) -> InterviewConfig:
    """One published interview on first boot, so the demo link works instantly.

    Seeded straight from the authored pool rather than from a JD: it represents
    the CSR screen that already existed, and a demo shouldn't require an LLM
    round-trip before the candidate link works.
    """
    from services.data import versions

    existing = _read_all().get("iv_default")
    if existing:
        # An interview that was published before versioning existed has no
        # frozen contract behind it. Mint v1 now rather than leaving its
        # candidates resolving questions from a mutable draft.
        if not versions.latest_published(existing.id):
            publish(existing, pool, published_by="system", notes="Initial version")
        return _read_all()["iv_default"]

    skills = [
        Skill(
            name=c.label,
            competency_id=c.id,
            priority="high" if c.weight >= 0.18 else "medium",
            proficiency_target=3 if c.weight >= 0.18 else 2,
            evaluated=True,
            pool_competency=c.id,
        )
        for c in pool.competencies
    ]
    cfg = save(
        InterviewConfig(
            id="iv_default",
            title=f"{pool.role_title} — standard screen",
            role=pool.role,
            role_title=pool.role_title,
            status="published",
            skills=skills,
            skills_evaluated=len(skills),
            outcomes=[
                "Customers leave an interaction with their problem solved and their time respected.",
                "Issues are escalated with enough context that nobody has to start again.",
                "Policy is applied consistently, and exceptions are argued rather than smuggled.",
            ],
        )
    )
    publish(cfg, pool, published_by="system", notes="Initial version")
    return _read_all()["iv_default"]
