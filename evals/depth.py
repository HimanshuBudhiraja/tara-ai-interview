"""The depth suite: does `depth_demonstrated` describe what the candidate showed?

`evals/benchmark.py` measures the evaluator's decisions across every dimension
at once. This measures one of them, at the resolution needed to fix it, against
`evals/datasets/depth_benchmark.json`.

The design point is **attribution**. `depth_demonstrated` is derived in code from
labels the extractor produces, so a wrong answer has two possible authors:

    candidate answer ─► extraction ─► (dimension, type, strength) ─► derivation ─► depth
                          model                                        code

Each case therefore carries `gold_evidence` — the labels a correct extractor
should have produced. Two runs come out of that:

  * **Mode A, derivation.** Feed the gold labels to `depth_demonstrated_from`
    and check the expected depth. This never calls a provider, and a failure
    here is a code defect in the rule.
  * **Mode B, extraction.** Run the real extractor and the real evaluator over
    the case's transcript. A failure here, when Mode A passed, is the model
    labelling the evidence differently from a reviewer — and the report says
    which dimension it reached for.

Nothing in here is a second evaluator: cases go through `evidence.extract_for_question`
and `evaluator.evaluate`, the same two functions `jobs.run` calls.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.types.evaluation import STAGES, EvidenceItem  # noqa: E402
from evals import benchmark as B  # noqa: E402
from services.evaluation import depth_config  # noqa: E402
from services.evaluation import evaluator as E  # noqa: E402
from services.evaluation import evidence as EV  # noqa: E402
from services.evaluation import transcript as T  # noqa: E402

DATASET = ROOT / "evals" / "datasets" / "depth_benchmark.json"
RESULTS = ROOT / "evals" / "results"


def load() -> dict[str, Any]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def cases(only: list[str] | None = None) -> list[dict[str, Any]]:
    rows = load()["cases"]
    if only:
        wanted = set(only)
        rows = [c for c in rows if c["case_id"] in wanted or c["axis"] in wanted]
    return rows


# --------------------------------------------------------------------------- #
#  Mode A — the derivation, with the extraction taken as given
# --------------------------------------------------------------------------- #
def _gold_items(case: dict[str, Any]) -> list[EvidenceItem]:
    """The case's authored labels as evidence items.

    Only the three fields the derivation reads are meaningful here; the rest are
    filled with placeholders so the dataclass is valid. If the derivation ever
    starts reading something else, this stops being a fair test of it — which is
    a reason to keep the derivation's inputs few.
    """
    return [
        EvidenceItem(
            skill_id=case["skill"]["id"], skill_name=case["skill"]["name"],
            question_id=f"q_{case['case_id']}", turn_id=f"q_{case['case_id']}#0",
            depth_stage="direct",
            depth_dimension=row["depth_dimension"],
            candidate_quote="(gold)",
            evidence_type=row.get("evidence_type", "supported"),
            evidence_strength=row.get("evidence_strength", "strong"),
            supports_criterion="Depth",
        )
        for row in case.get("gold_evidence", [])
    ]


def run_derivation(only: list[str] | None = None) -> dict[str, Any]:
    """Mode A. No provider, no model, no tolerance: the rule either agrees with
    the authored labels or it does not."""
    findings: list[dict[str, Any]] = []
    rows: dict[str, Any] = {}
    for case in cases(only):
        expected = case["expect"]["depth_demonstrated"]
        actual = E.depth_demonstrated_from(_gold_items(case))
        rows[case["case_id"]] = {
            "axis": case["axis"], "expected": expected, "actual": actual,
            "passed": actual == expected,
            "gold_evidence": case.get("gold_evidence", []),
        }
        if actual != expected:
            findings.append({
                "case_id": case["case_id"], "axis": case["axis"],
                "expected": expected, "actual": actual,
                "gold_evidence": case.get("gold_evidence", []),
                "reason": "the derivation disagrees with the authored labels — this is "
                          "a rule defect, not a model one",
            })
    passed = sum(1 for r in rows.values() if r["passed"])
    return {
        "mode": "derivation",
        "totals": {"cases": len(rows), "passed": passed,
                   "agreement": round(passed / len(rows), 3) if rows else 0.0},
        "findings": findings,
        "cases": rows,
    }


# --------------------------------------------------------------------------- #
#  Mode B — the real extractor and the real evaluator
# --------------------------------------------------------------------------- #
@dataclass
class Observed:
    depth_reached: str = ""
    depth_demonstrated: str = ""
    discussion_status: str = ""
    dimensions: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    criteria: dict[str, int] = field(default_factory=dict)
    score: int = 0
    latency_ms: int = 0
    error: str = ""


def observe(
    case: dict[str, Any], session_id: str = "_depth", config: str | None = None
) -> Observed:
    """One case through the production path.

    Assembled here rather than through `benchmark.run_case_live` for one reason:
    that function grades against the main dataset's contract and reports counts.
    Diagnosing depth needs the individual labels — which dimension, at which
    strength, on which quote — because that is where the answer comes from.
    """
    definition, state = B.build_interview(case)
    transcript = T.build(state, definition)
    started = time.perf_counter()
    if config:
        # The extractor reads `TARA_DEPTH_CFG` at call time, so setting it here
        # attributes every call in this observation to one configuration.
        os.environ[depth_config.ENV] = config
    try:
        items, _ = EV.extract(transcript, definition, session_id=session_id)
        evaluation, _ = E.evaluate(transcript, definition, items, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 — a provider failure is a result
        return Observed(error=f"{type(exc).__name__}: {exc}",
                        latency_ms=int((time.perf_counter() - started) * 1000))

    skill_name = case["skill"]["name"]
    row = next(
        (r for r in evaluation.skill_assessment if r.skill_name == skill_name), None
    )
    if row is None:
        return Observed(error=f"no skill row for {skill_name!r}",
                        latency_ms=int((time.perf_counter() - started) * 1000))
    mine = [i for i in items if i.skill_id == case["skill"]["id"]]
    return Observed(
        depth_reached=row.depth_evaluation.depth_reached,
        depth_demonstrated=row.depth_evaluation.depth_demonstrated,
        discussion_status=row.discussion_status,
        dimensions=sorted({i.depth_dimension for i in mine}),
        items=[
            {
                "dimension": i.depth_dimension,
                "type": i.evidence_type,
                "strength": i.evidence_strength,
                "criterion": i.supports_criterion,
                "stage": i.depth_stage,
                "quote": i.candidate_quote[:120],
            }
            for i in mine
        ],
        criteria=row.criteria(),
        score=row.score,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def grade_case(case: dict[str, Any], observed: Observed) -> list[dict[str, Any]]:
    """What this case asserts, checked. Depth first; the rest is context."""
    out: list[dict[str, Any]] = []
    expect = case["expect"]

    if observed.error:
        return [{"case_id": case["case_id"], "axis": case["axis"], "check": "run",
                 "expected": "a completed evaluation", "actual": observed.error,
                 "severity": "critical", "failure_class": "PROVIDER"}]

    if observed.depth_demonstrated != expect["depth_demonstrated"]:
        out.append({
            "case_id": case["case_id"], "axis": case["axis"],
            "check": "depth_demonstrated",
            "expected": expect["depth_demonstrated"],
            "actual": observed.depth_demonstrated,
            "severity": "critical", "failure_class": "DEPTH",
            "dimensions": observed.dimensions,
            "reason": case["reason"],
        })
    if observed.depth_reached != expect["depth_reached"]:
        out.append({
            "case_id": case["case_id"], "axis": case["axis"], "check": "depth_reached",
            "expected": expect["depth_reached"], "actual": observed.depth_reached,
            "severity": "critical", "failure_class": "FIXTURE",
            "reason": "depth_reached is a fact about the transcript; a disagreement "
                      "means the fixture and the runtime disagree about the rungs",
        })
    if "discussion_status" in expect and observed.discussion_status != expect["discussion_status"]:
        out.append({
            "case_id": case["case_id"], "axis": case["axis"], "check": "discussion_status",
            "expected": expect["discussion_status"], "actual": observed.discussion_status,
            "severity": "critical", "failure_class": "SCORING",
        })

    wanted = set(expect.get("dimensions_any_of", []))
    if wanted and not wanted & set(observed.dimensions):
        out.append({
            "case_id": case["case_id"], "axis": case["axis"], "check": "dimensions_any_of",
            "expected": sorted(wanted), "actual": observed.dimensions,
            "severity": "calibration", "failure_class": "EXTRACTION",
            "reason": "none of the dimensions this answer demonstrates was found",
        })
    forbidden = set(expect.get("dimensions_none_of", [])) & set(observed.dimensions)
    if forbidden:
        out.append({
            "case_id": case["case_id"], "axis": case["axis"], "check": "dimensions_none_of",
            "expected": f"none of {sorted(set(expect['dimensions_none_of']))}",
            "actual": sorted(forbidden),
            "severity": "calibration", "failure_class": "EXTRACTION",
            "reason": "a dimension was tagged that the words do not carry",
        })
    for name, (low, high) in (expect.get("criteria") or {}).items():
        value = observed.criteria.get(name)
        if value is not None and not (low <= value <= high):
            out.append({
                "case_id": case["case_id"], "axis": case["axis"],
                "check": f"criterion:{name}", "expected": f"{low}-{high}", "actual": value,
                "severity": "calibration", "failure_class": "SCORING",
            })
    return out


def run_live(only: list[str] | None = None, session_id: str = "_depth") -> dict[str, Any]:
    rows = cases(only)
    findings: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    confusion: Counter = Counter()

    for case in rows:
        observed = observe(case, session_id)
        case_findings = grade_case(case, observed)
        findings.extend(case_findings)
        confusion[(case["expect"]["depth_demonstrated"], observed.depth_demonstrated)] += 1
        observations[case["case_id"]] = {
            "axis": case["axis"],
            "expected_depth": case["expect"]["depth_demonstrated"],
            "observed_depth": observed.depth_demonstrated,
            "expected_reached": case["expect"]["depth_reached"],
            "observed_reached": observed.depth_reached,
            "dimensions": observed.dimensions,
            "evidence": observed.items,
            "criteria": observed.criteria,
            "score": observed.score,
            "discussion_status": observed.discussion_status,
            "latency_ms": observed.latency_ms,
            "error": observed.error,
            "passed": not case_findings,
            "depth_ok": all(f["check"] != "depth_demonstrated" for f in case_findings),
        }
        print(
            f"  {case['case_id']:26} {case['axis']:14} "
            f"{case['expect']['depth_demonstrated']:12} -> {observed.depth_demonstrated or '?':12} "
            f"{'ok ' if observations[case['case_id']]['depth_ok'] else 'MISS'}"
            f" {','.join(observed.dimensions)[:60]}",
            flush=True,
        )

    depth_ok = sum(1 for r in observations.values() if r["depth_ok"])
    clean = sum(1 for r in observations.values() if r["passed"])
    by_axis: dict[str, dict[str, int]] = {}
    for row in observations.values():
        bucket = by_axis.setdefault(row["axis"], {"cases": 0, "depth_ok": 0})
        bucket["cases"] += 1
        bucket["depth_ok"] += int(row["depth_ok"])

    # Did anything derive demonstrated from reached? A perfect correlation over
    # cases authored to disagree would be the signature.
    matched_reached = sum(
        1 for r in observations.values() if r["observed_depth"] == r["observed_reached"]
    )
    return {
        "mode": "live",
        "totals": {
            "cases": len(rows),
            "depth_agreement": round(depth_ok / len(rows), 3) if rows else 0.0,
            "depth_correct": depth_ok,
            "fully_clean": clean,
            "material_depth_errors": sum(
                1 for f in findings
                if f["check"] == "depth_demonstrated" and f["severity"] == "critical"
            ),
            "extraction_findings": sum(1 for f in findings if f["failure_class"] == "EXTRACTION"),
            "observed_equals_reached": matched_reached,
        },
        "by_axis": by_axis,
        "confusion": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items())},
        "findings": findings,
        "cases": observations,
        "telemetry": B._telemetry(session_id),  # noqa: SLF001 — same package
    }


# --------------------------------------------------------------------------- #
#  Repeatability
# --------------------------------------------------------------------------- #
REPEAT_CASES = [
    "dd-direct-02", "dd-probed-01", "dd-deep-02", "dd-deep-03",
    "dd-len-long-01", "dd-len-short-01", "dd-jargon-01", "dd-assert-01",
    "dd-indep-shallow-01", "dd-indep-deep-01", "dd-contra-01", "dd-stt-01",
]


def run_repeatability(runs: int = 3, only: list[str] | None = None) -> dict[str, Any]:
    """The same frozen inputs, several times. Semantic stability, not identical JSON."""
    chosen = only or REPEAT_CASES
    rows = [c for c in cases() if c["case_id"] in set(chosen)]
    per_case: dict[str, Any] = {}
    for case in rows:
        depths, dims, criteria = [], [], []
        for n in range(runs):
            observed = observe(case, session_id=f"_depth_repeat_{n}")
            depths.append(observed.depth_demonstrated)
            dims.append(tuple(sorted(observed.dimensions)))
            criteria.append(tuple(sorted(observed.criteria.items())))
        stable = len(set(depths)) == 1
        per_case[case["case_id"]] = {
            "axis": case["axis"],
            "expected": case["expect"]["depth_demonstrated"],
            "depths": depths,
            "depth_stable": stable,
            "depth_always_right": all(
                d == case["expect"]["depth_demonstrated"] for d in depths
            ),
            "dimension_sets": [list(d) for d in dims],
            "dimensions_stable": len(set(dims)) == 1,
            "criteria_stable": len(set(criteria)) == 1,
            "criterion_max_move": max(
                (max(vals) - min(vals) for vals in zip(
                    *[[v for _, v in c] for c in criteria]
                )), default=0,
            ) if criteria and all(criteria) else 0,
        }
        print(f"  {case['case_id']:26} {'stable' if stable else 'MOVED ':7} {depths}", flush=True)
    stable = sum(1 for r in per_case.values() if r["depth_stable"])
    return {
        "runs": runs,
        "cases": len(per_case),
        "depth_stable_cases": stable,
        "depth_stability": round(stable / len(per_case), 3) if per_case else 0.0,
        "dimension_stable_cases": sum(1 for r in per_case.values() if r["dimensions_stable"]),
        "criteria_stable_cases": sum(1 for r in per_case.values() if r["criteria_stable"]),
        "per_case": per_case,
    }


def write(payload: dict[str, Any], name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="call the configured provider")
    ap.add_argument("--repeat", type=int, default=0, help="repeatability runs")
    ap.add_argument("--case", action="append", default=None, help="case id or axis")
    ap.add_argument("--tag", default="", help="suffix for the result file")
    ap.add_argument("--config", default=None,
                    help="extractor configuration id (see services/evaluation/depth_config.py)")
    ap.add_argument("--runs", type=int, default=0,
                    help="run one configuration N times over the frozen input")
    ap.add_argument("--workers", type=int, default=8,
                    help="cases evaluated concurrently within a run")
    ap.add_argument("--model", default=None,
                    help="override SCORING_MODEL for this experiment only")
    args = ap.parse_args()

    if args.model:
        # Benchmark-only override, applied to the gateway's workload table rather
        # than to any business logic: nothing in the evaluator knows a model
        # name, and the gateway records the model it actually used on every call,
        # so the telemetry proves which one produced the numbers.
        from services.ai import gateway as ai_gateway

        ai_gateway._MODELS[ai_gateway.Workload.SCORING] = args.model  # noqa: SLF001
        print(f"SCORING_MODEL overridden for this run: {args.model}")
    tag = f"_{args.tag}" if args.tag else ""

    if args.runs:
        config = args.config or depth_config.DEFAULT
        payload = run_configuration(config, args.runs, args.case, args.workers)
        agreement = payload["exact_depth_agreement"]
        print(f"\n{config}  ({payload['configuration_summary']})")
        print(f"  runs                    {payload['runs']}")
        print(f"  strict cases            {payload['strict_case_count']}"
              f" of {payload['case_count']}")
        print(f"  exact depth agreement   mean {agreement['mean']} "
              f"min {agreement['min']} max {agreement['max']} sd {agreement['stdev']}")
        errors = payload["material_error_count"]
        print(f"  material errors         mean {errors['mean']} "
              f"min {errors['min']} max {errors['max']}")
        print(f"  repeatability            {payload['repeatability']['identical_across_all_runs']}"
              f"/{payload['repeatability']['of']} = {payload['repeatability']['rate']}"
              f"  (pairwise {payload['pairwise_repeatability']})")
        for stage, row in payload["per_class"].items():
            print(f"  {stage:12} precision {row['precision']} recall {row['recall']} "
                  f"support {row['support']}")
        for name, row in payload["neutrality"].items():
            print(f"  {name:28} mean {row['mean']} min {row['min']} max {row['max']}")
        print(f"  taxonomy                {payload['failure_taxonomy']}")
        if args.model:
            payload["model_override"] = args.model
        print(f"-> {write(payload, f'depth_cfg_{config}{tag}')}")
        return 0

    if args.repeat:
        payload = run_repeatability(args.repeat, args.case)
        print(f"\ndepth stability {payload['depth_stable_cases']}/{payload['cases']} "
              f"= {payload['depth_stability']}")
        print(f"-> {write(payload, f'depth_repeatability{tag}')}")
        return 0

    derivation = run_derivation(args.case)
    print(f"Mode A (derivation): {derivation['totals']['passed']}"
          f"/{derivation['totals']['cases']} agree with the authored labels")
    for finding in derivation["findings"]:
        print(f"  {finding['case_id']:26} expected {finding['expected']:12} "
              f"got {finding['actual']:12} {finding['gold_evidence']}")
    print(f"-> {write(derivation, f'depth_derivation{tag}')}")

    if not args.live:
        print("\nRe-run with --live to measure the real extractor.")
        return 0

    print("\n--- Mode B (real extractor + evaluator) ---")
    live = run_live(args.case)
    totals = live["totals"]
    print(f"\ncases                  {totals['cases']}")
    print(f"depth agreement        {totals['depth_correct']}/{totals['cases']} "
          f"= {totals['depth_agreement']}")
    print(f"material depth errors  {totals['material_depth_errors']}")
    print(f"extraction findings    {totals['extraction_findings']}")
    print(f"depth == reached in    {totals['observed_equals_reached']}/{totals['cases']} cases")
    print("by axis:")
    for axis, row in sorted(live["by_axis"].items()):
        print(f"  {axis:14} {row['depth_ok']}/{row['cases']}")
    print("confusion (expected -> observed):")
    for key, n in live["confusion"].items():
        print(f"  {key:28} {n}")
    print(f"telemetry              {json.dumps(live['telemetry'])}")
    print(f"-> {write(live, f'depth_live{tag}')}")
    return 0




# --------------------------------------------------------------------------- #
#  Phase 5 — one configuration, five runs, over frozen input
# --------------------------------------------------------------------------- #
STRICT_STATUSES = {"AMBIGUOUS_GOLD"}   # excluded from the strict denominator

#: The axes whose neutrality the phase gates on. Each is a set of case ids
#: through `axis`, so a metric cannot drift away from the cases that define it.
NEUTRALITY_AXES = (
    ("length_neutrality", "length"),
    ("jargon_neutrality", "jargon"),
    ("self_assertion_neutrality", "assertion"),
    ("stt_robustness", "stt"),
    ("injection_robustness", "injection"),
    ("contradiction_handling", "contradiction"),
)


def _gold_depth_from_gold_evidence(case: dict[str, Any]) -> str:
    """What the current derivation makes of the case's OWN authored labels."""
    return E.depth_demonstrated_from(_gold_items(case))


