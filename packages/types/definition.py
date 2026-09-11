"""`InterviewDefinition` — the central contract of the product.

Everything upstream of a candidate (the JD, the designer, the question
generator, the recruiter's edits) exists to produce one of these. Everything
downstream (the orchestrator, the scoring engine, the report) consumes one.

The orchestrator must not be able to tell where the questions came from:

    authored JSON pool  ─┐
    curated question bank ├─►  InterviewDefinition  ──►  orchestrator
    AI Question Generator ┘

That is the whole point of the seam. Today the CSR pool builds definitions
(`from_pool`); a generator plugs in by building the same object.

A published version stores the definition verbatim, so a definition is also the
unit of reproducibility: same definition + same answers = same interview.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}

#: How long each shape of interview is allowed to run, in minutes.
#:
#: The bands exist because "recommended duration" is the one number a recruiter
#: reads as a promise to the candidate, and a designer left to free-form it will
#: return 30 for a "short" interview. The type is the decision; the duration has
#: to agree with it.
DURATION_BANDS: dict[str, tuple[int, int]] = {
    "short": (8, 10),
    "medium": (15, 25),
    "deep": (35, 45),
}
INTERVIEW_TYPES = tuple(DURATION_BANDS)
DIFFICULTIES = ("easy", "medium", "hard")


def duration_band(interview_type: str) -> tuple[int, int]:
    return DURATION_BANDS.get(interview_type, DURATION_BANDS["medium"])


def duration_for(interview_type: str) -> int:
    """The duration an interview type means.

    Not a default the recruiter then tunes. "Short", "medium" and "deep" are
    the decision, and the number of minutes is what that decision *is* — a
    20-minute "short" interview and a 20-minute "medium" one are the same
    interview wearing two labels, and the label is what the rest of the
    system reasons about: how many questions the blueprint plans, how far the
    probe ladder is allowed to go, what the candidate is promised up front.
    So the type owns the number, and there is one place it comes from.
    """
    low, high = duration_band(interview_type)
    return (low + high) // 2


def clamp_duration(interview_type: str, minutes: int | None) -> int:
    """Pull a duration into its type's band.

    Used on the way out of the designer. A model that returns 30 minutes for a
    "short" interview has contradicted itself; the type is the considered
    judgement and the number is the one it is careless with, so the number moves.
    """
    low, high = duration_band(interview_type)
    if not minutes:
        return (low + high) // 2
    return max(low, min(high, int(minutes)))


# --------------------------------------------------------------------------- #
#  Priority bands → the distribution selection needs
#
#  Written once and shared, because two callers need it: the recruiter console's
#  live preview (over a draft config) and the runtime (over a published
#  definition). If those two ever disagreed, the preview would be a lie — it
#  would show a question order no candidate ever gets.
# --------------------------------------------------------------------------- #
def derive_bank_weights(pairs: list[tuple[str, str]]) -> dict[str, float]:
    """`[(bank_id, priority), ...]` → normalised share per bank.

    Several skills can share one bank, so their ranks add: a bank that answers
    two high-priority skills genuinely matters more than one that answers one.
    """
    raw: dict[str, float] = {}
    for bank, priority in pairs:
        if not bank:
            continue
        raw[bank] = raw.get(bank, 0.0) + PRIORITY_RANK.get(priority, 2)
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in raw.items()}


def derive_bank_min_items(pairs: list[tuple[str, str]]) -> dict[str, int]:
    """One question per covered bank; two where the role depends on it.

    A skill the job hangs on should not rest on a single answer.
    """
    out: dict[str, int] = {}
    for bank, priority in pairs:
        if not bank:
            continue
        want = 2 if priority == "high" else 1
        out[bank] = max(out.get(bank, 0), want)
    return out


class DefinitionError(ValueError):
    """A definition that cannot be run. Raised at publish time, never at runtime.

    Catching this when a recruiter presses Publish is a form; catching it when a
    candidate is on the line is an outage.
    """


# --------------------------------------------------------------------------- #
#  Parts
# --------------------------------------------------------------------------- #
@dataclass
class SkillSpec:
    id: str
    name: str
    priority: str = "medium"           # high | medium | low
    proficiency_target: int = 2        # 0-4
    description: str = ""
    assessment_scope: str = ""
    question_bank: str = ""            # "" = nothing can ask about it yet
    task_ids: list[str] = field(default_factory=list)
    #: The skill master domain, frozen with everything else at publication. A
    #: report read two years from now says which domain the skill was filed
    #: under AT THE TIME, not under whatever the catalogue says today.
    domain: str = ""

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 2)


@dataclass
class TaskSpec:
    id: str
    description: str
    #: A short label for the task, so a review screen has something to put in a
    #: heading that is not the whole sentence.
    name: str = ""
    priority: str = "medium"
    outcome: str = ""
    skill_ids: list[str] = field(default_factory=list)   # Task assesses Skill(s)

    @property
    def label(self) -> str:
        return self.name or self.description[:60]


@dataclass
class BankSpec:
    """A grouping of questions — what the progress rail counts against.

    A "bank" is whatever the questions were organised by upstream: the authored
    pool's competencies today, a generated skill cluster tomorrow. The runtime
    only needs its id, its human label, and how many questions it deserves.
    """

    id: str
    label: str
    weight: float = 0.0        # authored default share, used when no skill maps to it
    min_items: int = 1


#: What a question can be. Not every role is interviewed the same way: a
#: behavioural question about a payments engineer's concurrency reasoning
#: measures how well they tell a story, not whether they can reason about it.
QUESTION_TYPES = ("behavioral", "situational", "technical", "task_based")


@dataclass
class CriterionSpec:
    """One thing a reviewer could agree or disagree with about an answer.

    Written BEFORE the candidate answers, and frozen with the version. A rubric
    assembled after the fact — or from the candidate's own words — is not a
    rubric, it is a description.
    """

    id: str
    label: str
    description: str = ""
    importance: str = "medium"   # high | medium | low


@dataclass
class QuestionSpec:
    """The question contract, validated rather than assumed.

    Consumed unchanged by the candidate runtime: `Pool.from_definition` reads
    `question_text`, `competency`, `difficulty`, `looking_for`, `probe_bank`,
    `clarify`, `probe_eligible` and `time_budget_sec` — so a generated question
    and an authored one are the same object to the orchestrator.

    `competency` is the grouping selection counts against. For a generated pool
    it is the primary skill id, which is what makes the existing priority →
    coverage machinery work on generated questions with no runtime change.
    """

    id: str
    question_text: str
    competency: str = ""
    #: The one skill this question is selected and counted against.
    skill_id: str = ""
    #: Skills the answer also evidences. Never used for selection — a question
    #: counted against two skills would satisfy two floors with one answer.
    secondary_skill_ids: list[str] = field(default_factory=list)
    task_id: str = ""
    question_type: str = "situational"
    difficulty: str = "medium"
    expected_signal: str = ""
    looking_for: list[str] = field(default_factory=list)
    evaluation_criteria: list[CriterionSpec] = field(default_factory=list)
    probe_eligible: bool = True
    max_probes: int = 2
    time_budget_sec: int = 90
    probe_bank: list[str] = field(default_factory=list)
    clarify: str = ""
    modality: list[str] = field(default_factory=lambda: ["voice", "text"])
    type: str = "scenario"
    source: str = "authored"
    #: Which blueprint slot produced it, so a single question can be
    #: regenerated against the same coverage requirement it was created for.
    slot_id: str = ""

    @property
    def primary_skill_id(self) -> str:
        """§9's name for it. `skill_id` is the stored field; this is the word
        the rest of the product uses when it matters that there is only one."""
        return self.skill_id

    def all_skill_ids(self) -> list[str]:
        return [self.skill_id, *self.secondary_skill_ids] if self.skill_id else list(
            self.secondary_skill_ids
        )


@dataclass
class EvaluationSpec:
    criteria: list[str] = field(default_factory=list)
    scoring_scale: int = 5
    # Named here so a report can state the rule it was produced under rather
    # than implying one. Unanswered items are excluded from the denominator —
    # they are not zeros, and a candidate must not lose points for silence.
    unanswered_policy: str = "excluded"


@dataclass
class RuntimeLimits:
    """What the orchestrator is allowed to do in this interview."""

    question_budget: int = 8
    max_probes_per_item: int = 2
    max_reasks_per_item: int = 2
    max_clarifies_per_item: int = 2
    allow_generated_probes: bool = True
    rejoin_window_sec: int = 3600


# --------------------------------------------------------------------------- #
#  The contract
# --------------------------------------------------------------------------- #
@dataclass
class InterviewDefinition:
    interview_id: str = ""
    version: int = 0
    role: str = ""
    role_title: str = ""
    language: str = "en"
    #: The job this interview was designed from. Carried on the definition so a
    #: published version can be read back without joining to a mutable record.
    job_id: str = ""
    experience_from: int = 0
    experience_to: int = 0

    interview_type: str = "medium"      # short | medium | deep
    recommended_duration_min: int = 20
    difficulty: str = "medium"

    skills: list[SkillSpec] = field(default_factory=list)
    tasks: list[TaskSpec] = field(default_factory=list)
    questions: list[QuestionSpec] = field(default_factory=list)
    banks: list[BankSpec] = field(default_factory=list)

    evaluation: EvaluationSpec = field(default_factory=EvaluationSpec)
    runtime: RuntimeLimits = field(default_factory=RuntimeLimits)

    closing: str = "That's everything. Thank you for your time."

    # ------------------------------------------------------------------ #
    #  Selection inputs
    #
    #  The distribution the runtime needs is DERIVED from priority bands here,
    #  in one place, so the recruiter console's preview and a live interview
    #  cannot drift apart. Several skills can share a question bank, so their
    #  ranks add: a bank answering two high-priority skills genuinely matters
    #  more than one answering a single one.
    # ------------------------------------------------------------------ #
    def evaluated_skills(self) -> list[SkillSpec]:
        return list(self.skills)

    def high_priority_skills(self) -> list[SkillSpec]:
        """The skills the role depends on.

        Derived from `priority == "high"`, never asked of the model a second
        time. A separate call could disagree with the priorities it just set.
        """
        return [s for s in self.skills if s.priority == "high"]

    def tasks_for_skill(self, skill_id: str) -> list[TaskSpec]:
        return [t for t in self.tasks if skill_id in t.skill_ids]

    def covered_skills(self) -> list[SkillSpec]:
        return [s for s in self.skills if s.question_bank]

    def _bank_pairs(self) -> list[tuple[str, str]]:
        return [(s.question_bank, s.priority) for s in self.covered_skills()]

    def bank_weights(self) -> dict[str, float]:
        return derive_bank_weights(self._bank_pairs())

    def bank_min_items(self) -> dict[str, int]:
        return derive_bank_min_items(self._bank_pairs())

    def question(self, question_id: str) -> QuestionSpec | None:
        return next((q for q in self.questions if q.id == question_id), None)

    def skill(self, skill_id: str) -> SkillSpec | None:
        return next((s for s in self.skills if s.id == skill_id), None)

    def skills_for_bank(self, bank: str) -> list[SkillSpec]:
        return [s for s in self.skills if s.question_bank == bank]

    def bank_label(self, bank_id: str) -> str:
        found = next((b for b in self.banks if b.id == bank_id), None)
        return found.label if found else bank_id

    # ------------------------------------------------------------------ #
    #  Validation — run before publish, never during an interview
    # ------------------------------------------------------------------ #
    def validate(self) -> list[str]:
        """Every problem at once, in recruiter-readable language.

        Returns the list rather than raising on the first one: a form that
        reports one error per submission is a form nobody finishes.
        """
        problems: list[str] = []

        if not self.questions:
            problems.append("This interview has no questions, so nothing would be asked.")
        if self.runtime.question_budget < 1:
            problems.append("The question budget must be at least 1.")
        if self.evaluation.scoring_scale < 2:
            problems.append("The scoring scale must have at least two points.")

        seen_q: set[str] = set()
        for q in self.questions:
            if not q.id:
                problems.append("A question has no id.")
                continue
            if q.id in seen_q:
                problems.append(f"Question id {q.id!r} appears more than once.")
            seen_q.add(q.id)
            if not q.question_text.strip():
                problems.append(f"Question {q.id} has no text.")
            if q.probe_eligible and q.max_probes > 0 and not q.probe_bank:
                # Not fatal: generation may cover it. But a generated probe can
                # be rejected by a guardrail, and then there is nothing to say.
                problems.append(
                    f"Question {q.id} can be probed but has no authored fallback probe — "
                    f"if a generated follow-up is rejected there is nothing to ask instead."
                )

        skill_ids = {s.id for s in self.skills}
        seen_s: set[str] = set()
        for s in self.skills:
            if s.id in seen_s:
                problems.append(f"Skill id {s.id!r} appears more than once.")
            seen_s.add(s.id)
            if s.priority not in PRIORITY_RANK:
                problems.append(f"Skill {s.name!r} has an unknown priority {s.priority!r}.")
            if not 0 <= s.proficiency_target <= 4:
                problems.append(f"Skill {s.name!r} has a proficiency target outside 0-4.")

        for t in self.tasks:
            unknown = [sid for sid in t.skill_ids if sid not in skill_ids]
            if unknown:
                problems.append(
                    f"Task {t.description[:40]!r} requires skills that aren't in this "
                    f"interview: {', '.join(unknown)}."
                )

        if self.interview_type not in INTERVIEW_TYPES:
            problems.append(
                f"Interview type {self.interview_type!r} is not one of "
                f"{', '.join(INTERVIEW_TYPES)}."
            )
        if self.difficulty not in DIFFICULTIES:
            problems.append(
                f"Difficulty {self.difficulty!r} is not one of {', '.join(DIFFICULTIES)}."
            )
        if self.interview_type in DURATION_BANDS:
            low, high = DURATION_BANDS[self.interview_type]
            if not low <= self.recommended_duration_min <= high:
                problems.append(
                    f"A {self.interview_type} interview runs {low}-{high} minutes, but the "
                    f"recommended duration is {self.recommended_duration_min}."
                )

        if self.skills and not self.covered_skills():
            problems.append(
                "None of the skills has a question bank behind it, so no question "
                "would be selected."
            )
        return problems

    def require_valid(self) -> None:
        problems = self.validate()
        if problems:
            raise DefinitionError("; ".join(problems))

    # ------------------------------------------------------------------ #
    #  Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "InterviewDefinition":
        d = dict(d or {})
        skills = [SkillSpec(**s) for s in d.pop("skills", []) or []]
        banks = [BankSpec(**b) for b in d.pop("banks", []) or []]
        tasks = [TaskSpec(**t) for t in d.pop("tasks", []) or []]
        questions = []
        for raw in d.pop("questions", []) or []:
            raw = dict(raw)
            criteria = raw.pop("evaluation_criteria", []) or []
            known = set(QuestionSpec.__dataclass_fields__)
            spec = QuestionSpec(**{k: v for k, v in raw.items() if k in known})
            spec.evaluation_criteria = [
                # Tolerate the older shape, where criteria were bare strings.
                CriterionSpec(**c) if isinstance(c, dict)
                else CriterionSpec(id=f"crit_{n}", label=str(c))
                for n, c in enumerate(criteria)
            ]
            questions.append(spec)
        evaluation = EvaluationSpec(**(d.pop("evaluation", {}) or {}))
        runtime = RuntimeLimits(**(d.pop("runtime", {}) or {}))
        known = set(InterviewDefinition.__dataclass_fields__)
        defn = InterviewDefinition(**{k: v for k, v in d.items() if k in known})
        defn.skills = skills
        defn.banks = banks
        defn.tasks = tasks
        defn.questions = questions
        defn.evaluation = evaluation
        defn.runtime = runtime
        return defn

    #: Identity, not content. Excluded from the checksum below.
    _IDENTITY_FIELDS = ("interview_id", "version")

    def checksum(self) -> str:
        """Content hash — the thing "has this actually changed?" is decided on.

        `interview_id` and `version` are excluded deliberately. They are the
        definition's identity, not its content, and including them makes every
        checksum unique by construction: re-publishing an untouched interview
        would mint v2, v3, v4 forever, and "you have unpublished changes" would
        be permanently true. What a recruiter means by "changed" is the
        questions, the skills and the limits — so that is what is hashed.

        Sorted keys and no whitespace, so the same content always hashes the
        same regardless of how it was assembled.
        """
        content = {
            k: v for k, v in self.to_dict().items() if k not in self._IDENTITY_FIELDS
        }
        blob = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
