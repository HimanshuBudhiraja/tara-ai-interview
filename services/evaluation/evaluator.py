"""The evaluation engine: five criteria per skill, aggregated deterministically.

    evidence  ─►  per-skill judgement (AI)  ─►  constraints (code)  ─►  aggregation (code)

The division of labour is the design. A model reads evidence and judges whether
an answer was accurate, deep, clear, well-reasoned and well-communicated — that
is a genuinely qualitative call. Everything that follows is arithmetic and rules,
and is computed here: discussion status constraints, score bounds, totals,
percentage, rating, recommendation. A model asked for the final number would
produce one that quietly disagrees with the criteria it just set.

Two things the engine will not do:

  * **Reward interview length.** Depth reached is a fact about the conversation;
    depth demonstrated is a judgement about the candidate. A skill settled in
    one strong answer is not scored below one that needed two follow-ups.
  * **Treat a coverage gap as a weakness.** A skill nobody asked about tells you
    nothing about the candidate. It scores zero because the contract says so,
    and the recommendation rules treat it as a gap in the interview, not a
    finding about the person.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from packages.schemas import SKILL_ASSESSMENT, SKILL_ASSESSMENT_ACCEPT
from packages.types.definition import InterviewDefinition, SkillSpec
from packages.types.evaluation import (
    CRITERIA,
    UNRATED,
    Coverage,
    DIMENSIONS,
    EXPECTATIONS,
    RECOMMENDATIONS,
    STAGE_PURPOSE,
    CandidateDetails,
    DepthEvaluation,
    DepthStage,
    Evaluation,
    EvidenceItem,
    SkillAssessment,
    deeper_of,
    experience_level,
    overall_rating,
)
from services.ai.gateway import AIError, Workload, get_gateway
from services.ai.workloads import untrusted
from services.evaluation.transcript import InterviewTranscript

SYSTEM = """You assess ONE skill from a job interview, using only the evidence you are given.

You are given the skill, what it was supposed to assess, the candidate's expected experience
level, and a list of evidence items — each a verbatim quote from the candidate with what it
demonstrates. Every quote has already been checked against the transcript.

Return STRICT JSON with: discussion_status, Accuracy, Depth, Clarity, Problem-Solving,
Communication, remarks, dimensions_demonstrated, dimensions_missing, evidence_confidence.

SCORE WHAT WAS DEMONSTRATED, against the EXPECTED EXPERIENCE LEVEL you are given. The same
answer is not worth the same at every level: an answer showing solid working knowledge is
excellent for a junior and below the bar for a senior. Never infer knowledge that was not
evidenced, and never credit a claim — "I'm an expert in this" is not evidence of expertise.

Each criterion is 0-5:
  1  Very limited — superficial awareness, cannot explain it adequately, substantially below
     what the level expects.
  2  Basic but incomplete — foundations there, significant gaps, little application.
  3  The expected bar — the minimum depth this skill and this experience level call for.
  4  Strong — beyond the minimum: practical reasoning, worked examples, trade-offs.
  5  Excellent — exceptional for the expected level: nuance, edge cases, trade-off analysis,
     production judgement where the role calls for it.

Accuracy: technical and conceptual correctness, correct terminology. Incorrect statements
REDUCE it. Do not offset a wrong answer with confident delivery or good phrasing.

Depth: how far below the surface the answer goes, relative to the expected level.

Problem-Solving: demonstrated reasoning — decomposition, diagnosis, alternatives considered,
a decision made, trade-offs named, validation. "I'd use X" without a reason is not the same as
problem, constraints, alternatives, decision, trade-off.

Clarity and Communication: structure, relevance, explaining reasoning, responding to what was
actually asked. SPEECH-TO-TEXT ARTEFACTS ARE NOT THE CANDIDATE'S FAULT. Repetition, garbling,
dropped words and transcription errors must NOT reduce these scores when the substance is
understandable. Do not confuse a bad transcript with a bad communicator.

Practical experience — "I implemented", "I debugged", "in my last project we…" — is strong
evidence supporting Depth, Problem-Solving and Accuracy, and belongs in the remarks. It is not
a separate score. Generic knowledge ("you can use Redis for caching") is not practical
experience; a decision they personally made is.

