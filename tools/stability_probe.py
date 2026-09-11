#!/usr/bin/env python3
"""Evaluate one frozen snapshot several times and report what moved.

    python -m tools.stability_probe --session <id> --runs 5

The narrow question from §7: with the published snapshot, the transcript, the
engine version and the model all held still, does the recruiter-facing verdict
stay still? Byte-identical JSON is not required and not expected — the scoring
workload runs at temperature 0.2. What is measured is the decisions a person
reads: the score, the rating, the recommendation, each skill's discussion
status and depth, and each criterion.

Every run is a real, forced re-evaluation through `jobs.request_and_run`, so
this measures the production path rather than a re-implementation of it. Each
one supersedes the last and is kept on disk, which is also what makes the flip
visible afterwards in the pilot's own stability report.

It costs real money: one full evaluation per run.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.types.evaluation import CRITERIA  # noqa: E402
from services.data import sessions as store  # noqa: E402
from services.evaluation import jobs  # noqa: E402

RESULTS = ROOT / "evals" / "results"

#: A criterion at or below this on a discussed skill is what `recommend()` calls
#: severe, and one severe criterion is enough to hold a candidate back from
#: "Proceed". Mirrored here so the probe can report the flag that actually moved
#: rather than inferring it from the recommendation.
SEVERE_AT = 1


def _row(record) -> dict:
    result = record.result
    skills = []
    for skill in result["skill_assessment"]:
        criteria = {name: skill[name] for name in CRITERIA}
        skills.append({
            "skill_name": skill["skill_name"],
            "discussion_status": skill["discussion_status"],
            "score": skill["score"],
            "criteria": criteria,
            "min_criterion": min(criteria.values()),
            "severe": min(criteria.values()) <= SEVERE_AT
                      and skill["discussion_status"] == "discussed",
            "depth_reached": skill["depth_evaluation"]["depth_reached"],
            "depth_demonstrated": skill["depth_evaluation"]["depth_demonstrated"],
        })
    return {
        "evaluation_id": record.evaluation_id,
        "attempt": record.attempt,
        "snapshot_checksum": record.snapshot_checksum,
        "engine_version": record.engine_version,
        "model": (record.model_meta or {}).get("resolved_model", ""),
        "score": result["candidate_details"]["total_score"],
        "max_score": result["maximum_possible_score"],
        "percentage": result["percentage"],
        "rating": result["candidate_details"]["overall_rating"],
        "coverage": (result.get("coverage") or {}).get("coverage_percentage"),
        "recommendation": result["recommendation"],
        "any_severe": any(s["severe"] for s in skills),
        "severe_skills": [s["skill_name"] for s in skills if s["severe"]],
        "evidence_items": len(record.evidence),
        "latency_ms": (record.model_meta or {}).get("latency_ms", 0),
        "skills": skills,
    }


def compare(rows: list[dict]) -> dict:
    """What moved between runs, and whether it was material."""
    scores = [r["percentage"] for r in rows]
    recommendations = sorted({r["recommendation"] for r in rows})
    ratings = sorted({r["rating"] for r in rows})

    criterion_moves: list[dict] = []
    for index, skill in enumerate(rows[0]["skills"]):
        for criterion in CRITERIA:
            values = [r["skills"][index]["criteria"][criterion] for r in rows]
            if len(set(values)) > 1:
                criterion_moves.append({
                    "skill": skill["skill_name"],
                    "criterion": criterion,
                    "values": values,
                    "spread": max(values) - min(values),
                })
    status_moves = [
        {"skill": rows[0]["skills"][i]["skill_name"],
         "values": sorted({r["skills"][i]["discussion_status"] for r in rows})}
        for i in range(len(rows[0]["skills"]))
        if len({r["skills"][i]["discussion_status"] for r in rows}) > 1
    ]
    depth_moves = [
        {"skill": rows[0]["skills"][i]["skill_name"],
         "values": sorted({r["skills"][i]["depth_demonstrated"] for r in rows})}
        for i in range(len(rows[0]["skills"]))
        if len({r["skills"][i]["depth_demonstrated"] for r in rows}) > 1
    ]

    spread = round(max(scores) - min(scores), 2)
    flipped = len(recommendations) > 1
    return {
        "runs": len(rows),
        "score_percentages": scores,
        "score_spread_pp": spread,
        "score_stdev_pp": round(statistics.pstdev(scores), 3) if len(scores) > 1 else 0.0,
        "ratings": ratings,
        "recommendations": recommendations,
        "recommendation_flipped": flipped,
        "any_severe_values": sorted({r["any_severe"] for r in rows}),
        "severe_flag_moved": len({r["any_severe"] for r in rows}) > 1,
        "criterion_moves": criterion_moves,
        "max_criterion_move": max((m["spread"] for m in criterion_moves), default=0),
        "discussion_status_moves": status_moves,
        "depth_demonstrated_moves": depth_moves,
        # §8's rule, applied rather than asserted: a recommendation that changed
        # while the score barely did is the threshold moving, not the reading.
        "classification": (
            "stable" if not flipped
            else "boundary_instability" if spread <= 2.0
            else "scoring_variance"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    state = store.try_load(args.session)
    if state is None:
        print(f"no session {args.session}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    for n in range(args.runs):
        started = time.time()
        # `force` on every run after the first: the point is a NEW evaluation of
        # the same frozen input, not the idempotent answer.
        record = jobs.request_and_run(state, requested_by="stability_probe", force=n > 0)
        if record.status != "completed":
            print(f"run {n + 1}: {record.status} — {record.error}", file=sys.stderr)
            continue
        row = _row(record)
        row["wall_sec"] = round(time.time() - started, 1)
        rows.append(row)
        print(
            f"run {n + 1}: {row['score']}/{row['max_score']} ({row['percentage']}%) "
            f"{row['rating']} · severe={row['any_severe']}"
            f"{' ' + ','.join(row['severe_skills']) if row['severe_skills'] else ''} "
            f"· {row['recommendation']} · {row['wall_sec']}s"
        )

    if len(rows) < 2:
        print("not enough completed runs to compare", file=sys.stderr)
        return 1

    verdict = compare(rows)
    print()
    print(f"score spread   {verdict['score_spread_pp']} pp "
          f"(stdev {verdict['score_stdev_pp']})")
    print(f"recommendation {' / '.join(verdict['recommendations'])}")
    print(f"severe flag    {verdict['any_severe_values']}")
    print(f"criteria moved {len(verdict['criterion_moves'])}, "
          f"largest move {verdict['max_criterion_move']}")
    print(f"classification {verdict['classification'].upper()}")

    payload = {"session_id": args.session, "runs": rows, "verdict": verdict}
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else RESULTS / f"stability_{args.session[:8]}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
