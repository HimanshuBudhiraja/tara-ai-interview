"""Is the evaluator's verdict stable, or does it depend on how a thing was said?

Two questions, measured separately:

  **Repeatability (§20).** The same input, several times. The scoring workload
  runs at temperature 0.2, so byte-identical JSON is not expected and not
  required. What must be stable is the decisions a recruiter reads: discussion
  status, both depth figures, whether each criterion lands in the same band, and
  whether the total stays inside the range a reviewer authored.

  **Variation (§19).** The same substance, said differently — reworded, garbled
  by speech-to-text, or answering a differently-phrased question. A verdict that
  moves when the wording moves is measuring prose rather than capability.

Neither is a pass/fail gate on its own. They exist to find brittleness, and the
honest output is a spread rather than a number.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from evals import benchmark as B
from packages.types.evaluation import CRITERIA
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV
from services.evaluation import transcript as T

#: A verdict is materially different if a decision changes, or a criterion moves
#: by more than this. One point on a subjective five-point judgement is the
#: variance the ranges already allow for; two is a different assessment.
CRITERION_TOLERANCE = 1


def stt_variant(text: str) -> str:
    """Realistic speech-to-text damage, applied deterministically.

    Duplicated function words, punctuation stripped, lower-cased, and a few
    homophone substitutions that leave the technical meaning intact. This is
    what the runtime actually receives on the voice channel, so it is the
    variation most worth being robust to — and the one a benchmark can generate
    faithfully rather than by paraphrasing.
    """
    out = text.lower()
    for word, homophone in (
        (" write ", " right "), (" writes ", " rights "), (" their ", " there "),
        (" its ", " it's "), (" then ", " than "),
    ):
        out = out.replace(word, homophone)
    # Stutter the short function words, the way a transcriber doubles them.
    out = re.sub(r"\b(the|we|and|it|to|a|so|of|is|in)\b", r"\1 \1", out)
    out = re.sub(r"[.,;:]", "", out)
    return re.sub(r"\s+", " ", out).strip()


#: Authored equivalents. Paraphrase cannot be generated faithfully, so the
#: rewordings and alternative question phrasings are written by hand and are
#: meant to be substance-preserving — a reviewer should agree that the same
#: verdict is correct for each.
VARIANTS: dict[str, dict[str, Any]] = {
    "acc-01-correct": {
        "reworded": [
            "The request carries an idempotency key derived from the order id. Before we call the "
            "provider we persist an intent row against that key, in the same transaction that "
            "produces the response. A second request with the same key reads that row and returns "
            "the recorded result, so no second charge is ever issued."
        ],
        "question_reworded":
            "Tell me how you'd build payment capture so that a repeated request can't charge a "
            "customer twice.",
    },
    "dep-04-tradeoffs": {
        "reworded": [
            "Catalogue prices went in the cache; nothing customer-specific did. What we accepted "
            "was up to a minute of staleness after a price change — finance agreed to that. The "
            "alternative, invalidating on every publish, was more correct but would have tied our "
            "cache to the pricing team's deploys, and we decided that coupling was the worse of "
            "the two."
        ],
        "question_reworded":
            "Talk me through how you'd choose what to put in a cache in front of pricing.",
    },
    "ps-03-structured-diagnosis": {
        "reworded": [
            "The first thing is how wide the failure is, because that decides where to look — one "
            "payment method, one region, one issuer, or all of it. For a subset I'd take the "
            "failing requests and compare them with the ones that worked; normally one field is "
            "shared. Someone else checks the provider's status at the same time so I'm not "
            "hunting our bug during their outage. The rollback-or-failover decision waits until I "
            "know the shape, because rolling back a schema change does more damage than the "
            "incident."
        ],
        "question_reworded":
            "Right now some customers can't pay. Walk me through your first moves.",
    },
    "clm-01-bare-claim": {
        "reworded": [
            "Kubernetes is something I'm genuinely strong on — years of it, very comfortable, so "
            "this particular thing wouldn't concern me at all."
        ],
        "question_reworded":
            "How do you keep the service up while a Kubernetes rollout is happening?",
    },
}


@dataclass
class Verdict:
    """The decisions a recruiter actually reads, and nothing else."""

    discussion_status: str = ""
    depth_reached: str = ""
    depth_demonstrated: str = ""
    criteria: dict[str, int] = field(default_factory=dict)
    score: int = 0
    in_authored_range: bool = False
    evidence_items: int = 0
    quotes_all_real: bool = True
    criteria_all_canonical: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "discussion_status": self.discussion_status,
            "depth_reached": self.depth_reached,
            "depth_demonstrated": self.depth_demonstrated,
            "criteria": self.criteria,
            "score": self.score,
            "in_authored_range": self.in_authored_range,
            "evidence_items": self.evidence_items,
            "quotes_all_real": self.quotes_all_real,
            "criteria_all_canonical": self.criteria_all_canonical,
            **({"error": self.error} if self.error else {}),
        }


def verdict_for(case: dict[str, Any], session_id: str = "_robustness") -> Verdict:
    """Run one case through the production evaluator and keep only the decisions."""
    definition, state = B.build_interview(case)
    transcript = T.build(state, definition)
    target = case["expect"].get("target_skill") or case["skill"]["id"]
    try:
        items, _ = EV.extract(transcript, definition, session_id=session_id)
        evaluation, _ = E.evaluate(transcript, definition, items, session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        return Verdict(error=f"{type(exc).__name__}: {exc}")

    row = next((r for r in evaluation.skill_assessment if r.skill_id == target), None)
    if row is None:
        return Verdict(error="no assessment row for the target skill")

    turns = {t.turn_id: t for q in T.build(state, definition, scan=False).questions
             for t in q.turns}
    low, high = case["expect"].get("score_range", (0, 25))
    return Verdict(
        discussion_status=row.discussion_status,
        depth_reached=row.depth_evaluation.depth_reached,
        depth_demonstrated=row.depth_evaluation.depth_demonstrated,
        criteria=row.criteria(),
        score=row.score,
        in_authored_range=low <= row.score <= high,
        evidence_items=len([e for e in items if e.skill_id == target]),
        quotes_all_real=all(
            e.turn_id in turns and EV.quote_is_real(e.candidate_quote, turns[e.turn_id].answer)
            for e in items
        ),
        criteria_all_canonical=all(e.supports_criterion in CRITERIA for e in items),
    )


def compare(verdicts: list[Verdict]) -> dict[str, Any]:
    """What moved across a set of runs, and whether any of it is material."""
    usable = [v for v in verdicts if not v.error]
    if len(usable) < 2:
        return {"comparable": False, "errors": [v.error for v in verdicts if v.error]}

    def spread(name: str) -> list[Any]:
        return sorted({getattr(v, name) for v in usable}, key=str)

    drift = {}
    for criterion in CRITERIA:
        values = [v.criteria.get(criterion, 0) for v in usable]
        drift[criterion] = {"values": values, "spread": max(values) - min(values)}

    material: list[str] = []
    for name in ("discussion_status", "depth_reached", "depth_demonstrated"):
        if len(spread(name)) > 1:
            material.append(f"{name} varied: {spread(name)}")
    for criterion, row in drift.items():
        if row["spread"] > CRITERION_TOLERANCE:
            material.append(f"{criterion} moved {row['spread']} points: {row['values']}")
    if len({v.in_authored_range for v in usable}) > 1:
        material.append(
            "the skill total was inside the authored range on some runs and outside on others")
    if not all(v.quotes_all_real for v in usable):
        material.append("a run produced a quote that is not in the transcript")
    if not all(v.criteria_all_canonical for v in usable):
        material.append("a run named a criterion outside the canonical five")

    return {
        "comparable": True,
        "runs": len(usable),
        "scores": [v.score for v in usable],
        "score_spread": max(v.score for v in usable) - min(v.score for v in usable),
        "criterion_drift": drift,
        "stable": {
            "discussion_status": len(spread("discussion_status")) == 1,
            "depth_reached": len(spread("depth_reached")) == 1,
            "depth_demonstrated": len(spread("depth_demonstrated")) == 1,
        },
        "material_instability": material,
        "verdicts": [v.to_dict() for v in usable],
    }


# --------------------------------------------------------------------------- #
#  §20 — repeatability
# --------------------------------------------------------------------------- #
def run_repeatability(case_ids: list[str], runs: int = 3) -> dict[str, Any]:
    """The same input, several times. Identical prose, so any drift is the model."""
    cases = {c["case_id"]: c for c in B.cases()}
    out: dict[str, Any] = {}
    for cid in case_ids:
        verdicts = [verdict_for(cases[cid], session_id="_repeat") for _ in range(runs)]
        out[cid] = compare(verdicts)
    unstable = [cid for cid, r in out.items() if r.get("material_instability")]
    return {
        "mode": "repeatability",
        "runs_per_case": runs,
        "cases": out,
        "materially_unstable": unstable,
        "stable_case_count": len(out) - len(unstable),
    }


# --------------------------------------------------------------------------- #
#  §19 — variation
# --------------------------------------------------------------------------- #
def variants_of(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The same substance, said four ways. The baseline is the case itself."""
    authored = VARIANTS.get(case["case_id"], {})
    out = {"baseline": case}

    if authored.get("reworded"):
        reworded = copy.deepcopy(case)
        reworded["case_id"] = f"{case['case_id']}::reworded"
        reworded["answers"] = authored["reworded"]
        out["reworded"] = reworded

    garbled = copy.deepcopy(case)
    garbled["case_id"] = f"{case['case_id']}::stt"
    garbled["answers"] = [stt_variant(a) for a in case["answers"]]
    out["stt"] = garbled

    if authored.get("question_reworded"):
        requestioned = copy.deepcopy(case)
        requestioned["case_id"] = f"{case['case_id']}::question"
        requestioned["question"] = authored["question_reworded"]
        out["question_reworded"] = requestioned

    return out


