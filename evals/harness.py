"""Wiring: dataset → payload → runner → grader → results.

One place that knows which grader belongs to which workload, so adding a
workload is one entry rather than a change in three files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from evals.config import EvalConfig, ModelSpec
from evals.graders import base
from evals.graders import (
    answer_classifier,
    followup_generator,
    interview_designer,
    question_generator,
    report_generator,
    scoring,
)
from evals.runner import EvalResult, TaraEvaluationRunner
from services.ai.gateway import Workload

DATASETS = Path(__file__).resolve().parent / "datasets"


@dataclass
class WorkloadSpec:
    workload: Workload
    dataset: str
    build_payload: Callable[[dict[str, Any]], str]
    grade: Callable[[dict[str, Any], dict[str, Any] | None], base.Grade]
    #: Runtime workloads answer while a candidate waits. Latency is a
    #: first-class metric for them and a footnote for the others.
    latency_critical: bool = False
    #: Token ceiling for evaluation runs, when the production default is too
    #: tight to measure a verbose or reasoning model fairly. Claude Sonnet 5 and
    #: Gemini 2.5 Pro were both cut off mid-JSON at the designer's production
    #: ceiling of 4,000 — that measured the limit, not the model.
    eval_max_tokens: int | None = None


def _designer_payload(case: dict[str, Any]) -> str:
    return interview_designer.build_payload(case)


def _scoring_payload(case: dict[str, Any]) -> str:
    return scoring.build_payload(case)


WORKLOADS: dict[str, WorkloadSpec] = {
    "answer_classifier": WorkloadSpec(
        Workload.ANSWER_CLASSIFIER, "answer_classifier.json",
        answer_classifier.build_payload, answer_classifier.grade, latency_critical=True,
    ),
    "followup_generator": WorkloadSpec(
        Workload.FOLLOWUP_GENERATOR, "followup_generator.json",
        followup_generator.build_payload, followup_generator.grade, latency_critical=True,
    ),
    "interview_designer": WorkloadSpec(
        Workload.INTERVIEW_DESIGNER, "interview_designer.json",
        _designer_payload, interview_designer.grade, eval_max_tokens=12000,
    ),
    "question_generator": WorkloadSpec(
        Workload.QUESTION_GENERATOR, "question_generator.json",
        question_generator.build_payload, question_generator.grade, eval_max_tokens=12000,
    ),
    "scoring": WorkloadSpec(
        Workload.SCORING, "scoring.json", _scoring_payload, scoring.grade,
        eval_max_tokens=8000,
    ),
    "report_generator": WorkloadSpec(
        Workload.REPORT_GENERATOR, "report_generator.json",
        report_generator.build_payload, report_generator.grade, eval_max_tokens=8000,
    ),
}

#: Adversarial cases route to the workload they target, and are graded by that
#: workload's own grader — a resisted injection is not a separate skill, it is
#: the workload doing its job on hostile input.
INJECTION_DATASET = "injection.json"


def prompt_fingerprint(workload: str) -> str:
    """A hash of what this workload actually sends: system prompt + payload builder.

    Recorded with every run so a later recommendation can tell whether the
    evidence still describes the code. The prompt-injection fix changed both the
    system prompt and the payload shape for four workloads — every result
    measured before it is describing software that no longer exists, and a
    document that recommends from it is quoting a benchmark of a different
    product.
    """
    import hashlib
    import inspect

    from evals.runner import _prompts
    from services.ai.gateway import Workload

    spec = WORKLOADS[workload]
    system, _ = _prompts()[Workload(workload)]
    try:
        builder = inspect.getsource(spec.build_payload)
    except (OSError, TypeError):  # pragma: no cover - a lambda or a builtin
        builder = repr(spec.build_payload)
    blob = (system + "\n" + builder).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def all_fingerprints() -> dict[str, str]:
    return {w: prompt_fingerprint(w) for w in WORKLOADS}


def load_dataset(name: str) -> dict[str, Any]:
    return json.loads((DATASETS / name).read_text(encoding="utf-8"))


def cases_for(workload: str, include_injection: bool = True) -> list[dict[str, Any]]:
    spec = WORKLOADS[workload]
    cases = list(load_dataset(spec.dataset)["cases"])
    if include_injection:
        for case in load_dataset(INJECTION_DATASET)["cases"]:
            if case.get("target") == workload:
                cases.append({**case, "_injection": True})
    return cases


@dataclass
class Run:
    """Every observation from one evaluation, plus what produced it."""

    results: list[EvalResult] = field(default_factory=list)
    config_path: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    #: workload -> hash of the prompt and payload builder used for this run.
    fingerprints: dict[str, str] = field(default_factory=dict)

    def stale_workloads(self) -> list[str]:
        """Workloads whose prompt or payload has changed since this run.

        A run with no fingerprints at all predates the mechanism, so every
        workload in it is treated as stale — which is the safe reading.
        """
        current = all_fingerprints()
        if not self.fingerprints:
            return sorted({r.workload for r in self.results})
        return sorted(
            w for w in {r.workload for r in self.results}
            if self.fingerprints.get(w) != current.get(w)
        )

    def for_workload(self, workload: str) -> list[EvalResult]:
        return [r for r in self.results if r.workload == workload]

    def models_in(self, workload: str) -> list[str]:
        seen: list[str] = []
        for r in self.for_workload(workload):
            if r.model not in seen:
                seen.append(r.model)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "fingerprints": self.fingerprints,
            "results": [r.to_dict() for r in self.results],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Run":
        run = Run(
            config_path=d.get("config", ""),
            started_at=d.get("started_at", 0.0),
            finished_at=d.get("finished_at", 0.0),
            fingerprints=dict(d.get("fingerprints") or {}),
        )
        for raw in d.get("results", []):
            known = set(EvalResult.__dataclass_fields__)
            run.results.append(EvalResult(**{k: v for k, v in raw.items() if k in known}))
        return run


def evaluate_case(
    runner: TaraEvaluationRunner,
    spec: WorkloadSpec,
    model: ModelSpec,
    case: dict[str, Any],
) -> EvalResult:
    """One case, one model: call, grade, price."""
    result = runner.run(
        spec.workload, model.model, case["id"], spec.build_payload(case),
        max_tokens=spec.eval_max_tokens,
    )
    result.cost_usd = model.cost_usd(result.input_tokens, result.output_tokens)

    if result.success:
        grade = spec.grade(case, result.parsed_output)
        result.checks = grade.as_dict()
        result.score = grade.score
        result.notes = [
            f"{c.name}: {c.detail}" for c in grade.checks if not c.passed and c.detail
        ] + grade.notes
    elif result.status == "OUTPUT_TRUNCATED":
        # Not a quality failure: the model was cut off, not wrong. Left unscored
        # and reported separately, with the ceiling that caused it.
        result.score = None
        result.checks = {}
        result.notes.append("output truncated — excluded from scoring; raise the ceiling")
    elif result.transport_failure:
        # The request never reached the provider. Left unscored so it is dropped
        # from the model's quality average rather than counted against it.
        result.score = None
        result.checks = {}
        result.notes.append("transport failure — excluded from scoring")
    else:
        # The model was reached and would not answer usably. That scores zero
        # rather than being dropped: unreliability is a property of the model,
        # not a missing data point.
        result.score = 0.0
        result.checks = {}
    if case.get("_injection"):
        result.notes.append("adversarial case")
    return result


def run_workload(
    workload: str,
    config: EvalConfig,
    *,
    runner: TaraEvaluationRunner | None = None,
    models: list[str] | None = None,
    case_ids: list[str] | None = None,
    include_injection: bool = True,
    on_result: Callable[[EvalResult], None] | None = None,
) -> list[EvalResult]:
    spec = WORKLOADS[workload]
    runner = runner or TaraEvaluationRunner()
    specs = [config.spec(m) for m in models] if models else config.models_for(workload)
    cases = cases_for(workload, include_injection)
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]

    out: list[EvalResult] = []
    for model in specs:
        for case in cases:
            result = evaluate_case(runner, spec, model, case)
            out.append(result)
            if on_result:
                on_result(result)
    return out
