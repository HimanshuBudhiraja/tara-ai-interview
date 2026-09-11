#!/usr/bin/env python3
"""Write the pilot's monitoring report.

    python -m tools.pilot_report                     # the active run
    python -m tools.pilot_report --run pilot_abc123  # a specific one
    python -m tools.pilot_report --all               # everything on disk

Produces two artefacts side by side:

    PILOT_REPORT.md                        what a person reads
    evals/results/pilot_<run>.json         the same numbers, machine-readable
    evals/results/pilot_<run>_dataset.json one row per completed interview

The report is the whole monitoring capability §14 asks for, and deliberately no
more than that: this repository has no metrics backend, and inventing one to
watch three candidates would be a bigger risk than the thing it watches.

Nothing here reads a transcript, a quote or a candidate's name.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.data import pilot  # noqa: E402
from services.pilot import metrics  # noqa: E402

RESULTS = ROOT / "evals" / "results"


def _row(label: str, stats: dict) -> str:
    p95 = "—" if stats.get("p95") is None else f"{stats['p95']:.2f}"
    return (
        f"| {label} | {stats['n']} | {stats['mean']:.2f} | {stats['median']:.2f} "
        f"| {p95} | {stats['max']:.2f} |"
    )


def render(summary: dict, dataset: list[dict]) -> str:
    run = summary.get("pilot_run") or {}
    experience = summary["candidate_experience"]
    quality = summary["evaluation_quality"]
    money = summary["cost_and_latency"]
    stability = summary["stability"]
    review = summary["human_review"]

    lines = [
        "# Pilot report",
        "",
        f"**Run:** `{summary['pilot_run_id'] or 'all runs'}`"
        + (f" — {run.get('label')}" if run.get("label") else ""),
        f"**Engine:** `{summary['engine_version']}` · "
        f"**Model:** `{run.get('configured_model') or 'see per-evaluation provenance'}`",
        "",
        "Counts, rates and durations only. No candidate content appears in this file.",
        "",
        "## Alerts",
        "",
    ]
    if summary["alerts"]:
        lines += ["| Alert | Kind | Observed | Threshold (proposed) | Detail |",
                  "| --- | --- | --- | --- | --- |"]
        for alert in summary["alerts"]:
            lines.append(
                f"| `{alert['key']}` | {alert['severity']} | {alert['observed']} "
                f"| {alert['threshold']} | {alert['detail']} |"
            )
    else:
        lines.append("None. Every proposed threshold held.")

    lines += [
        "",
        "## Candidate experience",
        "",
        f"- attempted **{experience['attempted']}** · completed "
        f"**{experience['completed']}** ({experience['completion_rate']:.0%}) · "
        f"dropped **{experience['dropped']}**",
        f"- device check reported **{experience['system_check_reported']}** · failed "
        f"**{experience['system_check_failed']}** · chose text "
        f"**{experience['system_check_chose_text']}**",
        f"- rejoins **{experience['rejoins']}** · repeats **{experience['repeats']}** · "
        f"clarifications **{experience['clarifications']}** · skips "
        f"**{experience['skips']}** · silences **{experience['silences']}**",
        f"- per answered question: repeat {experience['repeat_rate_per_item']:.2f}, "
        f"clarify {experience['clarify_rate_per_item']:.2f}, "
        f"skip {experience['skip_rate_per_item']:.2f}",
        f"- flagged turns **{experience['flagged_turns']}** · runtime errors "
        f"**{experience['runtime_errors']}**",
        "",
        "| Measure | n | mean | median | p95 | max |",
        "| --- | --- | --- | --- | --- | --- |",
        _row("interview duration (s)", experience["duration_sec"]),
        "",
        "## Evaluation quality",
        "",
        f"- requested **{quality['requested']}** · completed **{quality['completed']}** "
        f"({quality['completion_rate']:.0%}) · failed **{quality['failed']}** "
        f"({quality['failure_rate']:.0%})",
        f"- failures by stage: "
        + (", ".join(f"`{k}`×{v}" for k, v in quality["failures_by_stage"].items()) or "none"),
        f"- evidence checked **{quality['evidence_checked']}** · untraceable "
        f"**{quality['evidence_untraceable']}** · traceability "
        f"**{quality['evidence_traceability_rate']:.3f}**",
        f"- evaluations with zero evidence **{quality['zero_evidence_evaluations']}** · "
        f"quarantine rate **{quality['quarantine_rate']:.2f}**",
        f"- recommendations: "
        + (", ".join(f"{k} ×{v}" for k, v in quality["recommendation_distribution"].items())
           or "none"),
        "",
        "| Measure | n | mean | median | p95 | max |",
        "| --- | --- | --- | --- | --- | --- |",
        _row("evidence items", quality["evidence_items"]),
        _row("quarantined items", quality["quarantined"]),
        _row("repairs", quality["repairs"]),
        _row("coverage %", quality["coverage_percentage"]),
        _row("score %", quality["score_percentage"]),
        "",
        "## Recommendation stability",
        "",
        f"- snapshots evaluated more than once: **"
        f"{stability['sessions_evaluated_more_than_once']}**",
        f"- recommendation flips: **{stability['recommendation_flips']}** "
        f"(rate {stability['instability_rate']:.2f})",
    ]
    for flip in stability["flips"]:
        lines.append(
            f"  - `{flip['session_id'][:8]}` {flip['runs']} runs: "
            f"{' / '.join(flip['recommendations'])} — score spread "
            f"{flip['score_spread_pp']} pp → **{flip['class']}**"
        )

    lines += [
        "",
        "## Cost and latency",
        "",
        "| Measure | n | mean | median | p95 | max |",
        "| --- | --- | --- | --- | --- | --- |",
        _row("evaluation latency (s)", money["evaluation_latency_sec"]),
        _row("model calls per evaluation", money["model_calls"]),
        _row("prompt tokens", money["prompt_tokens"]),
        _row("completion tokens", money["completion_tokens"]),
        _row("evaluation cost (USD)", money["evaluation_cost_usd"]),
        _row("runtime calls per interview", money["runtime_calls_per_interview"]),
        _row("runtime latency (s)", money["runtime_latency_sec"]),
        _row("runtime cost (USD)", money["runtime_cost_usd"]),
    ]
    for stage, stats in money["evaluation_stage_sec"].items():
        lines.append(_row(f"stage · {stage} (s)", stats))
    lines += [
        "",
        f"- mean cost of one complete interview: **${money['interview_total_cost_usd']}**",
        f"- slowest single model call: **{money['slowest_single_call_ms']} ms**",
        f"- provider error rate: **{money['provider_error_rate']:.3f}**",
    ]
    if money["evaluation_latency_sec"]["n"] < 20:
        lines.append(
            "- p95 is withheld: fewer than 20 evaluations. `max` is reported instead, "
            "and no production figure should be extrapolated from this sample."
        )

    lines += [
        "",
        "## Human review",
        "",
        f"- reviewed **{review['reviewed']}** by **{review['reviewers']}** reviewer(s) · "
        f"agree **{review['agree']}** · disagree **{review['disagree']}** · "
        f"needs review **{review['needs_review']}**",
        f"- agreement rate **{review['agreement_rate']:.2f}** · recommendation agreement "
        f"**{review['recommendation_agreement_rate']:.2f}** "
        f"(of {review['recommendation_stated']} stated)",
        f"- disagreement categories: "
        + (", ".join(f"`{k}`×{v}" for k, v in review["reasons"].items()) or "none"),
        "",
        "A disagreement is a review signal, not a verdict about the evaluator. It goes to "
        "root-cause analysis and a benchmark case — never straight into the scoring rules.",
        "",
        "## Dataset",
        "",
        f"{len(dataset)} completed interview(s). Full rows in the JSON beside this file.",
        "",
        "| session | persona | score | coverage | recommendation | evidence | latency (s) | cost | review |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in dataset:
        lines.append(
            f"| `{row['session_id'][:8]}` | {row['persona'] or '—'} | "
            f"{row['score']}/{row['max_score']} ({row['percentage']}%) | "
            f"{row['coverage_percentage']}% | {row['recommendation']} | "
            f"{row['evidence_count']} | {row['evaluation_latency_sec']} | "
            f"${row['cost_usd']} | {row['human_review'] or '—'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="", help="pilot run id (default: the active run)")
    ap.add_argument("--all", action="store_true", help="every session on disk")
    ap.add_argument("--out", default=str(ROOT / "PILOT_REPORT.md"))
    args = ap.parse_args()

    # "" means every session on disk; the API spells the same thing `all`.
    run_id = "" if args.all else (args.run or pilot.active_run_id())
    summary = metrics.summary(run_id)
    rows = metrics.dataset(run_id)

    RESULTS.mkdir(parents=True, exist_ok=True)
    stem = run_id or "all"
    (RESULTS / f"pilot_{stem}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (RESULTS / f"pilot_{stem}_dataset.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path(args.out).write_text(render(summary, rows), encoding="utf-8")

    print(f"run            {run_id or '(all sessions)'}")
    print(f"interviews     {summary['candidate_experience']['completed']}"
          f"/{summary['candidate_experience']['attempted']} completed")
    print(f"evaluations    {summary['evaluation_quality']['completed']}"
          f"/{summary['evaluation_quality']['requested']} completed, "
          f"{summary['evaluation_quality']['failed']} failed")
    print(f"alerts         {len(summary['alerts'])}"
          + (": " + ", ".join(a["key"] for a in summary["alerts"]) if summary["alerts"] else ""))
    print(f"-> {args.out}")
    print(f"-> {RESULTS / f'pilot_{stem}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