LENGTH IS NOT DEPTH. A long answer that restates the question, hedges, or describes why the
topic matters has demonstrated nothing, however many words it takes. A short answer that names
the mechanism, the decision and the cost has demonstrated all three. Two answers on the same
topic where one is forty words of substance and the other two hundred words of preamble must
not receive the same scores — score the substance, and let the length be irrelevant to it.

Contradictions: if evidence conflicts — one answer correct, another wrong — do not quietly
prefer the favourable one. Weigh the inconsistency in Accuracy, Depth and Problem-Solving, and
explain it in the remarks.

Do NOT report a depth figure. How far the candidate's evidence goes is computed from the
dimensions of the validated evidence, outside your answer, and asking you for it produced a
figure that tracked the interview's probe count instead. The context tells you how far the
interview probed only so you can judge the criteria fairly: a skill asked once has had less
opportunity to produce evidence than one followed up twice, and that is context, not a score.

evidence_confidence: how sure you are, given how much substantive evidence there is, how
directly the skill was tested, and whether the transcript was clean —
  high | medium | low | insufficient
Confidence is not capability. "Strong evidence, low confidence" and "weak evidence, high
confidence" are both real and both worth saying.

remarks: two or three sentences, QUALITATIVE ONLY. Never put a number or a score in the
remarks. Describe what the answers did and did not show.