def classify_failure(case: dict[str, Any], run: dict[str, Any], depths: list[str]) -> str:
    """Phase 6 — which layer is responsible for this case being wrong.

    Exactly one label per (case, run), decided in a fixed order so the answer
    cannot depend on which check happened to run first:

      1. the dataset already flagged the gold as ambiguous or wrong;
      2. the derivation disagrees with the case's own authored evidence — a rule
         error, whatever the model did;
      3. the model produced a deeper dimension than the authored evidence has —
         a false positive;
      4. the model missed one the authored evidence has — a false negative;
      5. the same case is right in another run — variance rather than bias.
    """
    status = (case.get("depth_reconciliation") or {}).get("status", "")
    if status == "AMBIGUOUS_GOLD":
        return "AMBIGUOUS_GOLD"
    if status == "GOLD_DATA_ERROR":
        return "GOLD_DATA_ERROR"

    expected = case["expect"]["depth_demonstrated"]
    if _gold_depth_from_gold_evidence(case) != expected:
        # The authored labels themselves do not produce the authored depth, so
        # no extraction could have got this right.
        return "DERIVATION_RULE_ERROR"

    gold_dims = {row["depth_dimension"] for row in case.get("gold_evidence", [])}
    seen_dims = set(run.get("dimensions") or [])
    deeper = {d for d, stage in E.DIMENSION_STAGE.items() if stage != "direct"}
    observed_depth = run.get("observed_depth", "")

    if STAGES.index(observed_depth or "direct") > STAGES.index(expected):
        # Went deeper than the gold. Which tag did it reach for that the
        # authored evidence does not contain?
        if (seen_dims & deeper) - gold_dims:
            return "EXTRACTION_FALSE_POSITIVE"
    elif STAGES.index(observed_depth or "direct") < STAGES.index(expected):
        if (gold_dims & deeper) - seen_dims:
            return "EXTRACTION_FALSE_NEGATIVE"

    if len(set(depths)) > 1 and expected in depths:
        return "MODEL_VARIANCE"
    return "EXTRACTION_FALSE_POSITIVE" if STAGES.index(observed_depth or "direct") > \
        STAGES.index(expected) else "EXTRACTION_FALSE_NEGATIVE"


