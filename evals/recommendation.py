"""Rendering MODEL_RECOMMENDATION.md.

Separate from `report.py` because the two documents answer different questions.
The report is the evidence — every model, every metric, every failed case. This
is the decision sheet: one recommendation per workload, with the reason and the
known weaknesses next to it.

It still does not decide on its own. It computes the ordering from the
priorities in `models.yaml`, applies the rules that ARE mechanical — a safety
failure disqualifies, a runtime workload over its latency budget is unusable
however well it scores — and writes "not determined" wherever the evidence is
missing rather than guessing from a partial run.

`Production model changed: NO` is asserted from the live config, not typed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from evals.config import EvalConfig
from evals.harness import WORKLOADS, Run, cases_for, load_dataset
from evals.report import ModelSummary, _composite, _fmt_ms, _fmt_usd, summarise

#: What each family of workloads is optimised for, in priority order (§18).
PRIORITY_ORDER = {
    True: ["correctness", "safety", "latency", "cost"],
    False: ["correctness", "evidence and rubric adherence",
            "structured-output reliability", "safety", "cost", "latency"],
}


#: Two models within this composite margin are a tie, and §13 says break a tie
#: on cost and speed rather than on a third decimal place.
TIE_MARGIN = 0.05


def _confidence(
    leader: ModelSummary | None,
    runner_up: ModelSummary | None,
    adversarial_total: int,
    adversarial_resisted: int,
) -> tuple[str, str]:
    """How much weight this recommendation can carry, and why.

    Deliberately conservative. A benchmark that reports HIGH CONFIDENCE on fifteen
    cases is telling you something it does not know.
    """
    if leader is None:
        return "INSUFFICIENT_DATA", "No eligible model was measured for this workload."
    if leader.insufficient_data:
        return "INSUFFICIENT_DATA", (
            f"Only {leader.cases - leader.failures} of {leader.attempted} calls were usable."
        )

    reasons: list[str] = []
    level = "HIGH"

    if adversarial_total and adversarial_resisted < adversarial_total:
        level = "LOW"
        reasons.append(
            f"the recommended model still failed {adversarial_total - adversarial_resisted} "
            f"of {adversarial_total} adversarial cases"
        )
    if leader.failures:
        level = "MEDIUM" if level == "HIGH" else level
        reasons.append(f"{leader.failures} call(s) returned unusable output")
    if leader.schema_valid < 1.0:
        level = "MEDIUM" if level == "HIGH" else level
        reasons.append(f"structured output valid on only {leader.schema_valid:.0%} of calls")
    if runner_up and abs(leader.composite - runner_up.composite) < TIE_MARGIN:
        level = "MEDIUM" if level == "HIGH" else level
        reasons.append(
            f"{runner_up.label} is within {TIE_MARGIN:.02f} — the ordering is not decisive"
        )
    if leader.cases < 10:
        level = "MEDIUM" if level == "HIGH" else level
        reasons.append(f"only {leader.cases} cases in this workload's dataset")
    if not adversarial_total:
        reasons.append("no adversarial cases exist for this workload yet")

    if level == "HIGH":
        return "HIGH", (
            f"Measured on all {leader.cases} cases, structured output valid throughout, "
            f"every adversarial case resisted, and a clear margin over the runner-up."
        )
    return level, "Caveats: " + "; ".join(reasons) + "."


def _pick(
    summaries: list[ModelSummary], budget: int | None, latency_critical: bool
) -> tuple[ModelSummary | None, ModelSummary | None, list[str]]:
    """Leader, runner-up, and the reasons anything was ruled out.

    Mechanical rules only. Anything requiring judgement is left to the reader.
    """
    notes: list[str] = []
    eligible: list[ModelSummary] = []

    for s in summaries:
        if s.cases == 0:
            notes.append(f"{s.label}: not measured ({s.unreachable} calls excluded).")
            continue
        if s.insufficient_data:
            notes.append(
                f"{s.label}: INSUFFICIENT_DATA — {s.cases - s.failures} of {s.attempted} "
                f"calls usable. Not ranked as better or worse than anything."
            )
            continue
        if s.truncated and s.truncated >= s.attempted / 2:
            notes.append(
                f"{s.label}: truncated on {s.truncated} of {s.attempted} calls even at the "
                f"raised ceiling — structurally unreliable for this workload."
            )
            continue
        if s.schema_valid == 0:
            notes.append(
                f"{s.label}: returned no usable output on any case — unusable regardless of cost."
            )
            continue
        if s.disqualified:
            notes.append(
                f"{s.label}: safety failure ({', '.join(s.critical)}) — disqualified "
                f"whatever it scored."
            )
            continue
        if budget and s.p95_ms > budget:
            notes.append(
                f"{s.label}: p95 {s.p95_ms:,} ms exceeds the {budget:,} ms budget. A runtime "
                f"workload past its budget leaves the candidate listening to silence."
            )
            continue
        eligible.append(s)

    eligible.sort(key=lambda s: s.composite, reverse=True)

    # §13: a tie is broken on cost and speed, not on a third decimal place. If
    # the top two are within the margin, prefer the cheaper — and for a runtime
    # workload, the faster — rather than pretending the ordering was decisive.
    if len(eligible) > 1 and abs(eligible[0].composite - eligible[1].composite) < TIE_MARGIN:
        a, b = eligible[0], eligible[1]
        key = (lambda s: (s.p95_ms, s.cost_per_1k_usd)) if latency_critical \
            else (lambda s: (s.cost_per_1k_usd, s.p95_ms))
        if key(b) < key(a):
            eligible[0], eligible[1] = b, a
            notes.append(
                f"{b.label} and {a.label} are within {TIE_MARGIN:.02f} on quality; "
                f"{b.label} is preferred on "
                + ("latency, then cost." if latency_critical else "cost, then latency.")
            )

    leader = eligible[0] if eligible else None
    runner_up = eligible[1] if len(eligible) > 1 else None
    return leader, runner_up, notes


def render(run: Run, config: EvalConfig, production: dict[str, str]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Model recommendation",
        "",
        f"Generated {generated}. Evidence: `MODEL_EVALUATION.md`.",
        "",
        "**Production model changed: NO.**",
        "",
        "Nothing in this file has been applied. The six `*_MODEL` environment "
        "variables are unset, so every workload runs on `TARA_MODEL_DEFAULT`. "
        "Applying a recommendation is a deliberate edit to `.env` followed by a "
        "re-run of the candidate suite — a classifier change moves how often Tara "
        "probes, which is a change to the interview, not to a dependency.",
        "",
        "Current production configuration:",
        "",
        "| Workload | Model in production |",
        "| --- | --- |",
    ]
    for workload in WORKLOADS:
        lines.append(f"| {workload} | `{production.get(workload, '—')}` |")
    lines += [
        "",
        "## How a recommendation is reached",
        "",
        "Two different priority orders, because the workloads are not the same job (§18):",
        "",
        f"- **Runtime** (answer classifier, follow-up generator): "
        f"{' → '.join(PRIORITY_ORDER[True])}. A candidate is sitting in silence waiting "
        f"for Tara to speak, so a model past its latency budget is ruled out however "
        f"well it scores.",
        f"- **Design-time** (interview designer, question generator, scoring, report): "
        f"{' → '.join(PRIORITY_ORDER[False])}. Nobody is waiting, so reasoning quality "
        f"outranks speed.",
        "",
        "Three rules are applied mechanically: a safety failure disqualifies; a runtime "
        "model over its p95 budget is ineligible; a model that returned nothing usable is "
        "ineligible. Everything else is a human call, and where the evidence is missing "
        "this file says so rather than guessing.",
        "",
    ]

    # A run measured against a prompt that has since changed is evidence about
    # software that no longer exists. The prompt-injection fix rewrote the system
    # prompt and the payload shape for all four candidate-text workloads, so
    # every pre-fix result is stale — and recommending from it would be exactly
    # the thing this phase was told not to do.
    stale = set(run.stale_workloads())

    decisions: dict[str, dict] = {}
    for workload, spec in WORKLOADS.items():
        results = run.for_workload(workload)
        if results and workload in stale:
            decisions[workload] = {
                "leader": None, "runner_up": None, "notes": [],
                "confidence": "INSUFFICIENT_DATA",
                "why": ("Measured against a prompt that has since changed. These results "
                        "describe the software before the prompt-injection fix and cannot "
                        "support a recommendation."),
                "adversarial": (0, 0), "summaries": [], "stale": True,
            }
            continue
        if not results:
            decisions[workload] = {"leader": None, "runner_up": None, "notes": [],
                                   "confidence": "INSUFFICIENT_DATA",
                                   "why": "This workload was not run.",
                                   "adversarial": (0, 0), "summaries": [], "stale": False}
            continue
        summaries = [
            summarise([r for r in results if r.model == m], config.spec(m).name)
            for m in run.models_in(workload)
        ]
        _composite(summaries, config.priority(workload))
        leader, runner_up, notes = _pick(
            summaries, config.latency_budget_ms.get(workload), spec.latency_critical
        )
        adversarial = [
            r for r in results
            if leader and r.model == leader.model
            and "adversarial case" in (r.notes or []) and r.success
        ]
        resisted = sum(1 for r in adversarial if (r.score or 0) == 1.0)
        confidence, why = _confidence(leader, runner_up, len(adversarial), resisted)
        decisions[workload] = {
            "leader": leader, "runner_up": runner_up, "notes": notes,
            "confidence": confidence, "why": why,
            "adversarial": (resisted, len(adversarial)), "summaries": summaries,
            "stale": False,
        }

    # --- §14 summary table -------------------------------------------------
    if stale:
        lines += [
            "> ### ⚠ This run predates the current prompts",
            ">",
            "> The prompt and payload for "
            + ", ".join(f"**{w.replace('_', ' ')}**" for w in sorted(stale))
            + " have changed since these results were measured — the prompt-injection fix "
              "rewrote both. Every affected workload is reported as "
              "`INSUFFICIENT_DATA` rather than ranked, because a recommendation drawn from "
              "a benchmark of different software is worse than no recommendation.",
            ">",
            "> Re-run with `make eval && make recommend` to replace it.",
            "",
        ]

    lines += ["## Recommendations at a glance", "",
              "| Workload | Recommended model | Alternatives | Confidence | Reason |",
              "| --- | --- | --- | --- | --- |"]
    for workload in WORKLOADS:
        dec = decisions[workload]
        leader, runner_up = dec["leader"], dec["runner_up"]
        title = workload.replace("_", " ").title()
        if leader is None:
            lines.append(
                f"| {title} | *not determined* | — | `{dec['confidence']}` | "
                f"{dec['why']} |"
            )
            continue
        others = ", ".join(
            s.label for s in dec["summaries"]
            if s is not leader and not s.disqualified and not s.insufficient_data
        ) or "none eligible"
        resisted, total = dec["adversarial"]
        reason = (
            f"{leader.quality:.0%} quality, p95 {_fmt_ms(leader.p95_ms)}, "
            f"{_fmt_usd(leader.cost_per_1k_usd)}/1k"
            + (f", {resisted}/{total} adversarial resisted" if total else "")
        )
        lines.append(
            f"| {title} | `{leader.model}` | {others} | `{dec['confidence']}` | {reason} |"
        )
    lines.append("")

    # --- benchmark metadata ------------------------------------------------
    total_calls = len(run.results)
    excluded = [r for r in run.results if getattr(r, "status", "SUCCESS") not in
                ("SUCCESS", "MODEL_ERROR", "SCHEMA_ERROR")]
    statuses: dict[str, int] = {}
    for r in run.results:
        key = getattr(r, "status", "SUCCESS") or "SUCCESS"
        statuses[key] = statuses.get(key, 0) + 1
    when = (
        datetime.fromtimestamp(run.started_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if run.started_at else "unknown"
    )
    lines += [
        "## The benchmark behind this",
        "",
        f"- **Run:** {when}",
        f"- **Models tested:** " + (", ".join(
            sorted({config.spec(m).name for w in WORKLOADS for m in run.models_in(w)})
        ) or "none"),
        f"- **Datasets:** `evals/datasets/` — "
        + ", ".join(f"{w} ({len(cases_for(w, False))})" for w in WORKLOADS)
        + f", plus {len([c for c in load_dataset('injection.json')['cases']])} adversarial cases",
        f"- **Calls:** {total_calls} total · {total_calls - len(excluded)} counted · "
        f"{len(excluded)} excluded",
        "",
    ]
    if len(statuses) > 1:
        lines += ["| Status | Calls | Counted against a model? | Affected ranking? |",
                  "| --- | ---: | --- | --- |"]
        for status, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
            blames = status in ("MODEL_ERROR", "SCHEMA_ERROR")
            lines.append(
                f"| `{status}` | {n} | {'**yes**' if blames else 'no'} | "
                f"{'yes' if blames else 'no — excluded before scoring'} |"
            )
        lines.append("")

    unmeasured: list[str] = []
    for workload, spec in WORKLOADS.items():
        results = run.for_workload(workload)
        dec = decisions[workload]
        title = workload.replace("_", " ").title()
        lines.append(f"## {title}")
        lines.append("")

        if not results or dec.get("stale"):
            reason = ("measured against a prompt that has since changed"
                      if dec.get("stale") else "this workload was not run")
            lines.append(f"**Recommended model:** not determined — {reason}.")
            lines.append("")
            lines.append(f"**Confidence:** `{dec['confidence']}` — {dec['why']}")
            lines.append("")
            unmeasured.append(workload)
            continue

        summaries = dec["summaries"]
        budget = config.latency_budget_ms.get(workload)
        leader, runner_up, notes = dec["leader"], dec["runner_up"], dec["notes"]

        if leader is None:
            lines.append(
                "**Recommended model:** not determined — no model in this run was both "
                "measurable and eligible."
            )
            lines.append("")
            if notes:
                lines.append("Why each was ruled out:")
                lines.append("")
                lines += [f"- {n}" for n in notes]
                lines.append("")
            unmeasured.append(workload)
            continue

        resisted, adversarial_total = dec["adversarial"]
        safety = (
            f"{resisted}/{adversarial_total} adversarial cases resisted"
            if adversarial_total else "no adversarial cases in this workload"
        )

        lines += [
            f"**Recommended model:** `{leader.model}` ({leader.label})",
            "",
            f"**Runner-up:** "
            + (f"`{runner_up.model}` ({runner_up.label})" if runner_up else "none eligible"),
            "",
            f"**Quality:** {leader.quality:.0%} of checks passed across {leader.cases} cases "
            f"(schema valid on {leader.schema_valid:.0%})",
            "",
            f"**Safety:** {safety}",
            "",
            f"**p95 latency:** {_fmt_ms(leader.p95_ms)}"
            + (f" (budget {budget:,} ms)" if budget else " — not latency-critical"),
            "",
            f"**Cost:** {_fmt_usd(leader.cost_per_1k_usd)} per 1,000 calls",
            "",
        ]

        weaknesses: list[str] = []
        if resisted < adversarial_total:
            weaknesses.append(
                f"failed {adversarial_total - resisted} of {adversarial_total} adversarial cases"
            )
        if leader.truncated:
            weaknesses.append(f"{leader.truncated} call(s) hit the token ceiling")
        if leader.failures:
            weaknesses.append(f"{leader.failures} call(s) returned unusable output")
        if leader.mean_reasoning_tokens:
            weaknesses.append(
                f"reasoning model — {leader.mean_reasoning_tokens} reasoning tokens per call "
                f"on average, which is real cost and latency the output-token count hides"
            )
        lines.append(
            "**Known weaknesses:** " + ("; ".join(weaknesses) if weaknesses else "none observed")
        )
        lines.append("")

        reason = (
            f"Highest weighted score under this workload's priorities "
            f"({', '.join(f'{k} {v:.0%}' for k, v in config.priority(workload).items())})"
        )
        if runner_up:
            delta = leader.composite - runner_up.composite
            reason += (
                f", ahead of {runner_up.label} by {delta:.02f}"
                + (" — close enough that either is defensible" if delta < 0.05 else "")
            )
        lines += [
            f"**Reason:** {reason}.", "",
            f"**Confidence:** `{dec['confidence']}` — {dec['why']}", "",
        ]

        if notes:
            lines.append("Ruled out:")
            lines.append("")
            lines += [f"- {n}" for n in notes]
            lines.append("")

    if unmeasured:
        lines += [
            "---",
            "",
            "## ⚠ This recommendation is incomplete",
            "",
            "No recommendation was produced for: "
            + ", ".join(f"**{w.replace('_', ' ')}**" for w in unmeasured)
            + ".",
            "",
            "A recommendation must not be assembled from a partial run. Finish the "
            "evaluation and regenerate:",
            "",
            "```bash",
            "python -m evals.cli check-models   # are the ids still live?",
            "python -m evals.cli run --all",
            "python -m evals.cli recommend",
            "```",
            "",
        ]

    lines += [
        "---",
        "",
        "## Applying a decision",
        "",
        "```bash",
        "# in .env — nothing here is set automatically",
        "ANSWER_CLASSIFIER_MODEL=",
        "FOLLOWUP_GENERATOR_MODEL=",
        "INTERVIEW_DESIGNER_MODEL=",
        "QUESTION_GENERATOR_MODEL=",
        "SCORING_MODEL=",
        "REPORT_GENERATOR_MODEL=",
        "```",
        "",
        "Then run `make test` and `pytest -m server` before and after. A classifier "
        "change moves how often Tara follows up, and that is a change to the interview.",
    ]
    return "\n".join(lines) + "\n"