Never use age, family status, religion, ethnicity, nationality, immigration status, health,
disability, politics or any other protected characteristic. Never treat how the candidate
sounded — nervous, confident, flat — as evidence of capability."""


@dataclass
class EvaluationReport:
    """What had to be corrected on the way out of the model."""

    adjustments: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.adjustments.append(message)


# --------------------------------------------------------------------------- #
#  Discussion status — decided from the evidence, not asked for
# --------------------------------------------------------------------------- #
#: A turn shorter than this cannot be a substantive answer whatever it contains.
SUBSTANTIVE_WORDS = 12


def discussion_status_for(
    skill: SkillSpec,
    transcript: InterviewTranscript,
    evidence: list[EvidenceItem],
) -> str:
    """`discussed` / `mentioned` / `not_discussed`, decided in code.

    Deliberately not left to the model. Whether a skill was actually put to the
    candidate is a fact about the interview — the questions asked and the
    answers given — and a model reading a rich answer to a different question
    will call the skill "discussed" because the words appear in it.
    """
    questions = transcript.for_skill(skill.id)
    answered = [q for q in questions if q.answered]

    if answered:
        substantive = any(
            len(t.answer.split()) >= SUBSTANTIVE_WORDS
            for q in answered for t in q.usable_turns()
        )
        if substantive:
            return "discussed"
        # Asked, but nothing substantive came back. That is a real answer to a
        # real question and still not a demonstration.
        return "mentioned"

    # No question targeted this skill. Evidence can still exist — the candidate
    # brought it up while answering something else — but that is a mention.
    if any(e.skill_id == skill.id for e in evidence):
        return "mentioned"
    return "not_discussed"


# --------------------------------------------------------------------------- #
#  Depth demonstrated — bounded by the evidence
# --------------------------------------------------------------------------- #
#: Which stage a dimension of evidence corresponds to, when the candidate
#: demonstrates it FIRMLY — see the strength rule in `depth_demonstrated_from`.
#: This is how a first answer can demonstrate "probed" depth: the stage is about
#: the substance, not about which rung it was said on.
#:
#: `trade_offs` stays at `probed`, and the decision was measured rather than
#: argued. The depth rubric separates "some trade-offs" at probed from
#: "meaningful trade-offs" at deep, and the obvious way to express that here is
#: to let a STRONG trade-off — "an alternative that was WEIGHED and something
#: GIVEN UP", by the extractor's own definition — carry the deeper stage. That
#: was tried and reverted:
#:
#:   depth suite      31/46 → 37/46   three cases whose only deep evidence was a
#:                                    real trade-off stopped being capped
#:   gold benchmark   35/38 → 30/38   five cases moved the other way, including
#:                                    an answer with no alternative in it at all
#:                                    that the extractor tagged `trade_offs`
#:                                    anyway, and was then recorded as deep
#:
#: The premise the promotion rested on is that `evidence_strength` reliably
#: separates a weighed alternative from a mentioned one. On this model it does
#: not, and the tier that a single generous tag can reach is the wrong place to
#: find that out. So the cap stays, with a known cost: an answer whose deepest
#: evidence is a trade-off and nothing else is recorded as `probed`. That is
#: understating three of forty-six cases rather than overstating five of
#: thirty-eight, and the understatement is visible on the row — the trade-off
#: appears under demonstrated evidence, next to the figure.
DIMENSION_STAGE: dict[str, DepthStage] = {
    "conceptual_understanding": "direct",
    "practical_application": "probed",
    "reasoning": "probed",
    "trade_offs": "probed",
    "edge_cases": "deep_probed",
    "production_judgment": "deep_probed",
}


def depth_demonstrated_from(evidence: list[EvidenceItem]) -> DepthStage:
    """The deepest stage the candidate's own evidence actually supports.

    Three conditions, and they are one idea applied consistently: a claim about
    depth needs evidence firm enough to carry it.

      * the evidence must be `supported` — partial, unclear and contradicted
        evidence establishes nothing about how deep the candidate went;
      * `weak` evidence carries no stage at all — gesturing at a failure mode is
        not demonstrating failure-mode thinking;
      * anything DEEPER than `direct` requires `strong` evidence. Moderate
        evidence still establishes that the candidate knows the concept; it is
        not enough to establish that they applied it, weighed it, or reasoned
        about what it costs in production.

    The third condition was added after measurement, and the measurement matters
    because the alternative was guessing. `depth_demonstrated_from` takes the
    DEEPEST stage over all items, so a single generously-tagged item sets the
    figure for the whole skill — and a benchmark run found the extractor tagging
    a dimension from a surface marker: a purpose clause read as `reasoning`, a
    tool name as `practical_application`, the word "provider" as
    `production_judgment`. Two rounds of prompt work reduced that and did not
    remove it.

    Requiring `strong` for the deeper stages is not a new principle. It is the
    `weak` rule above, applied one notch further: `evidence_strength` is the
    extractor's own judgement of how firmly a quote establishes the thing, made
    per item against the actual words, and the deeper claims are the ones that
    need the firm grade. Agreement with the gold labels went from 0.711 to 0.895
    on the same extraction, and no case that legitimately demonstrated depth
    lost it.
    """
    demonstrated: DepthStage = "direct"
    for item in evidence:
        if item.evidence_type != "supported":
            continue
        if item.evidence_strength == "weak":
            continue
        stage = DIMENSION_STAGE.get(item.depth_dimension, "direct")
        if stage != "direct" and item.evidence_strength != "strong":
            continue
        demonstrated = deeper_of(demonstrated, stage)
    return demonstrated


def dimensions_split(evidence: list[EvidenceItem]) -> tuple[list[str], list[str]]:
    shown = {
        e.depth_dimension for e in evidence
        if e.evidence_type == "supported" and e.evidence_strength != "weak"
    }
    return sorted(shown), sorted(d for d in DIMENSIONS if d not in shown)


def confidence_from(
    evidence: list[EvidenceItem], status: str, questions_asked: int
) -> str:
    """How much weight this row can carry.

    Confidence is not capability: "we do not know" is a more useful thing to
    report than an invented low score.
    """
    if status == "not_discussed":
        return "insufficient"
    supported = [e for e in evidence if e.evidence_type == "supported"]
    strong = [e for e in supported if e.evidence_strength == "strong"]
    contradicted = [e for e in evidence if e.evidence_type == "contradicted"]
    unclear = [e for e in evidence if e.evidence_type == "unclear"]

    if status == "mentioned" or not supported:
        return "insufficient" if not supported else "low"
    if contradicted:
        # Inconsistent evidence is exactly the case where a confident score is
        # least deserved.
        return "low" if len(contradicted) >= len(strong) else "medium"
    if len(unclear) > len(supported):
        return "low"
    if len(strong) >= 2 and questions_asked >= 1:
        return "high"
    if supported:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
#  Constraints — applied to whatever the model returned
# --------------------------------------------------------------------------- #
def apply_constraints(
    raw: dict[str, Any], status: str, skill_name: str, report: EvaluationReport
) -> dict[str, int]:
    """The contract's scoring rules, enforced rather than requested.

    `discussed` floors every criterion at 1; `mentioned` caps them at 1;
    `not_discussed` zeroes them. A model that ignores any of these does not get
    to change what the row says.
    """
    scores: dict[str, int] = {}
    for criterion in CRITERIA:
        if criterion not in raw:
            # Distinct from a value out of bounds: the model did not judge this
            # at all. Recorded in its own words so a row resting on a default is
            # never mistaken for a row the model actually assessed.
            report.note(
                f"{skill_name}: {criterion} was absent from the judgement and defaulted "
                f"before the contract's bounds were applied"
            )
        value = raw.get(criterion, 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        value = max(0, min(5, value))

        if status == "not_discussed" and value != 0:
            report.note(f"{skill_name}: {criterion} zeroed — the skill was never discussed")
            value = 0
        elif status == "mentioned" and value > 1:
            report.note(
                f"{skill_name}: {criterion} capped at 1 — the skill was mentioned, "
                f"not demonstrated"
            )
            value = 1
        elif status == "discussed" and value < 1:
            report.note(
                f"{skill_name}: {criterion} raised to 1 — a discussed skill cannot score zero"
            )
            value = 1
        scores[criterion] = value
    return scores


_NUMBER_IN_REMARKS = __import__("re").compile(
    r"\b(?:scored?|rating|rated|score of)\b[^.]{0,20}\b\d\b|\b\d\s*/\s*5\b|\b\d\s+out of\s+5\b",
    __import__("re").I,
)


def clean_remarks(remarks: str, status: str, skill_name: str, report: EvaluationReport) -> str:
    """Remarks are qualitative. A number in them is a second, unaudited score."""
    if status == "not_discussed":
        return "Not discussed in interview"
    text = (remarks or "").strip()
    if not text:
        return "No substantive evidence was recorded for this skill."
    if _NUMBER_IN_REMARKS.search(text):
        report.note(f"{skill_name}: numeric score removed from the remarks")
        text = _NUMBER_IN_REMARKS.sub("", text)
        text = " ".join(text.split())
    return text


# --------------------------------------------------------------------------- #
#  Per-skill assessment
# --------------------------------------------------------------------------- #
def build_payload(
    skill: SkillSpec,
    evidence: list[EvidenceItem],
    level: str,
    depth_reached: DepthStage,
    status: str,
) -> str:
    context = json.dumps(
        {
            "skill": {
                "name": skill.name,
                "description": skill.description,
                "assessment_scope": skill.assessment_scope,
                "expected_proficiency_0_to_4": skill.proficiency_target,
            },
            "expected_experience_level": level,
            "what_that_level_expects": EXPECTATIONS.get(level, ""),
            "discussion_status": status,
            # Told to the model as context, never as a score. How far the
            # interview probed says nothing about how well the candidate did.
            #
            # The key is spelled to be unmistakable and carries its own warning,
            # because a benchmark run showed the judge echoing this value back as
            # `depth_demonstrated` in 36 of 37 cases — the two figures collapsed
            # into one, which is the failure the whole design exists to prevent.
            "how_far_the_interview_probed": depth_reached,
            "how_far_the_interview_probed_IS_NOT_THE_ANSWER": (
                "Background only. Do not copy this into depth_demonstrated."
            ),
            "what_that_stage_investigates": STAGE_PURPOSE.get(depth_reached, ""),
        },
        ensure_ascii=False, indent=2,
    )
    items = "\n".join(
        f"- [{e.depth_stage}/{e.depth_dimension}/{e.evidence_type}/{e.evidence_strength}] "
        f"supports {e.supports_criterion}: {untrusted.fence(e.candidate_quote)}"
        + (f"\n  note: {e.note}" if e.note else "")
        for e in evidence
    ) or "(no evidence was recorded for this skill)"
    return (
        f"ASSESSMENT CONTEXT (trusted):\n{context}\n\n"
        f"{untrusted.PREAMBLE}\n\n"
        f"EVIDENCE:\n{items}\n"
    )


def assess_skill(
    skill: SkillSpec,
    evidence: list[EvidenceItem],
    transcript: InterviewTranscript,
    level: str,
    report: EvaluationReport,
    *,
    session_id: str = "_evaluation",
    judge: Callable[..., dict[str, Any]] | None = None,
) -> SkillAssessment:
    """One row. Status, depth and confidence are computed; the criteria are judged."""
    status = discussion_status_for(skill, transcript, evidence)
    depth_reached = transcript.depth_reached_for(skill.id)
    questions_asked = len([q for q in transcript.for_skill(skill.id) if q.answered])

    if status == "not_discussed":
        # No model call. There is nothing to read, and asking would invite an
        # invented judgement about a skill nobody tested.
        return SkillAssessment(
            skill_id=skill.id,
            skill_name=skill.name,
            discussion_status="not_discussed",
            score=0,
            remarks="Not discussed in interview",
            depth_evaluation=DepthEvaluation(
                depth_reached="direct",
                depth_demonstrated="direct",
                dimensions_demonstrated=[],
                dimensions_missing=list(DIMENSIONS),
                evidence_confidence="insufficient",
            ),
        )

    raw: dict[str, Any] = {}
    payload = build_payload(skill, evidence, level, depth_reached, status)
    try:
        caller = judge or _judge
        raw = caller(payload, session_id=session_id)
    except AIError as exc:
        report.failures.append(f"{skill.name}: {str(exc)[:160]}")
        raw = {}
    else:
        # One absent criterion is a judgement with a gap; four absent is not a
        # judgement. The line is where a row would stop resting on the model's
        # assessment and start resting on defaults.
        present = sum(1 for criterion in CRITERIA if criterion in raw)
        if present < len(CRITERIA) - 1:
            report.failures.append(
                f"{skill.name}: the judgement returned {present} of {len(CRITERIA)} "
                f"criteria, which is too few to constrain into a score"
            )

    scores = apply_constraints(raw, status, skill.name, report)

    # Demonstrated depth is computed, not negotiated. The model has no input
    # here: the dimensions its extraction assigned, and the strength it gave
    # each item, are its entire contribution — both already applied per item,
    # against the quote, by `depth_demonstrated_from`.
    demonstrated = depth_demonstrated_from(evidence)
    if raw.get("depth_demonstrated"):
        # Not part of the contract any more. Recorded if a model volunteers it,
        # so a prompt drifting back toward asking for it is visible.
        report.note(
            f"{skill.name}: the judge returned an unsolicited depth figure "
            f"({raw['depth_demonstrated']!r}); it was ignored"
        )

    shown, missing = dimensions_split(evidence)
    confidence = confidence_from(evidence, status, questions_asked)

    return SkillAssessment(
        skill_id=skill.id,
        skill_name=skill.name,
        discussion_status=status,
        score=sum(scores.values()),
        remarks=clean_remarks(raw.get("remarks", ""), status, skill.name, report),
        accuracy=scores["Accuracy"],
        depth=scores["Depth"],
        clarity=scores["Clarity"],
        problem_solving=scores["Problem-Solving"],
        communication=scores["Communication"],
        depth_evaluation=DepthEvaluation(
            depth_reached=depth_reached,
            depth_demonstrated=demonstrated,
            dimensions_demonstrated=shown,
            dimensions_missing=missing,
            evidence_confidence=confidence,
        ),
        evidence=list(evidence),
    )


def _judge(payload: str, *, session_id: str) -> dict[str, Any]:
    result = get_gateway().generate_structured(
        Workload.SCORING, SYSTEM, payload, SKILL_ASSESSMENT,
        schema_name="skill_assessment",
        # Ask for all five; refuse only over what makes a response a judgement
        # at all. A dropped criterion is handled and recorded by
        # `apply_constraints`, not paid for with the whole evaluation.
        accept_schema=SKILL_ASSESSMENT_ACCEPT,
        session_id=session_id,
    )
    return result.data or {}


# --------------------------------------------------------------------------- #
#  Recommendation — rules, in order
# --------------------------------------------------------------------------- #
#: A discussed skill at or above this on every criterion is meeting the bar.
BAR = 3

#: A criterion at or below this, on a skill that was substantively discussed, is
#: severe enough to hold a candidate back from "Proceed" whatever the total says.
#: Named rather than inlined because the pilot has to be able to report which
#: skills are sitting on it — see `result.recommendation_boundary`.
SEVERE_AT = 1


def recommend(rows: list[SkillAssessment]) -> tuple[str, str]:
    """The recommendation and its explanation, from the contract's rules.

    Undiscussed skills are coverage gaps, never weaknesses: a skill nobody
    asked about says nothing about the candidate, and treating silence as
    failure would penalise them for how the interview was run.
    """
    if not rows:
        return RECOMMENDATIONS[1], (
            "No skills were assessed, so there is nothing to base a decision on."
        )

    discussed = [r for r in rows if r.discussion_status == "discussed"]
    not_discussed = [r for r in rows if r.discussion_status == "not_discussed"]

    if not discussed:
        return RECOMMENDATIONS[1], (
            "None of the skills for this role were substantively discussed, so the "
            "interview did not establish what the candidate can do. This is a gap in "
            "the conversation rather than a finding about the candidate."
        )

    # The coverage gate. It counts every skill WITHOUT substantive evidence, not
    # only the ones never asked about — because a skill that was asked and
    # answered in six words established no more than one nobody raised, and
    # `Coverage` already treats both as uncovered.
    #
    # It used to count `not_discussed` alone, which produced a contradiction
    # visible on a real interview: a candidate with one skill of six assessed
    # (16.7% coverage) was declared "Not suitable for this role" because the
    # other five were `mentioned` rather than `not_discussed`, while an
    # otherwise identical candidate whose five gaps were never asked got
    # "Needs further evaluation". Two candidates, equally unassessed, opposite
    # verdicts. A confident negative from one skill is the same error as the
    # coverage-inflated denominator this phase removed, pointing the other way.
    #
    # Every canonical case is unchanged: the four that specify a majority of
    # DISCUSSED skills cannot reach this branch, and the two coverage cases
    # already fired here.
    uncovered = [r for r in rows if r.discussion_status != "discussed"]
    if len(uncovered) > len(rows) / 2:
        covered = ", ".join(r.skill_name for r in discussed[:3])
        return RECOMMENDATIONS[1], (
            f"Most of the skills for this role were not substantively discussed, so the "
            f"interview covered too little to decide on. What was covered ({covered}) is "
            f"reported above, but the gaps are in the coverage rather than in the "
            f"candidate."
        )

    # Only discussed skills speak to capability.
    def meets_bar(row: SkillAssessment) -> bool:
        return min(row.criteria().values()) >= BAR or (
            sum(row.criteria().values()) / len(CRITERIA) >= BAR
        )

    at_or_above = [r for r in discussed if meets_bar(r)]
    any_severe = any(min(r.criteria().values()) <= SEVERE_AT for r in discussed)
    below = [r for r in discussed if not meets_bar(r)]

    if len(at_or_above) > len(discussed) / 2 and not any_severe:
        strengths = ", ".join(r.skill_name for r in at_or_above[:3])
        return RECOMMENDATIONS[2], (
            f"The candidate met or exceeded what the role expects on most of the skills "
            f"that were discussed, including {strengths}, with evidence in their own "
            f"words rather than assertions. Worth taking to the next conversation."
        )

    if len(below) > len(discussed) / 2:
        weak = ", ".join(r.skill_name for r in below[:3])
        return RECOMMENDATIONS[0], (
            f"On most of the skills that were discussed — {weak} — the answers stayed "
            f"below what this role expects, without the reasoning or worked examples "
            f"the level calls for."
        )

    mixed_strong = ", ".join(r.skill_name for r in at_or_above[:2]) or "some areas"
    mixed_weak = ", ".join(r.skill_name for r in below[:2]) or "others"
    return RECOMMENDATIONS[1], (
        f"The picture is uneven: the candidate showed what the role expects on "
        f"{mixed_strong}, but the evidence on {mixed_weak} did not reach the same bar. "
        f"A further conversation focused on the weaker areas would settle it."
    )


#: One skill, reduced to what a boundary check needs: how it was discussed, its
#: five criteria, and its name. Both the engine's own rows and the persisted wire
#: rows can produce this shape, so there is one implementation rather than two.
SkillView = tuple[str, dict[str, int], str]


def boundary_of(views: list[SkillView]) -> dict[str, Any]:
    """Is this recommendation one point away from being a different one?

    Measured, not guessed. Five forced re-evaluations of one frozen snapshot
    produced 74.7 / 76.0 / 74.7 / 76.7 / 76.7 % — a two-point spread and the same
    rating every time — and two different recommendations. What moved was a
    single criterion: `Communication clarity · Depth` sat at 2 in two runs and 1
    in three, and `any_severe` is a step at 1. Ten criteria moved by a point
    across those five runs; only that one changed the verdict.

    So the rule is left exactly as it is, and the fact is disclosed instead.
    This walks `recommend`'s branches in the same order, because a threshold
    only matters when it is the one the recommendation actually turned on: a
    candidate whose skills are mostly below the bar is not sensitive to
    `any_severe` at all, and flagging them would make the warning noise.

    Smoothing it away — requiring two severe criteria, widening the bar,
    averaging the minimum — would change who gets recommended, on evidence from
    one interview. Scoring changes go through the benchmark and a regression
    run, not through a pilot observation.
    """
    empty = {
        "at_boundary": False, "reasons": [], "skills": [],
        "skills_meeting_bar": 0, "skills_discussed": 0,
    }
    if not views:
        return empty

    discussed = [v for v in views if v[0] == "discussed"]
    uncovered = len(views) - len(discussed)
    if not discussed:
        return empty

    reasons: list[str] = []

    # Branch 1 in `recommend`: the coverage gate. While it is firing, no score
    # can change the answer — but one more skill being substantively discussed
    # could, and that is its own sensitivity.
    if uncovered > len(views) / 2:
        if uncovered - 1 <= len(views) / 2:
            reasons.append(
                f"{uncovered} of {len(views)} skills were not substantively discussed, "
                f"one away from the coverage gate that produced this recommendation"
            )
        return {
            "at_boundary": bool(reasons), "reasons": reasons, "skills": [],
            "skills_meeting_bar": 0, "skills_discussed": len(discussed),
        }

    def meets_bar(criteria: dict[str, int]) -> bool:
        values = list(criteria.values())
        return min(values) >= BAR or sum(values) / len(values) >= BAR

    at_or_above = [v for v in discussed if meets_bar(v[1])]
    below = [v for v in discussed if not meets_bar(v[1])]
    majority = len(discussed) / 2

    # `any_severe` gates the "Proceed" branch and nothing else, so it is a live
    # threshold only when the majority test passes.
    on_the_line: list[str] = []
    if len(at_or_above) > majority:
        on_the_line = [
            name for _, criteria, name in discussed
            if min(criteria.values()) in (SEVERE_AT, SEVERE_AT + 1)
        ]
    if on_the_line:
        reasons.append(
            "a criterion on " + ", ".join(on_the_line) + " is at the severe threshold; "
            "one point either way changes the recommendation without changing the "
            "score materially"
        )
    if abs(len(at_or_above) - majority) <= 1 or abs(len(below) - majority) <= 1:
        reasons.append(
            f"{len(at_or_above)} of {len(discussed)} discussed skills meet the bar, "
            f"within one skill of the majority the recommendation turns on"
        )
    return {
        "at_boundary": bool(reasons),
        "reasons": reasons,
        "skills": on_the_line,
        "skills_meeting_bar": len(at_or_above),
        "skills_discussed": len(discussed),
    }


def boundary_for(rows: list[SkillAssessment]) -> dict[str, Any]:
    """`boundary_of` over the engine's own rows."""
    return boundary_of([(r.discussion_status, r.criteria(), r.skill_name) for r in rows])


