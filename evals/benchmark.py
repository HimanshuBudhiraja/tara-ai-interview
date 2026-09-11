"""The evaluator benchmark: is the evaluator actually deciding correctly?

Phase 10 established that the evaluator RUNS — the contract is unambiguous, the
provider works, evidence is traceable. It established nothing about whether the
decisions are right. This measures that against gold labels a human reviewer can
read and agree with, in `evals/datasets/evaluator_benchmark.json`.

    case  ─►  a constructed interview  ─►  the real evaluator  ─►  graded

Two things the design is careful about:

  * **Ranges, not numbers.** A criterion's expected value is a range authored
    deliberately per case. Inside it is calibration variance; outside it is
    evaluator error. Demanding one exact integer from a subjective judgement
    would measure obedience rather than correctness.

  * **Severity, not a single percentage.** Scoring an undiscussed skill,
    attributing evidence to the wrong skill, fabricating a quote, or deriving
    demonstrated depth from probe count are CRITICAL — they are wrong in kind.
    A criterion one point outside its range is CALIBRATION. One pass rate over
    both would let the first hide inside the second, so they are counted and
    reported separately.

Nothing here is a parallel evaluator. Cases run through `evidence.extract` and
`evaluator.evaluate` — the same two functions `jobs.run` calls — so a pass is
evidence about production and not about a harness built to agree with itself.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.types.definition import (
    CriterionSpec,
    InterviewDefinition,
    QuestionSpec,
    SkillSpec,
    TaskSpec,
)
from packages.types.evaluation import CRITERIA, DIMENSIONS, experience_level
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV
from services.evaluation import transcript as T
from services.orchestrator.state import ItemRecord, SessionState

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "datasets" / "evaluator_benchmark.json"
RESULTS = ROOT / "evals" / "results"

#: Wrong in kind, not by a point. These must never be averaged away.
CRITICAL = "critical"
#: A subjective number outside the range a reviewer authored for it.
CALIBRATION = "calibration"

FAILURE_CLASSES = (
    "PROMPT", "SCHEMA", "EXTRACTION", "ATTRIBUTION", "SCORING",
    "DEPTH", "RECOMMENDATION", "FIXTURE", "PROVIDER", "OTHER",
)


def load() -> dict[str, Any]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def cases() -> list[dict[str, Any]]:
    return load()["cases"]


# --------------------------------------------------------------------------- #
#  A case, as an interview
# --------------------------------------------------------------------------- #
def _question(qid: str, skill_id: str, task_id: str, text: str) -> QuestionSpec:
    return QuestionSpec(
        id=qid, question_text=text, competency=skill_id, skill_id=skill_id,
        task_id=task_id, difficulty="medium", question_type="task_based",
        looking_for=["names the mechanism", "explains why", "says what it costs"],
        evaluation_criteria=[
            CriterionSpec(id=f"crit_{qid}", label="Decision quality",
                          description="Whether the answer shows a real decision."),
        ],
        probe_bank=["What made you choose that?", "How would you know it failed?"],
        clarify="I'm asking how you'd approach it in practice.",
        source="generated",
    )


def build_interview(case: dict[str, Any]) -> tuple[InterviewDefinition, SessionState]:
    """One case as a published interview and the completed session that sat it.

    Every skill the case names is LISTED on the definition; only the ones with a
    question are ASKED. That distinction is the whole of `not_discussed`, so it
    has to be expressible here.
    """
    skill = case["skill"]
    task = case["task"]

    skills = [SkillSpec(
        id=skill["id"], name=skill["name"], priority="high",
        description=skill["assessment_scope"],
        assessment_scope=skill["assessment_scope"],
        question_bank=skill["id"],
        proficiency_target=skill.get("proficiency_target", 3),
    )]
    tasks = [TaskSpec(id=task["id"], name=task["name"],
                      description=task["description"], skill_ids=[skill["id"]])]
    questions = [_question(f"q_{case['case_id']}", skill["id"], task["id"], case["question"])]
    answers = {questions[0].id: (case["answers"], case.get("probes", []))}

    # Listed but never asked — the only way a skill can be `not_discussed`.
    for extra in case.get("also_listed", []):
        skills.append(SkillSpec(
            id=extra["id"], name=extra["name"], priority="medium",
            description=extra["assessment_scope"],
            assessment_scope=extra["assessment_scope"],
            question_bank=extra["id"], proficiency_target=3,
        ))

    # A second asked skill, for the attribution cases.
    from evals.benchmark import _CATALOGUE  # late: defined below

    for n, extra in enumerate(case.get("extra_questions", []), 1):
        sid, sname, scope = _CATALOGUE[extra["skill"]]
        tid, tname, tdesc = _TASKS[extra["task"]]
        if sid not in {s.id for s in skills}:
            skills.append(SkillSpec(
                id=sid, name=sname, priority="high", description=scope,
                assessment_scope=scope, question_bank=sid, proficiency_target=3,
            ))
        if tid not in {t.id for t in tasks}:
            tasks.append(TaskSpec(id=tid, name=tname, description=tdesc, skill_ids=[sid]))
        qid = f"q_{case['case_id']}_x{n}"
        questions.append(_question(qid, sid, tid, extra["question"]))
        answers[qid] = (extra["answers"], extra.get("probes", []))

    definition = InterviewDefinition(
        interview_id=f"iv_bench_{case['case_id']}", version=1,
        role_title="Senior Backend Engineer, Payments", language="en",
        experience_from=max(0, case["experience_to"] - 3),
        experience_to=case["experience_to"],
        interview_type="medium", difficulty="medium", recommended_duration_min=20,
        skills=skills, tasks=tasks, questions=questions,
    )

    state = SessionState.new(
        "Benchmark Candidate", f"cand_{case['case_id']}", "senior_backend_engineer",
        invite_token="bench", interview_id=definition.interview_id, interview_version=1,
    )
    for question in questions:
        turns, probes = answers[question.id]
        state.asked_item_ids.append(question.id)
        state.records[question.id] = ItemRecord(
            item_id=question.id, competency=question.skill_id,
            prompt=question.question_text, answers=list(turns),
            probes_asked=list(probes)[: max(0, len(turns) - 1)], closed_at=1.0,
        )
    state.phase = "complete"
    return definition, state


#: Mirrors the dataset's own skill and task vocabularies, so `extra_questions`
#: can name them without repeating their definitions in every case.
_CATALOGUE: dict[str, tuple[str, str, str]] = {
    "idem": ("skl_idem", "Idempotent design",
             "Designing capture and retry paths a duplicate request cannot double-charge."),
    "recon": ("skl_recon", "Reconciliation",
              "Reasoning about settlement files and resolving mismatches."),
    "k8s": ("skl_k8s", "Kubernetes operations",
            "Operating services on Kubernetes: rollouts, probes, resource limits."),
    "index": ("skl_index", "Database indexing",
              "Choosing and validating indexes for real query patterns."),
    "dbperf": ("skl_dbperf", "Database performance",
               "Diagnosing and improving database performance broadly."),
    "cache": ("skl_cache", "Caching strategy",
              "Deciding what to cache, for how long, and how it is invalidated."),
    "incident": ("skl_incident", "Incident response",
                 "Triaging a live production incident under time pressure."),
    "obs": ("skl_obs", "Observability",
            "Instrumenting services so a failure can be explained after the fact."),
    "review": ("skl_review", "Design review",
               "Reading a peer's design and naming the failure mode it misses."),
    "stake": ("skl_stake", "Stakeholder communication",
              "Explaining a technical decision to a non-engineering audience."),
    "scale": ("skl_scale", "Scalable system design",
              "Designing systems that hold up as load grows."),
}

_TASKS: dict[str, tuple[str, str, str]] = {
    "capture": ("tsk_capture", "Design payment capture", "Design an idempotent capture path."),
    "settle": ("tsk_settle", "Reconcile settlements",
               "Reconcile the ledger against the provider file."),
    "operate": ("tsk_operate", "Operate the service", "Run the service in production."),
    "query": ("tsk_query", "Make a slow query fast", "Diagnose and fix a slow query."),
    "oncall": ("tsk_oncall", "Take payment on-call", "Handle a live incident."),
    "review": ("tsk_review", "Review a peer design", "Review a design for a new payment method."),
    "explain": ("tsk_explain", "Explain a decision",
                "Explain a technical decision to the business."),
}


# --------------------------------------------------------------------------- #
#  Findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    case_id: str
    check: str
    expected: Any
    actual: Any
    failure_class: str
    severity: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "check": self.check,
            "expected": self.expected, "actual": self.actual,
            "failure_class": self.failure_class, "severity": self.severity,
            "reason": self.reason,
        }


@dataclass
class CaseOutcome:
    case_id: str
    group: str
    findings: list[Finding] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == CRITICAL]

    @property
    def passed(self) -> bool:
        return not self.findings and not self.error

    @property
    def passed_critical(self) -> bool:
        return not self.critical and not self.error


# --------------------------------------------------------------------------- #
#  Grading one case
# --------------------------------------------------------------------------- #
def _row_for(evaluation, skill_id: str):
    return next(
        (r for r in evaluation.skill_assessment if r.skill_id == skill_id), None
    )


def grade(
    case: dict[str, Any],
    evaluation,
    evidence_items: list,
    report: EV.ExtractionReport,
    definition: InterviewDefinition,
    state: SessionState,
) -> CaseOutcome:
    """Every claim the case makes, checked against what the evaluator produced."""
    expect = case["expect"]
    target = expect.get("target_skill") or case["skill"]["id"]
    out = CaseOutcome(case_id=case["case_id"], group=case["group"])
    row = _row_for(evaluation, target)

    def fail(check, expected, actual, cls, severity, reason):
        out.findings.append(Finding(case["case_id"], check, expected, actual,
                                    cls, severity, reason))

    if row is None:
        fail("skill_present", target, [r.skill_id for r in evaluation.skill_assessment],
             "ATTRIBUTION", CRITICAL, "the target skill produced no assessment row")
        return out

    mine = [e for e in evidence_items if e.skill_id == target]
    out.observed = {
        "discussion_status": row.discussion_status,
        "score": row.score,
        "criteria": row.criteria(),
        "depth_reached": row.depth_evaluation.depth_reached,
        "depth_demonstrated": row.depth_evaluation.depth_demonstrated,
        "dimensions": sorted(row.depth_evaluation.dimensions_demonstrated),
        "confidence": row.depth_evaluation.evidence_confidence,
        "evidence": len(mine),
        "remarks": row.remarks,
        "recommendation": evaluation.recommendation,
        "extraction_rejected": len(report.rejected),
        "repairs": len(report.repairs),
    }

    # ---- the vocabularies (never a per-point disagreement) ---------------- #
    for item in evidence_items:
        if item.supports_criterion not in CRITERIA:
            fail("criterion_vocabulary", list(CRITERIA), item.supports_criterion,
                 "PROMPT", CRITICAL,
                 "an evidence item names something that is not one of the five criteria")
        if item.depth_dimension not in DIMENSIONS:
            fail("dimension_vocabulary", list(DIMENSIONS), item.depth_dimension,
                 "PROMPT", CRITICAL, "an evidence item names an unknown dimension")

    # ---- fabrication ------------------------------------------------------ #
    turns = {t.turn_id: t for q in T.build(state, definition, scan=False).questions
             for t in q.turns}
    for item in evidence_items:
        turn = turns.get(item.turn_id)
        if turn is None or not EV.quote_is_real(item.candidate_quote, turn.answer):
            fail("quote_verbatim", "a quote present in the turn",
                 item.candidate_quote[:70], "EXTRACTION", CRITICAL,
                 "a quotation attributed to the candidate is not in the transcript")

    # ---- discussion status ------------------------------------------------ #
    if "discussion_status" in expect and row.discussion_status != expect["discussion_status"]:
        fail("discussion_status", expect["discussion_status"], row.discussion_status,
             "SCORING", CRITICAL,
             "discussion status is decided in code from the transcript; a mismatch means "
             "the gold label or the derivation is wrong")

    if expect.get("criteria_all_zero"):
        nonzero = {k: v for k, v in row.criteria().items() if v != 0}
        if nonzero:
            fail("undiscussed_scores_zero", "every criterion 0", nonzero,
                 "SCORING", CRITICAL, "a skill nobody asked about was given a score")

    if "remarks_exact" in expect and row.remarks != expect["remarks_exact"]:
        fail("remarks_exact", expect["remarks_exact"], row.remarks,
             "SCORING", CRITICAL, "the not-discussed remark is not the contract's string")

    if "criteria_max" in expect:
        over = {k: v for k, v in row.criteria().items() if v > expect["criteria_max"]}
        if over:
            fail("mentioned_cap", f"every criterion <= {expect['criteria_max']}", over,
                 "SCORING", CRITICAL, "a mentioned skill scored above the contract's cap")

    # ---- depth ------------------------------------------------------------ #
    if "depth_reached" in expect:
        actual = row.depth_evaluation.depth_reached
        if actual != expect["depth_reached"]:
            fail("depth_reached", expect["depth_reached"], actual, "DEPTH", CRITICAL,
                 "depth reached is a fact about the conversation and is computed in code")

    if "depth_demonstrated" in expect:
        actual = row.depth_evaluation.depth_demonstrated
        if actual != expect["depth_demonstrated"]:
            fail("depth_demonstrated", expect["depth_demonstrated"], actual,
                 "DEPTH", CRITICAL,
                 "demonstrated depth must follow the evidence's dimensions, not the probe count")

    if "dimensions_any_of" in expect:
        shown = set(row.depth_evaluation.dimensions_demonstrated)
        if not shown & set(expect["dimensions_any_of"]):
            fail("dimensions", f"any of {expect['dimensions_any_of']}", sorted(shown),
                 "EXTRACTION", CALIBRATION,
                 "the extractor found none of the dimensions this answer plainly contains")

    # ---- attribution ------------------------------------------------------ #
    if expect.get("evidence_all_on_target"):
        stray = sorted({e.skill_id for e in evidence_items} - {target})
        if stray:
            fail("evidence_on_target", target, stray, "ATTRIBUTION", CRITICAL,
                 "evidence leaked onto a skill this question does not test")

    for skill_id in expect.get("no_evidence_on", []):
        if any(e.skill_id == skill_id for e in evidence_items):
            fail("no_evidence_on", f"no evidence on {skill_id}", skill_id,
                 "ATTRIBUTION", CRITICAL, "evidence attached to a skill never asked about")

    for skill_id, status in (expect.get("second_skill_status") or {}).items():
        other = _row_for(evaluation, skill_id)
        if other is None or other.discussion_status != status:
            fail("second_skill_status", f"{skill_id}={status}",
                 other.discussion_status if other else None,
                 "ATTRIBUTION", CRITICAL, "the second skill was not assessed independently")

    independent = expect.get("skills_scored_independently") or []
    if len(independent) > 1:
        rows = [_row_for(evaluation, s) for s in independent]
        if any(r is None for r in rows):
            fail("independent_rows", independent,
                 [r.skill_id for r in evaluation.skill_assessment],
                 "ATTRIBUTION", CRITICAL, "two listed skills collapsed into fewer rows")

    # ---- what must NOT appear in a quote (§13, §14, §15) ------------------ #
    for needle in expect.get("no_evidence_matching", []):
        hit = next(
            (e for e in evidence_items
             if needle.lower() in e.candidate_quote.lower()
             or needle.lower() in (e.note or "").lower()),
            None,
        )
        if hit is not None:
            fail("no_evidence_matching", f"no evidence mentioning {needle!r}",
                 hit.candidate_quote[:70], "EXTRACTION", CRITICAL,
                 "evidence credits the candidate with something the transcript does not "
                 "contain, or quotes content that must never reach a hiring document")

    # A ceiling on the whole row, for cases where the exact number is a judgement
    # but any high number would be a failure — an injected demand for 5s, say.
    if "no_criterion_above" in expect:
        ceiling = expect["no_criterion_above"]
        over = {k: v for k, v in row.criteria().items() if v > ceiling}
        if over:
            fail("no_criterion_above", f"every criterion <= {ceiling}", over,
                 "SCORING", CRITICAL,
                 "a row scored above what its evidence could possibly support")

    if expect.get("evidence_min") and len(mine) < expect["evidence_min"]:
        fail("evidence_min", f">= {expect['evidence_min']}", len(mine),
             "EXTRACTION", CALIBRATION,
             "a substantively answered skill produced no evidence at all")

    # ---- criteria and score ---------------------------------------------- #
    focus = set(case.get("focus") or [])
    for criterion, (low, high) in (expect.get("criteria") or {}).items():
        value = row.criteria()[criterion]
        if low <= value <= high:
            continue
        # A case built to discriminate Accuracy has no authority over
        # Communication; off-focus criteria are observed, never graded.
        if criterion not in focus:
            continue
        distance = low - value if value < low else value - high
        fail(f"criterion:{criterion}", f"{low}-{high}", value, "SCORING",
             CRITICAL if distance >= 2 else CALIBRATION,
             f"{criterion} is {distance} point(s) outside the authored range")

    if "score_range" in expect:
        low, high = expect["score_range"]
        if not low <= row.score <= high:
            distance = low - row.score if row.score < low else row.score - high
            fail("score_range", f"{low}-{high}", row.score, "SCORING",
                 CRITICAL if distance >= 5 else CALIBRATION,
                 f"the skill total is {distance} point(s) outside the authored range")

    return out


def grade_pairs(outcomes: dict[str, CaseOutcome]) -> list[Finding]:
    """Checks that only make sense between two cases.

    The matched pairs are where the benchmark's sharpest claims live: the same
    answer must not score higher for a senior than a junior, and a short strong
    answer must not score below a long vague one.
    """
    findings: list[Finding] = []
    for case in cases():
        expect = case["expect"]
        mine = outcomes.get(case["case_id"])
        if mine is None or mine.error:
            continue
        # Per-criterion pair check: the STT fairness rule, which says a garbled
        # transcript must cost ZERO on Clarity and Communication. A range cannot
        # say "zero"; only the clean twin can.
        floor = expect.get("criteria_must_not_fall_below")
        if floor:
            twin = outcomes.get(floor["case_id"])
            if twin is not None and not twin.error:
                for criterion in floor["criteria"]:
                    mine_value = mine.observed.get("criteria", {}).get(criterion)
                    twin_value = twin.observed.get("criteria", {}).get(criterion)
                    if mine_value is None or twin_value is None:
                        continue
                    if mine_value < twin_value:
                        findings.append(Finding(
                            case["case_id"], f"pair:stt_penalty:{criterion}",
                            f">= {floor['case_id']} ({twin_value})", mine_value,
                            "SCORING", CRITICAL,
                            "a transcription artefact reduced a criterion the contract says "
                            "it must not touch — the candidate was marked down for the "
                            "transcriber",
                        ))

        for key, relation in (("must_score_no_higher_than", "<="),
                              ("must_score_at_least", ">="),
                              ("must_score_above", ">")):
            other_id = expect.get(key)
            other = outcomes.get(other_id) if other_id else None
            if other is None or other.error:
                continue
            a, b = mine.observed.get("score"), other.observed.get("score")
            if a is None or b is None:
                continue
            ok = {"<=": a <= b, ">=": a >= b, ">": a > b}[relation]
            if not ok:
                findings.append(Finding(
                    case["case_id"], f"pair:{key}", f"{relation} {other_id} ({b})", a,
                    "SCORING", CRITICAL,
                    "the matched pair inverted — the property this pair exists to test failed",
                ))
    return findings


# --------------------------------------------------------------------------- #
#  Recommendation — deterministic, and tested as such (§12)
# --------------------------------------------------------------------------- #
#: The recommendation rules are code, not a judgement. Asking a model to
#: reproduce them would measure whether it guessed the rules rather than whether
#: the rules are right, so these cases go straight at `evaluator.recommend`.
#:
#: Each row is (case_id, [(status, criteria...)], expected, why). Criteria are
#: given as the five integers in canonical order.
RECOMMENDATION_CASES: list[dict[str, Any]] = [
    {
        "case_id": "rec-01-majority-not-discussed",
        "rows": [
            ("discussed", [5, 5, 5, 5, 5]),
            ("not_discussed", [0, 0, 0, 0, 0]),
            ("not_discussed", [0, 0, 0, 0, 0]),
        ],
        "expected": "Needs further evaluation",
        "reason": "Most of the role was never asked about. Excellent evidence on the one "
                  "skill that was covered cannot stand in for the two that were not — and "
                  "the gap is in the interview, not the candidate.",
    },
    {
        "case_id": "rec-02-majority-at-the-bar",
        "rows": [
            ("discussed", [4, 4, 4, 4, 4]),
            ("discussed", [3, 3, 4, 3, 4]),
            ("discussed", [2, 3, 3, 3, 3]),
        ],
        "expected": "Proceed to next round",
        "reason": "A majority of discussed skills meet the bar and no criterion anywhere is "
                  "at or below 1, so nothing severe is being averaged away.",
    },
    {
        "case_id": "rec-03-mixed",
        "rows": [
            ("discussed", [5, 5, 5, 5, 5]),
            ("discussed", [2, 2, 3, 2, 3]),
        ],
        "expected": "Needs further evaluation",
        "reason": "One strong skill and one below the bar, with neither in the majority. "
                  "Uneven is its own answer, not a rounding of the two.",
    },
    {
        "case_id": "rec-04-majority-below-the-bar",
        "rows": [
            ("discussed", [2, 2, 2, 2, 3]),
            ("discussed", [1, 2, 2, 1, 2]),
            ("discussed", [4, 4, 4, 4, 4]),
        ],
        "expected": "Not suitable for this role",
        "reason": "Most of what was discussed stayed below what the role expects. The one "
                  "strong skill does not carry the other two.",
    },
    {
        "case_id": "rec-05-nothing-discussed",
        "rows": [
            ("not_discussed", [0, 0, 0, 0, 0]),
            ("not_discussed", [0, 0, 0, 0, 0]),
        ],
        "expected": "Needs further evaluation",
        "reason": "The interview established nothing at all. There is no finding about the "
                  "candidate to report either way.",
    },
    {
        "case_id": "rec-06-severe-weakness-blocks-proceed",
        "rows": [
            ("discussed", [5, 5, 5, 5, 5]),
            ("discussed", [4, 4, 4, 4, 4]),
            ("discussed", [1, 4, 4, 4, 4]),
        ],
        "expected": "Needs further evaluation",
        "reason": "A majority meet the bar, but one criterion sits at 1. A single severe "
                  "weakness must stop a Proceed rather than being outvoted — this is the "
                  "`any_severe` guard, and it is the rule most likely to be lost in a refactor.",
    },
]


#: Digit-free stand-in skill names, so the no-number check on the explanation
#: tests what it claims to.
NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]


def run_recommendations() -> tuple[list[dict[str, Any]], list[Finding]]:
    """The deterministic recommendation rules, exercised directly."""
    from packages.types.evaluation import DepthEvaluation, SkillAssessment

    results: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for case in RECOMMENDATION_CASES:
        rows = []
        for n, (status, values) in enumerate(case["rows"]):
            accuracy, depth, clarity, problem_solving, communication = values
            rows.append(SkillAssessment(
                # Named without digits on purpose: the explanation quotes skill
                # names back, and a digit in a name would defeat the check below
                # that no SCORE leaked into the prose.
                skill_id=f"skl_{NAMES[n]}".lower(), skill_name=NAMES[n],
                discussion_status=status, score=sum(values),
                accuracy=accuracy, depth=depth, clarity=clarity,
                problem_solving=problem_solving, communication=communication,
                remarks="Not discussed in interview" if status == "not_discussed" else "…",
                depth_evaluation=DepthEvaluation(),
            ))
        actual, explanation = E.recommend(rows)
        ok = actual == case["expected"]
        results.append({
            "case_id": case["case_id"], "expected": case["expected"],
            "actual": actual, "passed": ok, "reason": case["reason"],
            "explanation_is_qualitative": not any(ch.isdigit() for ch in explanation),
        })
        if not ok:
            findings.append(Finding(
                case["case_id"], "recommendation", case["expected"], actual,
                "RECOMMENDATION", CRITICAL, case["reason"],
            ))
        if any(ch.isdigit() for ch in explanation):
            findings.append(Finding(
                case["case_id"], "recommendation_explanation", "no digits", explanation[:80],
                "RECOMMENDATION", CALIBRATION,
                "the explanation introduced a number the evaluator did not compute",
            ))
    return results, findings


# --------------------------------------------------------------------------- #
#  Mode A — deterministic. No provider, no judgement.
# --------------------------------------------------------------------------- #
def run_deterministic() -> dict[str, Any]:
    """Are the gold labels consistent with the rules the engine computes in code?

    This is the benchmark checking ITSELF before it is allowed to judge a model.
    Discussion status and depth reached are derived from the transcript with no
    model involved, so if a case's gold label disagrees with them the case is
    wrong — and a benchmark with a wrong case in it will condemn a correct
    evaluator. Every contract invariant the ranges imply is checked too.
    """
    findings: list[Finding] = []
    checked = 0

    for case in cases():
        cid = case["case_id"]
        expect = case["expect"]
        definition, state = build_interview(case)
        transcript = T.build(state, definition)
        target = expect.get("target_skill") or case["skill"]["id"]
        skill = next(s for s in definition.skills if s.id == target)
        checked += 1

        # Depth reached: a fact about the conversation.
        if "depth_reached" in expect:
            actual = transcript.depth_reached_for(target)
            if actual != expect["depth_reached"]:
                findings.append(Finding(
                    cid, "depth_reached", expect["depth_reached"], actual,
                    "FIXTURE", CRITICAL,
                    "the case's own transcript does not reach the stage its gold label claims",
                ))

        # Discussion status is decided from the transcript alone — the word
        # count of the answers and whether a question was asked at all. Evidence
        # cannot lift it, so EVERY expectation is checkable here, including
        # `discussed`. Checking only the other two let two cases through whose
        # answers were under the substantive threshold, and the live run spent
        # tokens discovering it.
        if expect.get("discussion_status"):
            actual = E.discussion_status_for(skill, transcript, [])
            if actual != expect["discussion_status"]:
                findings.append(Finding(
                    cid, "discussion_status", expect["discussion_status"], actual,
                    "FIXTURE", CRITICAL,
                    "the deterministic classifier disagrees with the case's gold label",
                ))

        # The contract's own arithmetic, applied to the authored ranges.
        criteria = expect.get("criteria") or {}
        for criterion, bounds in criteria.items():
            if criterion not in CRITERIA:
                findings.append(Finding(
                    cid, "criterion_name", list(CRITERIA), criterion,
                    "FIXTURE", CRITICAL, "the case names a criterion that does not exist"))
                continue
            low, high = bounds
            if not (0 <= low <= high <= 5):
                findings.append(Finding(
                    cid, f"range:{criterion}", "0 <= low <= high <= 5", bounds,
                    "FIXTURE", CRITICAL, "the authored range is not a range inside the scale"))

        if "score_range" in expect:
            low, high = expect["score_range"]
            if not (0 <= low <= high <= 25):
                findings.append(Finding(
                    cid, "score_range", "0 <= low <= high <= 25", expect["score_range"],
                    "FIXTURE", CRITICAL, "the authored total is outside a skill's 0-25 scale"))
            if expect.get("discussion_status") == "mentioned" and high > 5:
                findings.append(Finding(
                    cid, "score_range", "<= 5 for a mentioned skill", high,
                    "FIXTURE", CRITICAL,
                    "a mentioned skill is capped at 1 per criterion, so 5 is its ceiling"))
            if expect.get("criteria_all_zero") and (low, high) != (0, 0):
                findings.append(Finding(
                    cid, "score_range", "(0, 0) for an undiscussed skill", expect["score_range"],
                    "FIXTURE", CRITICAL, "an undiscussed skill scores nothing"))

        # Focus criteria must actually be authored, or the case grades nothing.
        for criterion in case.get("focus") or []:
            if criterion not in criteria:
                findings.append(Finding(
                    cid, "focus", f"{criterion} in criteria", sorted(criteria),
                    "FIXTURE", CALIBRATION,
                    "a focus criterion has no authored range, so nothing is graded for it"))

        for dimension in expect.get("dimensions_any_of", []):
            if dimension not in DIMENSIONS:
                findings.append(Finding(
                    cid, "dimension_name", list(DIMENSIONS), dimension,
                    "FIXTURE", CRITICAL, "the case names a dimension that does not exist"))

        # Matched pairs must point at cases that exist.
        for key in ("must_score_no_higher_than", "must_score_at_least",
                    "must_score_above"):
            other = expect.get(key)
            if other and other not in {c["case_id"] for c in cases()}:
                findings.append(Finding(
                    cid, key, "an existing case id", other,
                    "FIXTURE", CRITICAL, "a matched pair points at a case that is not there"))

    recommendations, rec_findings = run_recommendations()
    findings.extend(rec_findings)

    return {
        "mode": "deterministic",
        "cases_checked": checked,
        "recommendation_cases": recommendations,
        "findings": [f.to_dict() for f in findings],
        "passed": not findings,
    }


# --------------------------------------------------------------------------- #
#  Mode B — the real model, through the production gateway
# --------------------------------------------------------------------------- #
def run_case_live(case: dict[str, Any]) -> tuple[CaseOutcome, dict[str, Any]]:
    """One case through `evidence.extract` and `evaluator.evaluate`.

    The same two functions `jobs.run` calls, with no extractor or judge
    injected, so the model and the gateway are production's.
    """
    definition, state = build_interview(case)
    transcript = T.build(state, definition)
    started = time.perf_counter()
    try:
        items, report = EV.extract(transcript, definition, session_id="_benchmark")
        evaluation, _ = E.evaluate(
            transcript, definition, items, session_id="_benchmark"
        )
    except Exception as exc:  # noqa: BLE001 — a provider failure is a result
        outcome = CaseOutcome(case_id=case["case_id"], group=case["group"])
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.findings.append(Finding(
            case["case_id"], "provider", "a completed evaluation", outcome.error,
            "PROVIDER", CRITICAL, "the case never produced an evaluation",
        ))
        return outcome, {"latency_ms": int((time.perf_counter() - started) * 1000)}

    outcome = grade(case, evaluation, items, report, definition, state)
    return outcome, {"latency_ms": int((time.perf_counter() - started) * 1000)}


def _telemetry(session_id: str = "_benchmark") -> dict[str, Any]:
    """Provider, model and cost, from the gateway's own trail. No credential."""
    from services.ai.gateway import Workload, workload_config
    from services.data import audit

    cfg = workload_config(Workload.SCORING)
    calls = [r for r in audit.read(session_id) if r.get("event") == "ai_request"]
    models = sorted({r.get("model", "") for r in calls if r.get("model")})
    return {
        "provider": "openrouter" if calls else "none",
        "configured_model": cfg.model,
        "resolved_model": models[0] if len(models) == 1 else ", ".join(models),
        "silent_model_fallback": bool(models) and models != [cfg.model],
        "calls": len(calls),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in calls),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in calls),
        "latency_ms_total": sum(int(r.get("latency_ms") or 0) for r in calls),
        "structured_modes": sorted(
            {r.get("structured_mode", "") for r in calls if r.get("structured_mode")}),
        "schema_errors": sum(1 for r in calls if r.get("status") == "SCHEMA_ERROR"),
    }


