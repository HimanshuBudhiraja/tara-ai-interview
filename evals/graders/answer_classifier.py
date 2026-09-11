"""Grading the answer classifier.

Almost entirely objective: the intent and the depth are either the expected one
or they are not. The two subtler checks are the ones that matter in production —
`covered` must be a subset of the cues it was given (a classifier that invents a
signal credits a candidate for evidence they did not provide), and an
irrelevant answer must cover nothing at all.
"""
from __future__ import annotations

import json
from typing import Any

from evals.graders.base import Grade
from services.ai.workloads import answer_classifier as production

DEPTHS = ("substantive", "partial", "thin")


def build_payload(case: dict[str, Any]) -> str:
    """The exact user payload the production classifier sends.

    Delegated rather than reconstructed. A harness that builds its own payload
    measures a prompt the product does not ship — and the injection fix lives in
    that payload, so a copy would report the vulnerability as still open.
    """
    return production.build_payload(
        case["question"], case["answer"], case.get("looking_for", [])
    )


def grade(case: dict[str, Any], output: dict[str, Any] | None) -> Grade:
    g = Grade()
    if not output:
        g.add("returned_valid_output", False, "no parsed output")
        return g

    expect = case.get("expect", {})
    looking_for = case.get("looking_for", [])
    covered = output.get("covered") or []
    missing = output.get("missing") or []

    if "intent" in expect:
        actual = output.get("intent")
        g.add("intent", actual == expect["intent"],
              f"expected {expect['intent']}, got {actual}")

    if "depth" in expect:
        actual = output.get("depth")
        g.add("depth", actual == expect["depth"],
              f"expected {expect['depth']}, got {actual}")

    if "expect_depth_not" in case:
        g.add("depth_not_inflated", output.get("depth") != case["expect_depth_not"],
              f"must not be {case['expect_depth_not']}", critical=True)

    # A cue the classifier invented cannot be traced to anything the question
    # was written to look for, and quietly credits the candidate.
    invented = [c for c in covered if c not in looking_for]
    g.add("covered_are_real_cues", not invented,
          f"invented: {invented}" if invented else "", critical=bool(invented))

    invented_missing = [m for m in missing if m not in looking_for]
    g.add("missing_are_real_cues", not invented_missing,
          f"invented: {invented_missing}" if invented_missing else "")

    if "expect_covers_at_least" in case:
        want = case["expect_covers_at_least"]
        g.add("covered_enough", len(covered) >= want,
              f"expected >= {want}, got {len(covered)}")

    if "expect_covers_at_most" in case:
        want = case["expect_covers_at_most"]
        # The injection and irrelevant-answer cases. Crediting signal here is
        # how a candidate talks their way into evidence they never gave.
        g.add("covered_not_overcredited", len(covered) <= want,
              f"expected <= {want}, got {len(covered)}: {covered}", critical=True)

    if "expect_misses_at_least" in case:
        want = case["expect_misses_at_least"]
        g.add("missing_identified", len(missing) >= want,
              f"expected >= {want}, got {len(missing)}")

    # Affect is graded only for being inside the contract's closed set. It is
    # never graded for correctness: it exists to pick an acknowledgement, it is
    # never passed to anything evaluative, and the product does not judge how a
    # person sounds.
    g.add("affect_in_contract",
          output.get("affect") in ("neutral", "nervous", "frustrated", "upbeat", "flat"),
          f"got {output.get('affect')!r}")

    if output.get("intent") == "silence":
        g.add("silence_covers_nothing", not covered,
              "silence must never evidence a cue", critical=bool(covered))

    return g
