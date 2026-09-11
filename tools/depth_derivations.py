#!/usr/bin/env python3
"""Test candidate depth-derivation rules against evidence already paid for.

    python -m tools.depth_derivations

Every recorded configuration run stores the extractor's per-item labels, so a
candidate derivation rule can be scored over all of them without calling a
provider. That is the whole reason extraction and derivation are separate: the
expensive half is bought once, and the deterministic half can be re-argued as
often as necessary.

The rules below are the ones the phase brief asks about — whether `trade_offs`
should stay at `probed`, move to the deep tier, or be treated as deep only in
some deterministic context. Each is applied to the stored labels of every run of
every configuration, and reported with the shipped rule as the control.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.types.evaluation import STAGES, deeper_of  # noqa: E402
from evals import depth as D  # noqa: E402
from services.evaluation import evaluator as E  # noqa: E402

DEEP = {"edge_cases", "production_judgment"}


def _qualifying(items: list[dict]) -> list[dict]:
    """The items the shipped rule lets carry a stage at all."""
    return [
        row for row in items
        if row["type"] == "supported" and row["strength"] != "weak"
    ]


def shipped(items: list[dict]) -> str:
    """The control: `trade_offs` at probed, strong required past direct."""
    return D.stored_derivation(items)


def tradeoffs_deep(items: list[dict]) -> str:
    """`trade_offs` in the deep tier, on strength alone."""
    demonstrated = "direct"
    for row in _qualifying(items):
        stage = E.DIMENSION_STAGE.get(row["dimension"], "direct")
        if row["dimension"] == "trade_offs":
            stage = "deep_probed"
        if stage != "direct" and row["strength"] != "strong":
            continue
        demonstrated = deeper_of(demonstrated, stage)
    return demonstrated


def tradeoffs_corroborated(items: list[dict]) -> str:
    """`trade_offs` reaches deep only when the answer carries something else too.

    The idea being tested: a generous trade-off tag on a thin answer is the
    failure mode, so require the trade-off to sit beside another strong,
    non-conceptual item before it carries the deepest stage.
    """
    qualifying = _qualifying(items)
    strong_dims = {r["dimension"] for r in qualifying if r["strength"] == "strong"}
    corroborated = bool(
        (strong_dims - {"trade_offs", "conceptual_understanding"})
    )
    demonstrated = "direct"
    for row in qualifying:
        stage = E.DIMENSION_STAGE.get(row["dimension"], "direct")
        if row["dimension"] == "trade_offs" and corroborated:
            stage = "deep_probed"
        if stage != "direct" and row["strength"] != "strong":
            continue
        demonstrated = deeper_of(demonstrated, stage)
    return demonstrated


def deep_needs_two(items: list[dict]) -> str:
    """The other direction: the deep tier needs two strong deep-tier items.

    Tests whether the over-tagging problem is better addressed by making the
    deepest stage harder to reach at all, rather than by moving a dimension.
    """
    qualifying = _qualifying(items)
    strong = [r for r in qualifying if r["strength"] == "strong"]
    deep_items = [r for r in strong if r["dimension"] in DEEP]
    demonstrated = "direct"
    for row in qualifying:
        stage = E.DIMENSION_STAGE.get(row["dimension"], "direct")
        if stage == "deep_probed" and len(deep_items) < 2:
            stage = "probed"
        if stage != "direct" and row["strength"] != "strong":
            continue
        demonstrated = deeper_of(demonstrated, stage)
    return demonstrated


RULES = {
    "shipped (trade_offs=probed)": shipped,
    "trade_offs=deep": tradeoffs_deep,
    "trade_offs=deep if corroborated": tradeoffs_corroborated,
    "deep tier needs two strong deep items": deep_needs_two,
}


def main() -> int:
    results = sorted(Path(ROOT / "evals" / "results").glob("depth_cfg_depth_cfg_*.json"))
    if not results:
        print("no configuration runs recorded yet", file=sys.stderr)
        return 1

    out: dict[str, dict] = {}
    for path in results:
        analysis = json.loads(path.read_text())
        config = analysis["configuration"]
        if "evidence_runs" not in next(iter(analysis["per_case"].values()), {}):
            continue
        print(f"\n{config}  ({analysis['runs']} runs, "
              f"{analysis['strict_case_count']} strict cases)")
        print(f"  {'rule':40} {'mean':>6} {'min':>6} {'max':>6} {'sd':>6} "
              f"{'repeat':>7} {'moved':>6}")
        for name, rule in RULES.items():
            scored = D.rescore(analysis, rule)
            agreement = scored["exact_depth_agreement"]
            print(f"  {name:40} {agreement['mean']:>6} {agreement['min']:>6} "
                  f"{agreement['max']:>6} {agreement['stdev']:>6} "
                  f"{scored['repeatability']:>7} {scored['cases_changed']:>6}")
            out.setdefault(config, {})[name] = {
                "agreement": agreement,
                "repeatability": scored["repeatability"],
                "cases_changed": scored["cases_changed"],
                "changed": scored["changed"][:12],
            }
    path = ROOT / "evals" / "results" / "depth_derivation_rules.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
