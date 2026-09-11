"""The evaluation CLI.

    python -m evals.cli check-models                  are the model ids still live?
    python -m evals.cli run --all                     every workload, every model
    python -m evals.cli run --workload scoring        one workload
    python -m evals.cli run --workload answer_classifier --model openai/gpt-4.1-mini
    python -m evals.cli report                        re-render from the last run

No web UI, by design. This is a tool for choosing a model once, not a product
surface — and a recorded JSON run is more useful than a dashboard, because it can
be re-graded without paying for the tokens again.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import config as eval_config  # noqa: E402
from evals import report as report_module  # noqa: E402
from evals.harness import WORKLOADS, Run, all_fingerprints, cases_for, run_workload  # noqa: E402
from evals.runner import EvalResult, TaraEvaluationRunner  # noqa: E402
from services import config as app_config  # noqa: E402

RESULTS = eval_config.RESULTS_DIR
LATEST = RESULTS / "latest.json"

#: Stop a run after this many consecutive calls that never reached the provider.
#: Each has already been retried three times with backoff, so this is roughly a
#: minute of a genuinely dead connection rather than a blip.
ABORT_AFTER = 5


# --------------------------------------------------------------------------- #
def cmd_check_models(args) -> int:
    """Ask OpenRouter which of the configured ids still exist.

    The catalogue changes — models are renamed, deprecated and withdrawn — and a
    long run that dies forty cases in because an id went away is an expensive way
    to find that out.
    """
    import httpx

    cfg = eval_config.load(args.config)
    if not app_config.OPENROUTER_API_KEY:
        print("No OPENROUTER_API_KEY set — cannot check the catalogue.", file=sys.stderr)
        return 1

    try:
        response = httpx.get(
            f"{app_config.OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {app_config.OPENROUTER_API_KEY}"},
            timeout=30,
        )
        response.raise_for_status()
        available = {m["id"] for m in response.json().get("data", [])}
    except Exception as exc:  # noqa: BLE001
        print(f"Could not reach the catalogue: {exc}", file=sys.stderr)
        return 1

    missing = 0
    for spec in cfg.models:
        live = spec.model in available
        missing += not live
        print(f"{'ok  ' if live else 'GONE'}  {spec.model:<44} {spec.name}")
    if missing:
        print(f"\n{missing} model id(s) are no longer in the catalogue. "
              f"Edit evals/models.yaml before running.", file=sys.stderr)
    return 1 if missing else 0


# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    cfg = eval_config.load(args.config)
    if not app_config.llm_is_live():
        print("No OPENROUTER_API_KEY set — an evaluation needs a provider.", file=sys.stderr)
        return 1

    workloads = list(WORKLOADS) if args.all else [args.workload]
    if not workloads or workloads == [None]:
        print("Pass --all or --workload <name>.", file=sys.stderr)
        return 2

    RESULTS.mkdir(parents=True, exist_ok=True)
    runner = TaraEvaluationRunner()
    run = Run(config_path=str(args.config), started_at=time.time(),
              fingerprints=all_fingerprints())

    wanted = args.case.split(",") if args.case else None

    def _planned(w: str) -> int:
        cases = cases_for(w, not args.no_injection)
        if wanted:
            cases = [c for c in cases if c["id"] in wanted]
        models = [args.model] if args.model else cfg.models_for(w)
        return len(cases) * len(models)

    total_planned = sum(_planned(w) for w in workloads)
    done = 0
    consecutive_transport_failures = 0
    print(f"{total_planned} calls planned across {len(workloads)} workload(s).\n")

    class Unreachable(RuntimeError):
        """The provider has stopped answering. Stop rather than produce a report
        that says every model is broken."""

    def progress(result: EvalResult) -> None:
        nonlocal done, consecutive_transport_failures
        done += 1

        if result.transport_failure:
            consecutive_transport_failures += 1
        else:
            consecutive_transport_failures = 0

        if result.transport_failure:
            mark = "~"
        elif not result.success:
            mark = "✗"
        elif (result.score or 0) == 1.0:
            mark = "·"
        else:
            mark = "!"

        score = "  —  " if result.score is None else f"{result.score:.0%}"
        retries = f"  ↻{result.retries}" if result.retries else ""
        print(
            f"  [{done:>3}/{total_planned}] {mark} {result.workload:<19} "
            f"{result.model:<34} {result.test_case_id:<34} "
            f"{result.latency_ms:>6} ms  {score}{retries}",
            flush=True,
        )

        # Each of these already retried three times with backoff. Several in a
        # row means the network is down, not that the models are — and a run
        # that keeps going produces a document saying every model failed.
        # A refused key fails every model identically and will not recover on
        # its own. Stop on the first one rather than after five.
        first_error = (result.validation_errors or [""])[0]
        if "provider unavailable" in first_error:
            raise Unreachable(
                f"the provider refused the request — {first_error[:140]}. "
                f"This is an account problem, not a model problem: every remaining "
                f"call would fail the same way."
            )

        if consecutive_transport_failures >= ABORT_AFTER:
            raise Unreachable(
                f"{consecutive_transport_failures} consecutive calls never reached the "
                f"provider. Stopping — check your connection and re-run."
            )

    aborted = False
    for workload in workloads:
        print(f"── {workload} " + "─" * (60 - len(workload)))
        collected: list[EvalResult] = []

        def collect(result: EvalResult) -> None:
            collected.append(result)
            progress(result)

        try:
            run_workload(
                workload,
                cfg,
                runner=runner,
                models=[args.model] if args.model else None,
                case_ids=wanted,
                include_injection=not args.no_injection,
                on_result=collect,
            )
        except Unreachable as exc:
            run.results += collected
            print(f"\n  ABORTED: {exc}", file=sys.stderr)
            aborted = True
            break
        run.results += collected
        print()

    run.finished_at = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"run-{stamp}.json"
    path.write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    LATEST.write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    spent = sum(r.cost_usd for r in run.results)
    unreachable = sum(1 for r in run.results if r.transport_failure)
    failed = sum(1 for r in run.results if not r.success and not r.transport_failure)
    print(
        f"{len(run.results)} calls · {failed} failed · {unreachable} unreachable · "
        f"{run.finished_at - run.started_at:.0f}s · ~${spent:.3f} estimated"
    )
    print(f"saved  {path.relative_to(ROOT)}")

    if aborted:
        print("\nRun aborted — the report was NOT regenerated, because a partial run "
              "would understate every model that had not been reached yet.", file=sys.stderr)
        return 1

    if not args.no_report:
        return _write_report(run, cfg)
    return 0


# --------------------------------------------------------------------------- #
def cmd_report(args) -> int:
    cfg = eval_config.load(args.config)
    source = Path(args.run) if args.run else LATEST
    if not source.exists():
        print(f"No run at {source}. Run `python -m evals.cli run --all` first.",
              file=sys.stderr)
        return 1
    run = Run.from_dict(json.loads(source.read_text(encoding="utf-8")))
    return _write_report(run, cfg)


def _write_report(run: Run, cfg: eval_config.EvalConfig) -> int:
    out = ROOT / "MODEL_EVALUATION.md"
    out.write_text(report_module.render(run, cfg), encoding="utf-8")
    print(f"wrote  {out.relative_to(ROOT)}")
    return 0


# --------------------------------------------------------------------------- #
def cmd_recommend(args) -> int:
    """Write MODEL_RECOMMENDATION.md from a completed run."""
    from evals import recommendation
    from services.ai.gateway import Workload, workload_config

    cfg = eval_config.load(args.config)
    source = Path(args.run) if args.run else LATEST
    if not source.exists():
        print(f"No run at {source}. Run `python -m evals.cli run --all` first.", file=sys.stderr)
        return 1
    run = Run.from_dict(json.loads(source.read_text(encoding="utf-8")))

    # Read from the live config rather than trusting a constant: the document
    # asserts "production model changed: NO", so it must look.
    production = {w.value: workload_config(w).model for w in Workload}
    out = ROOT / "MODEL_RECOMMENDATION.md"
    out.write_text(recommendation.render(run, cfg, production), encoding="utf-8")
    print(f"wrote  {out.relative_to(ROOT)}")
    return 0


def cmd_cases(args) -> int:
    """What is in the datasets, without running anything."""
    for name in (WORKLOADS if not args.workload else [args.workload]):
        cases = cases_for(name)
        real = [c for c in cases if not c.get("_injection")]
        adversarial = [c for c in cases if c.get("_injection")]
        print(f"\n{name}  ({len(real)} cases + {len(adversarial)} adversarial)")
        for case in cases:
            tag = " [adversarial]" if case.get("_injection") else ""
            print(f"  {case['id']}{tag}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals", description=__doc__)
    ap.add_argument("--config", default=eval_config.DEFAULT_CONFIG, type=Path)
    sub = ap.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run an evaluation")
    run.add_argument("--all", action="store_true", help="every workload")
    run.add_argument("--workload", choices=sorted(WORKLOADS))
    run.add_argument("--model", help="one model id, overriding models.yaml")
    run.add_argument("--case", help="comma-separated case ids")
    run.add_argument("--no-injection", action="store_true",
                     help="skip the adversarial cases")
    run.add_argument("--no-report", action="store_true")
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="render MODEL_EVALUATION.md from a run")
    rep.add_argument("--run", help="path to a run json (default: the latest)")
    rep.set_defaults(func=cmd_report)

    rec = sub.add_parser("recommend", help="write MODEL_RECOMMENDATION.md from a run")
    rec.add_argument("--run", help="path to a run json (default: the latest)")
    rec.set_defaults(func=cmd_recommend)

    chk = sub.add_parser("check-models", help="are the configured model ids still live?")
    chk.set_defaults(func=cmd_check_models)

    lst = sub.add_parser("cases", help="list the test cases")
    lst.add_argument("--workload", choices=sorted(WORKLOADS))
    lst.set_defaults(func=cmd_cases)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