def _precision_recall(pairs: list[tuple[str, str]]) -> dict[str, dict[str, float]]:
    """Per-class precision and recall over (expected, observed) pairs."""
    out: dict[str, dict[str, float]] = {}
    for stage in STAGES:
        predicted = [p for p in pairs if p[1] == stage]
        actual = [p for p in pairs if p[0] == stage]
        hits = [p for p in pairs if p[0] == stage and p[1] == stage]
        out[stage] = {
            "support": len(actual),
            "predicted": len(predicted),
            "precision": round(len(hits) / len(predicted), 3) if predicted else None,
            "recall": round(len(hits) / len(actual), 3) if actual else None,
        }
    return out


def _spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"mean": None, "min": None, "max": None, "stdev": None}
    return {
        "mean": round(statistics.fmean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "stdev": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
    }


def run_configuration(
    config: str, runs: int = 5, only: list[str] | None = None, workers: int = 8
) -> dict[str, Any]:
    """One configuration, N runs over identical frozen input.

    Nothing between runs changes: same cases, same answers, same snapshot
    construction, same model, same gateway settings. The only thing that varies
    is what the provider returns, which is the quantity being measured.
    """
    rows = cases(only)
    per_run: list[dict[str, Any]] = []

    # One configuration for the whole sweep, set once: `observe` reads the
    # environment at call time, and setting it per thread would race.
    os.environ[depth_config.ENV] = config

    for index in range(runs):
        print(f"\n--- {config} · run {index + 1}/{runs} ---", flush=True)
        observations: dict[str, Any] = {}
        # Cases are independent — each builds its own interview and evaluates one
        # skill — so they run concurrently. The provider is the wall clock here,
        # not the harness, and 21 sweeps at four minutes each is a day of waiting
        # for no measurement gain.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    observe, case, f"_depth_{config}_{index}", None
                ): case for case in rows
            }
            done: dict[str, Observed] = {}
            for future in as_completed(futures):
                case = futures[future]
                done[case["case_id"]] = future.result()
        for case in rows:
            observed = done[case["case_id"]]
            observations[case["case_id"]] = {
                "axis": case["axis"],
                "expected_depth": case["expect"]["depth_demonstrated"],
                "observed_depth": observed.depth_demonstrated,
                "expected_reached": case["expect"]["depth_reached"],
                "observed_reached": observed.depth_reached,
                "dimensions": observed.dimensions,
                "evidence": observed.items,
                "criteria": observed.criteria,
                "score": observed.score,
                "latency_ms": observed.latency_ms,
                "error": observed.error,
                "depth_ok": observed.depth_demonstrated == case["expect"]["depth_demonstrated"],
            }
            print(f"  {case['case_id']:26} {case['expect']['depth_demonstrated']:12} -> "
                  f"{observed.depth_demonstrated or '?':12} "
                  f"{'ok ' if observations[case['case_id']]['depth_ok'] else 'MISS'}", flush=True)
        per_run.append(observations)

    return analyse(config, rows, per_run)