def run_variations(case_ids: list[str] | None = None) -> dict[str, Any]:
    """Does the verdict survive a rewording, a garbled transcript, a rephrased question?"""
    cases = {c["case_id"]: c for c in B.cases()}
    selected = case_ids or list(VARIANTS)
    out: dict[str, Any] = {}
    for cid in selected:
        variants = variants_of(cases[cid])
        verdicts = {
            name: verdict_for(variant, session_id="_variation")
            for name, variant in variants.items()
        }
        summary = compare(list(verdicts.values()))
        summary["by_variant"] = {n: v.to_dict() for n, v in verdicts.items()}
        out[cid] = summary
    brittle = [cid for cid, r in out.items() if r.get("material_instability")]
    return {
        "mode": "variation",
        "variants": ["baseline", "reworded", "stt", "question_reworded"],
        "cases": out,
        "brittle": brittle,
        "robust_case_count": len(out) - len(brittle),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--subset", action="append", default=None)
    args = parser.parse_args()

    subset = args.subset or list(VARIANTS)

    print("--- §20 repeatability ---", flush=True)
    repeat = run_repeatability(subset, runs=args.repeat)
    B.write(repeat, "evaluator_repeatability.json")
    for cid, row in repeat["cases"].items():
        flag = row.get("material_instability") or "stable"
        print(f"  {cid:<34} scores={row.get('scores')} spread={row.get('score_spread')}  {flag}")

    print("\n--- §19 variation ---", flush=True)
    variation = run_variations(subset)
    B.write(variation, "evaluator_variation.json")
    for cid, row in variation["cases"].items():
        flag = row.get("material_instability") or "robust"
        print(f"  {cid:<34} scores={row.get('scores')} spread={row.get('score_spread')}  {flag}")

    print(f"\nrepeatable: {repeat['stable_case_count']}/{len(subset)}   "
          f"robust: {variation['robust_case_count']}/{len(subset)}")
    print(json.dumps(B._telemetry("_repeat"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
