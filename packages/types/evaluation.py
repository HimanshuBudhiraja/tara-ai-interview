"""The evaluation contract: evidence, depth, and the five-criterion assessment.

Two ideas run through this file and are deliberately kept apart, because
collapsing them is the most common way an interview evaluation goes wrong:

    depth_reached       how far the INTERVIEW investigated
    depth_demonstrated  how far the CANDIDATE's evidence actually goes

Reaching the deepest stage is not an achievement — it usually means the earlier
answers left something unresolved. A candidate who answered so well at the first
stage that no follow-up was needed has not done worse than one who was probed
twice; the runtime stopped because it had what it needed. So the engine never
rewards interview length, and `depth_reached` is a fact about the conversation
while `depth_demonstrated` is a judgement about the person.

The depth stages are the runtime's existing probe ladder, named for what each
turn is rather than invented as a parallel system:

    direct       the answer to the question as it was asked
    probed       the answer to the first follow-up
    deep_probed  the answer to the second follow-up

`max_probes` caps the ladder at three, which is where three stages come from.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# --------------------------------------------------------------------------- #
#  Engine version
# --------------------------------------------------------------------------- #
#: The methodology that produced an evaluation, persisted with every record.
#:
#: This will change — the extractor's dimension tagging and the discussion
#: threshold are both known to need calibration — and when it does, old
#: evaluations must stay attributable to the engine that actually produced them
#: rather than silently reading as though today's rules made them. Bump it
#: whenever a change would give the same transcript a different result.
#:
#: v1 -> v2: coverage-aware assessment, which is two changes with one idea
#: behind it — report what the interview established, and report separately how
#: much of it the interview reached.
#:
#:   * The score's denominator. v1 counted 25 points for every skill the
#:     published version listed, including ones nobody asked about, so a
#:     candidate who answered one skill superbly read as 21/100 = `Poor`. v2
#:     scores over substantively discussed skills and reports the rest as
#:     `Coverage`.
#:   * The recommendation's coverage gate. v1 counted only skills never asked;
#:     v2 counts every skill without substantive evidence, so a candidate with
#:     one skill of six assessed cannot be declared unsuitable on the strength
#:     of that one — the mirror of the denominator problem.
#:
#: The same transcript recommends differently under the two, which is exactly
#: what this constant exists to record: v1 evaluations stay attributable to v1
#: and are never silently re-read under v2's arithmetic.
ENGINE_VERSION = "deep_evidence_v2"

#: The shape of the assembled assessment result, which recruiter-facing clients
#: read. Separate from `ENGINE_VERSION` because the two change for different
#: reasons: the engine version changes when the same transcript would score
#: differently, this changes when the same score would be shaped differently.
RESULT_CONTRACT_VERSION = "assessment_result_v1"


# --------------------------------------------------------------------------- #
#  Depth
# --------------------------------------------------------------------------- #
DepthStage = Literal["direct", "probed", "deep_probed"]

#: Ordered shallowest-first. Position is meaningful — `STAGES.index` is how the
#: engine compares two stages.
STAGES: tuple[DepthStage, ...] = ("direct", "probed", "deep_probed")

STAGE_LABEL: dict[str, str] = {
    "direct": "Direct answer",
    "probed": "Probed once",
    "deep_probed": "Probed twice",
}

#: What each stage of the ladder is positioned to establish. Descriptive, not
#: prescriptive: a candidate can demonstrate trade-off reasoning in their first
#: answer, and that counts.
STAGE_PURPOSE: dict[str, str] = {
    "direct": (
        "Whether the candidate can answer the question as asked: the concepts, the "
        "terminology, the foundational knowledge."
    ),
    "probed": (
        "Whether they can apply it — reasoning, a worked example, a decision they "
        "made and why."
    ),
    "deep_probed": (
        "Whether they can go further: edge cases, failure modes, scale, "
        "architectural or production judgement."
    ),
}

#: The dimensions evidence can demonstrate. These are NOT scores — they describe
#: what kind of evidence exists, and feed the criteria that already exist.
DepthDimension = Literal[
    "conceptual_understanding",
    "practical_application",
    "reasoning",
    "trade_offs",
    "edge_cases",
    "production_judgment",
]

DIMENSIONS: tuple[DepthDimension, ...] = (
    "conceptual_understanding",
    "practical_application",
    "reasoning",
    "trade_offs",
    "edge_cases",
    "production_judgment",
)

#: Which criterion each dimension chiefly supports. Used to explain a score, not
#: to compute one — the model judges the criteria and this says why.
DIMENSION_SUPPORTS: dict[str, str] = {
    "conceptual_understanding": "Accuracy",
    "practical_application": "Depth",
    "reasoning": "Problem-Solving",
    "trade_offs": "Problem-Solving",
    "edge_cases": "Depth",
    "production_judgment": "Depth",
}


def stage_index(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else 0


def deeper_of(a: str, b: str) -> str:
    return a if stage_index(a) >= stage_index(b) else b


# --------------------------------------------------------------------------- #
#  Experience calibration
# --------------------------------------------------------------------------- #
ExperienceLevel = Literal["Junior", "Mid", "Senior", "Lead / Expert"]

#: (max_years, level). The same answer is not worth the same at every level —
#: an intermediate answer can be excellent for a junior and below the bar for a
#: senior, and that is correct behaviour rather than inconsistency.
EXPERIENCE_BANDS: tuple[tuple[int, ExperienceLevel], ...] = (
    (2, "Junior"),
    (5, "Mid"),
    (8, "Senior"),
    (99, "Lead / Expert"),
)

EXPECTATIONS: dict[str, str] = {
    "Junior": (
        "Foundational knowledge, basic terminology and textbook-level understanding. "
        "Practical experience is a bonus, not the bar."
    ),
    "Mid": (
        "Working knowledge, real-world examples, hands-on experience with the tools, "
        "and an ability to name trade-offs."
    ),
    "Senior": (
        "Deep expertise, architectural thinking, explanation at a level that could "
        "mentor someone, and nuanced problem-solving."
    ),
    "Lead / Expert": (
        "Strategic thinking, system design, knowledge across neighbouring domains, "
        "and leadership in technical decisions."
    ),
}


def experience_level(years_to: int) -> ExperienceLevel:
    for ceiling, level in EXPERIENCE_BANDS:
        if years_to <= ceiling:
            return level
    return "Lead / Expert"


# --------------------------------------------------------------------------- #
#  Evidence
# --------------------------------------------------------------------------- #
EvidenceType = Literal["supported", "partial", "contradicted", "missing", "unclear"]
EvidenceStrength = Literal["strong", "moderate", "weak"]

CRITERIA: tuple[str, ...] = (
    "Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication",
)

DiscussionStatus = Literal["discussed", "mentioned", "not_discussed"]

EvidenceConfidence = Literal["high", "medium", "low", "insufficient"]


@dataclass
class EvidenceItem:
    """One thing the candidate actually said, and what it evidences.

    `candidate_quote` must appear verbatim in the transcript. Anything that does
    not is discarded before it can reach a score: a quotation the candidate
    never said, inside a document used to make a hiring decision, is the worst
    failure this engine can have.
    """

    skill_id: str
    skill_name: str
    question_id: str
    turn_id: str
    depth_stage: DepthStage
    depth_dimension: DepthDimension
    candidate_quote: str
    evidence_type: EvidenceType
    evidence_strength: EvidenceStrength
    supports_criterion: str
    task_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DepthEvaluation:
    """How far the interview went, and how far the candidate went."""

    depth_reached: DepthStage = "direct"
    depth_demonstrated: DepthStage = "direct"
    dimensions_demonstrated: list[str] = field(default_factory=list)
    dimensions_missing: list[str] = field(default_factory=list)
    evidence_confidence: EvidenceConfidence = "insufficient"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillAssessment:
    """One row of the evaluator's output contract.

    The five criteria are the canonical ones and are not extended. Practical
    experience, where the candidate demonstrated it, is evidence supporting
    Depth, Problem-Solving and Accuracy, and is described in the remarks — not a
    sixth number.
    """

    skill_name: str
    discussion_status: DiscussionStatus
    score: int = 0
    remarks: str = ""
    accuracy: int = 0
    depth: int = 0
    clarity: int = 0
    problem_solving: int = 0
    communication: int = 0
    depth_evaluation: DepthEvaluation = field(default_factory=DepthEvaluation)
    #: Internal: the evidence behind the row. Not part of the wire contract, but
    #: carried so a reviewer can trace any score back to what was said.
    evidence: list[EvidenceItem] = field(default_factory=list)
    skill_id: str = ""

    def criteria(self) -> dict[str, int]:
        return {
            "Accuracy": self.accuracy,
            "Depth": self.depth,
            "Clarity": self.clarity,
            "Problem-Solving": self.problem_solving,
            "Communication": self.communication,
        }

    def to_dict(self) -> dict[str, Any]:
        """The canonical wire shape, with the criteria as top-level keys."""
        return {
            "skill_name": self.skill_name,
            "discussion_status": self.discussion_status,
            "score": self.score,
            "remarks": self.remarks,
            **self.criteria(),
            "depth_evaluation": self.depth_evaluation.to_dict(),
        }


@dataclass
class CandidateDetails:
    name: str = ""
    job_role: str = ""
    experience_level: str = ""
    total_score: int = 0
    overall_rating: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The only values the recommendation may take.
RECOMMENDATIONS: tuple[str, ...] = (
    "Not suitable for this role",
    "Needs further evaluation",
    "Proceed to next round",
)

RATING_BANDS: tuple[tuple[float, str], ...] = (
    (40.0, "Poor"),
    (60.0, "Average"),
    (80.0, "Good"),
    (100.1, "Excellent"),
)


#: What a rating is when there is nothing to rate.
#:
#: Not a fifth band — the absence of one. With no skill substantively assessed
#: there is no denominator, so "Poor" would be a verdict on the interview
#: reported as a verdict on the candidate. `overall_rating` still maps a
#: percentage to a band and knows nothing about this; `aggregate` chooses it.
UNRATED = "Not rated"


def overall_rating(percentage: float) -> str:
    for ceiling, label in RATING_BANDS:
        if percentage < ceiling:
            return label
    return "Excellent"


@dataclass
class Coverage:
    """How much of the assessment the interview actually got to.

    Kept apart from the score on purpose. The score answers "how well did the
    candidate do on what we asked?"; coverage answers "how much did we ask?".
    Rolling the second into the first is what made a candidate who answered one
    skill superbly read as `Poor` — three questions nobody asked were being
    counted against them.

    Coverage is a completeness and confidence figure. It is deliberately NOT a
    score: it is never added to one, never averaged with one, and never scaled by
    one.
    """

    skills_total: int = 0
    skills_discussed: int = 0
    skills_mentioned: int = 0
    skills_not_discussed: int = 0
    #: `discussed / total`, as a percentage. Only substantive evaluation counts:
    #: a skill that was merely mentioned was not covered.
    coverage_percentage: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def of(statuses: list[str]) -> "Coverage":
        total = len(statuses)
        discussed = sum(1 for s in statuses if s == "discussed")
        return Coverage(
            skills_total=total,
            skills_discussed=discussed,
            skills_mentioned=sum(1 for s in statuses if s == "mentioned"),
            skills_not_discussed=sum(1 for s in statuses if s == "not_discussed"),
            coverage_percentage=round(discussed / total * 100, 1) if total else 0.0,
        )


@dataclass
class Evaluation:
    """The whole evaluation. Shape is the canonical evaluator contract."""

    candidate_details: CandidateDetails = field(default_factory=CandidateDetails)
    skill_assessment: list[SkillAssessment] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    areas_for_improvement: list[str] = field(default_factory=list)
    recommendation: str = "Needs further evaluation"
    recommendation_explaination: str = ""   # spelling is part of the contract
    #: Internal provenance. Not part of the wire contract.
    session_id: str = ""
    interview_id: str = ""
    interview_version: int = 0
    #: 25 x the number of DISCUSSED skills. Mentioned and not-discussed skills
    #: contribute to neither the total nor the maximum — they are reported as
    #: coverage instead, which is the whole point of keeping the two apart.
    maximum_possible_score: int = 0
    percentage: float = 0.0
    coverage: Coverage = field(default_factory=Coverage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_details": self.candidate_details.to_dict(),
            "skill_assessment": [s.to_dict() for s in self.skill_assessment],
            "strengths_and_improvement_areas": {
                "strengths": list(self.strengths),
                "areas_for_improvement": list(self.areas_for_improvement),
            },
            "recommendation": self.recommendation,
            # Spelled this way on purpose: it is the existing contract, and a
            # silent rename would break whatever already reads it. The corrected
            # spelling is offered alongside it, never instead of it.
            "recommendation_explaination": self.recommendation_explaination,
            "recommendation_explanation": self.recommendation_explaination,
            "coverage": self.coverage.to_dict(),
        }