def analyse(config: str, rows: list[dict[str, Any]], per_run: list[dict[str, Any]]) -> dict[str, Any]:
    """Phase 5's metric block, plus the Phase 6 taxonomy."""
    runs = len(per_run)
    strict = [
        c for c in rows
        if (c.get("depth_reconciliation") or {}).get("status") not in STRICT_STATUSES
    ]
    strict_ids = {c["case_id"] for c in strict}
    by_id = {c["case_id"]: c for c in rows}

    agreements: list[float] = []
    material: list[int] = []
    pairs_all: list[tuple[str, str]] = []
    for observations in per_run:
        ok = sum(1 for cid, r in observations.items() if cid in strict_ids and r["depth_ok"])
        agreements.append(ok / len(strict_ids))
        material.append(len(strict_ids) - ok)
        pairs_all += [
            (r["expected_depth"], r["observed_depth"])
            for cid, r in observations.items() if cid in strict_ids
        ]

    # Repeatability: identical depth across ALL runs, and over every pair of runs.
    stable = 0
    pair_hits = pair_total = 0
    per_case: dict[str, Any] = {}
    for case in rows:
        cid = case["case_id"]
        depths = [obs[cid]["observed_depth"] for obs in per_run]
        identical = len(set(depths)) == 1
        stable += int(identical and cid in strict_ids)
        for i in range(runs):
            for j in range(i + 1, runs):
                if cid not in strict_ids:
                    continue
                pair_total += 1
                pair_hits += int(depths[i] == depths[j])
        expected = case["expect"]["depth_demonstrated"]
        failures = [
            classify_failure(case, obs[cid], depths)
            for obs in per_run if not obs[cid]["depth_ok"]
        ]
        per_case[cid] = {
            "axis": case["axis"],
            "expected": expected,
            "depths": depths,
            "stable": identical,
            "always_right": all(d == expected for d in depths),
            "never_right": all(d != expected for d in depths),
            "correct_runs": sum(1 for d in depths if d == expected),
            "dimension_sets": [sorted(obs[cid]["dimensions"]) for obs in per_run],
            # The full labels, per run. Kept so a derivation rule can be tested
            # against evidence somebody already paid a provider for — see
            # `rescore`, which needs no calls at all.
            "evidence_runs": [obs[cid]["evidence"] for obs in per_run],
            "failure_classes": sorted(set(failures)),
            "reconciliation": (case.get("depth_reconciliation") or {}).get("status", ""),
        }

    taxonomy: dict[str, int] = {}
    for row in per_case.values():
        for label in row["failure_classes"]:
            taxonomy[label] = taxonomy.get(label, 0) + 1

    neutrality = {}
    for name, axis in NEUTRALITY_AXES:
        ids = [c["case_id"] for c in rows if c["axis"] == axis]
        if not ids:
            continue
        scores = [
            sum(1 for cid in ids if obs[cid]["depth_ok"]) / len(ids) for obs in per_run
        ]
        neutrality[name] = {"cases": len(ids), **_spread(scores)}

    return {
        "configuration": config,
        "configuration_summary": depth_config.resolve(config).summary,
        "runs": runs,
        "case_count": len(rows),
        "strict_case_count": len(strict_ids),
        "excluded_from_strict": [c["case_id"] for c in rows if c["case_id"] not in strict_ids],
        "exact_depth_agreement": _spread(agreements),
        "material_error_count": _spread([float(m) for m in material]),
        "per_class": _precision_recall(pairs_all),
        "repeatability": {
            "identical_across_all_runs": stable,
            "of": len(strict_ids),
            "rate": round(stable / len(strict_ids), 3) if strict_ids else 0.0,
        },
        "pairwise_repeatability": round(pair_hits / pair_total, 3) if pair_total else 0.0,
        "neutrality": neutrality,
        "failure_taxonomy": taxonomy,
        "per_case": per_case,
        "confusion": {
            f"{a}->{b}": pairs_all.count((a, b))
            for a in STAGES for b in STAGES if pairs_all.count((a, b))
        },
        "telemetry": B._telemetry(f"_depth_{config}_0"),  # noqa: SLF001
    }