# --------------------------------------------------------------------------- #
#  Aggregation
# --------------------------------------------------------------------------- #
def aggregate(
    rows: list[SkillAssessment],
    *,
    candidate_name: str,
    job_role: str,
    level: str,
    session_id: str = "",
    interview_id: str = "",
    interview_version: int = 0,
) -> Evaluation:
    """Totals, rating and recommendation — all computed, none asked for.

    **The score is over DISCUSSED skills only**, and that is the one thing in
    this function worth explaining at length.

    The denominator used to be every skill the published version listed. It made
    a candidate who answered one skill superbly read as 21/100 — `Poor` — because
    three questions nobody asked were counted against them. Meanwhile
    `recommend` below correctly called the same interview a coverage gap "rather
    than a finding about the candidate". Two halves of one contract, disagreeing.

    So: a skill that was substantively evaluated contributes its score and its 25
    points. A skill that was merely mentioned, or never asked about, contributes
    NEITHER — it is reported as `Coverage` instead. The score answers "how well
    did they do on what we asked"; coverage answers "how much did we ask". They
    are two numbers because they are two questions, and coverage is never added
    to, averaged with, or scaled into the score.

    With nothing discussed there is no denominator, so there is no rating — see
    `UNRATED`. Reporting `Poor` for an interview that established nothing would
    be the original mistake in its purest form.
    """
    assessed = [r for r in rows if r.discussion_status == "discussed"]
    total = sum(r.score for r in assessed)
    maximum = 5 * len(CRITERIA) * len(assessed)
    percentage = (total / maximum * 100) if maximum else 0.0
    coverage = Coverage.of([r.discussion_status for r in rows])

    recommendation, explanation = recommend(rows)

    strengths: list[str] = []
    improvements: list[str] = []
    for row in rows:
        if row.discussion_status != "discussed":
            continue
        average = sum(row.criteria().values()) / len(CRITERIA)
        if average >= 4:
            strengths.append(
                f"{row.skill_name}: {row.remarks.split('.')[0].strip()}."
            )
        elif average < BAR:
            # The measured number leads, then the remark. Left as remark-only,
            # a below-bar skill whose remark opened on something the candidate
            # DID do well read as praise under an "areas for improvement"
            # heading — observed on a real run: "Communication clarity: the
            # candidate used a clear and relatable analogy". The score is the
            # reason this line is in this list, so it says so.
            improvements.append(
                f"{row.skill_name}: scored {row.score}/{5 * len(CRITERIA)} — below what "
                f"this role expects. {row.remarks.split('.')[0].strip()}."
            )
    # Coverage gaps are NOT improvement areas. They used to be appended here
    # with a disclaimer attached, which put "the interview did not ask about
    # Kubernetes" in the same list as things the candidate did poorly — and a
    # list a recruiter skims is a list where the disclaimer is the first thing
    # lost. They are reported as `Coverage`, which is what they are.

    return Evaluation(
        candidate_details=CandidateDetails(
            name=candidate_name,
            job_role=job_role,
            experience_level=level,
            total_score=total,
            overall_rating=overall_rating(percentage) if assessed else UNRATED,
        ),
        skill_assessment=rows,
        strengths=strengths,
        areas_for_improvement=improvements,
        recommendation=recommendation,
        recommendation_explaination=explanation,
        session_id=session_id,
        interview_id=interview_id,
        interview_version=interview_version,
        maximum_possible_score=maximum,
        percentage=round(percentage, 1),
        coverage=coverage,
    )


def evaluate(
    transcript: InterviewTranscript,
    definition: InterviewDefinition,
    evidence: list[EvidenceItem],
    *,
    session_id: str = "_evaluation",
    judge: Callable[..., dict[str, Any]] | None = None,
) -> tuple[Evaluation, EvaluationReport]:
    """The whole evaluation, for every skill the published version lists."""
    report = EvaluationReport()
    level = experience_level(definition.experience_to)
    by_skill: dict[str, list[EvidenceItem]] = {}
    for item in evidence:
        by_skill.setdefault(item.skill_id, []).append(item)

    rows = [
        assess_skill(
            skill, by_skill.get(skill.id, []), transcript, level, report,
            session_id=session_id, judge=judge,
        )
        for skill in definition.skills
    ]

    evaluation = aggregate(
        rows,
        candidate_name=transcript.candidate_name,
        job_role=definition.role_title,
        level=level,
        session_id=transcript.session_id,
        interview_id=transcript.interview_id,
        interview_version=transcript.interview_version,
    )
    return evaluation, report