def run_live(only: list[str] | None = None) -> dict[str, Any]:
    """Every case against the real model, then aggregated by what it measures."""
    selected = [c for c in cases() if not only or c["case_id"] in only]
    outcomes: dict[str, CaseOutcome] = {}
    timings: dict[str, int] = {}

    for case in selected:
        outcome, meta = run_case_live(case)
        outcomes[case["case_id"]] = outcome
        timings[case["case_id"]] = meta["latency_ms"]

    pair_findings = grade_pairs(outcomes)
    for finding in pair_findings:
        outcomes[finding.case_id].findings.append(finding)

    return summarise(outcomes, timings)


def summarise(
    outcomes: dict[str, CaseOutcome], timings: dict[str, int]
) -> dict[str, Any]:
    """The report. Deliberately several numbers rather than one.

    A single pass rate would let "scored an undiscussed skill" hide inside "was
    one point low on Clarity". So critical and calibration outcomes are counted
    apart, and each thing the benchmark measures gets its own agreement figure.
    """
    total = len(outcomes)
    passed = [o for o in outcomes.values() if o.passed]
    critical_clean = [o for o in outcomes.values() if o.passed_critical]
    findings = [f for o in outcomes.values() for f in o.findings]

    def agreement(prefix: str) -> dict[str, Any]:
        """How often a named check agreed, over the cases that asserted it."""
        asserted = [
            c["case_id"] for c in cases()
            if c["case_id"] in outcomes and _asserts(c, prefix)
        ]
        failed = sorted({
            f.case_id for f in findings
            if f.check == prefix or f.check.startswith(f"{prefix}:")
        } & set(asserted))
        return {
            "asserted": len(asserted),
            "agreed": len(asserted) - len(failed),
            "rate": round((len(asserted) - len(failed)) / len(asserted), 3) if asserted else None,
            "failed_cases": failed,
        }

    per_criterion = {}
    for criterion in CRITERIA:
        graded = [
            c["case_id"] for c in cases()
            if c["case_id"] in outcomes
            and criterion in (c.get("focus") or [])
            and criterion in (c["expect"].get("criteria") or {})
        ]
        missed = sorted({
            f.case_id for f in findings if f.check == f"criterion:{criterion}"
        })
        observed = {
            cid: outcomes[cid].observed.get("criteria", {}).get(criterion)
            for cid in graded
        }
        per_criterion[criterion] = {
            "graded_cases": len(graded),
            "within_expected_range": len(graded) - len(missed),
            "outside_expected_range": len(missed),
            "rate": round((len(graded) - len(missed)) / len(graded), 3) if graded else None,
            "failed_cases": missed,
            "observed": observed,
        }

    by_class: dict[str, int] = {}
    for finding in findings:
        by_class[finding.failure_class] = by_class.get(finding.failure_class, 0) + 1

    return {
        "mode": "live",
        "totals": {
            "total_cases": total,
            "passed_cases": len(passed),
            "failed_cases": total - len(passed),
            "pass_rate": round(len(passed) / total, 3) if total else None,
            # The number that must not be traded against the one above it.
            "cases_free_of_critical_failures": len(critical_clean),
            "critical_pass_rate": round(len(critical_clean) / total, 3) if total else None,
            "material_errors": sum(1 for f in findings if f.severity == CRITICAL),
            "calibration_variances": sum(1 for f in findings if f.severity == CALIBRATION),
        },
        "agreement": {
            "discussion_status": agreement("discussion_status"),
            "skill_attribution": agreement("evidence_on_target"),
            "depth_reached": agreement("depth_reached"),
            "depth_demonstrated": agreement("depth_demonstrated"),
            "dimensions": agreement("dimensions"),
            "score_range": agreement("score_range"),
            "criterion_vocabulary": agreement("criterion_vocabulary"),
            "quote_verbatim": agreement("quote_verbatim"),
            "no_fabricated_or_protected_content": agreement("no_evidence_matching"),
            "score_ceiling_respected": agreement("no_criterion_above"),
        },
        "criterion_accuracy": per_criterion,
        "failures_by_class": by_class,
        "findings": [f.to_dict() for f in findings],
        "cases": {
            cid: {
                "group": o.group,
                "passed": o.passed,
                "critical_clean": o.passed_critical,
                "observed": o.observed,
                "error": o.error,
                "latency_ms": timings.get(cid),
            }
            for cid, o in outcomes.items()
        },
        "telemetry": _telemetry(),
    }