def rescore(analysis: dict[str, Any], derivation) -> dict[str, Any]:
    """Re-derive depth from stored evidence labels, with no provider calls.

    Separating extraction from derivation pays for itself here: a candidate
    derivation rule can be tested against evidence a previous run already paid
    for, over every run of every configuration, in a second. `derivation` takes
    a list of stored item dicts (dimension / type / strength) and returns a
    stage.
    """
    rows = {c["case_id"]: c for c in cases()}
    strict = {
        cid for cid, r in analysis["per_case"].items()
        if r.get("reconciliation") not in STRICT_STATUSES
    }
    runs = analysis["runs"]
    agreements = [0.0] * runs
    changed: list[dict[str, Any]] = []
    stable = 0

    for cid, row in analysis["per_case"].items():
        expected = rows[cid]["expect"]["depth_demonstrated"]
        depths = [derivation(items) for items in row["evidence_runs"]]
        if cid in strict:
            for n, depth in enumerate(depths):
                agreements[n] += int(depth == expected)
            stable += int(len(set(depths)) == 1)
        if depths != row["depths"]:
            changed.append({
                "case_id": cid, "axis": row["axis"], "expected": expected,
                "was": row["depths"], "now": depths,
            })

    per_run = [a / len(strict) for a in agreements] if strict else []
    return {
        "strict_cases": len(strict),
        "exact_depth_agreement": _spread(per_run),
        "repeatability": round(stable / len(strict), 3) if strict else 0.0,
        "cases_changed": len(changed),
        "changed": changed,
    }


def stored_derivation(items: list[dict[str, Any]]) -> str:
    """The SHIPPED rule, over stored labels. The control for `rescore`."""
    return E.depth_demonstrated_from([
        EvidenceItem(
            skill_id="s", skill_name="s", question_id="q", turn_id="t",
            depth_stage=row.get("stage", "direct"),
            depth_dimension=row["dimension"],
            candidate_quote="(stored)",
            evidence_type=row["type"],
            evidence_strength=row["strength"],
            supports_criterion=row.get("criterion", "Depth"),
        )
        for row in items
    ])


if __name__ == "__main__":
    raise SystemExit(main())