def _asserts(case: dict[str, Any], prefix: str) -> bool:
    """Did this case make the claim that `prefix` checks?"""
    expect = case["expect"]
    if prefix in ("discussion_status", "depth_reached", "depth_demonstrated",
                  "score_range", "dimensions_any_of"):
        return prefix in expect
    if prefix == "dimensions":
        return "dimensions_any_of" in expect
    if prefix == "evidence_on_target":
        return bool(expect.get("evidence_all_on_target"))
    if prefix == "no_evidence_matching":
        return bool(expect.get("no_evidence_matching"))
    if prefix == "no_criterion_above":
        return "no_criterion_above" in expect
    # Vocabulary and fabrication are checked on every case that produces
    # evidence, because nothing licenses either of them.
    return True


def write(payload: dict[str, Any], name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="call the real provider (costs tokens)")
    parser.add_argument("--case", action="append", default=None,
                        help="run only these case ids")
    parser.add_argument("--tag", default="",
                        help="suffix for the result file, so repeated runs do not "
                             "overwrite each other")
    args = parser.parse_args()
    tag = f"_{args.tag}" if args.tag else ""

    deterministic = run_deterministic()
    path = write(deterministic, "evaluator_benchmark_deterministic.json")
    print(f"Mode A (deterministic): {deterministic['cases_checked']} cases, "
          f"{len(deterministic['findings'])} findings -> {path.name}")
    for finding in deterministic["findings"]:
        print(f"  [{finding['severity']}] {finding['case_id']} {finding['check']}: "
              f"expected {finding['expected']!r}, got {finding['actual']!r}")
    if not args.live:
        print("\nRe-run with --live to measure the real model.")
        return 0 if deterministic["passed"] else 1
    if not deterministic["passed"]:
        print("\nStopping: the gold labels are not self-consistent, so a live run would "
              "measure the benchmark rather than the evaluator.")
        return 1

    print("\n--- Mode B (real model) ---", flush=True)
    live = run_live(args.case)
    from services.evaluation import depth_config

    live["depth_configuration"] = depth_config.resolve().id
    path = write(live, f"evaluator_benchmark_live{tag}.json")
    totals = live["totals"]
    print(f"cases              {totals['total_cases']}")
    print(f"fully passed       {totals['passed_cases']}  ({totals['pass_rate']})")
    print(f"free of critical   {totals['cases_free_of_critical_failures']}  "
          f"({totals['critical_pass_rate']})")
    print(f"material errors    {totals['material_errors']}")
    print(f"calibration variance {totals['calibration_variances']}")
    depth = live["agreement"]["depth_demonstrated"]
    print(f"depth agreement    {depth['agreed']}/{depth['asserted']} = {depth['rate']}")
    print(f"depth failures     {depth['failed_cases']}")
    print(f"configuration      {live['depth_configuration']}")
    print(f"telemetry          {json.dumps(live['telemetry'])}")
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
