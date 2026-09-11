"""A deterministic stand-in for the evaluation model.

Exists for the same reason the question-generator stub does: the whole
subsystem — snapshotting, validation, persistence, idempotency, the API, the
audit trail — has to be verifiable end to end with no provider, and today there
is no provider (the inference key has no balance).

What it is NOT: a replacement for the extractor, or a deterministic answer to
the extractor-quality problem. The dimension tagging below is keyword matching
and would be a bad way to read an interview. It is a fixture that produces
schema-valid, verbatim-quoted evidence so the code paths around the model are
the real ones — nothing more, and no claim about extraction quality can be made
from anything it produces.

Two properties it is written to have:

  * **Deterministic.** The same transcript yields the same evidence and the same
    criteria, so a persisted evaluation is reproducible in tests.
  * **Honest.** Turning it on is an explicit choice (`TARA_EVAL_STUB=1`). With it
    off and no provider, an evaluation FAILS — visibly, with `error_kind:
    "model"` — rather than quietly producing a score nobody generated.
"""
from __future__ import annotations

import os
import re
from typing import Any

from packages.types.definition import InterviewDefinition, SkillSpec
from packages.types.evaluation import DIMENSION_SUPPORTS, EvidenceItem
from services.evaluation.evidence import (
    ExtractionReport,
    mentions_protected_topic,
    validate_evidence,
)
from services.evaluation.transcript import QuestionTranscript

STUB_ENV = "TARA_EVAL_STUB"


def enabled() -> bool:
    return os.environ.get(STUB_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


#: Keyword → dimension. Crude on purpose and clearly labelled: a real extractor
#: reads the answer, this matches strings.
_CUES: tuple[tuple[str, str], ...] = (
    ("production_judgment", r"\b(in production|at scale|on-?call|incident|throughput|"
                            r"per second|customers?|slo|sla|cost us)\b"),
    ("edge_cases", r"\b(edge case|fails?|failure|retry|retries|timeout|duplicate|"
                   r"crash(?:es|ed)?|partial|breaks?|mismatch)\b"),
    ("trade_offs", r"\b(trade-?off|instead of|rather than|downside|the cost of|"
                   r"we gave up|cheaper|simpler than)\b"),
    ("reasoning", r"\b(because|the reason|so that|which means|that way|otherwise)\b"),
    ("practical_application", r"\b(i (?:built|implemented|wrote|debugged|shipped)|"
                              r"we (?:built|implemented|ran|had|used)|in my last)\b"),
)


def _dimension(text: str) -> str:
    lowered = text.lower()
    for dimension, pattern in _CUES:
        if re.search(pattern, lowered):
            return dimension
    return "conceptual_understanding"


def _sentences(answer: str) -> list[str]:
    """Exact substrings of `answer`, so every quote is verbatim by construction."""
    return [m.group().strip() for m in re.finditer(r"[^.!?]+[.!?]?", answer) if m.group().strip()]


def _quote(answer: str) -> str:
    """The longest sentence that is safe to quote in a hiring document."""
    candidates = [s for s in _sentences(answer) if not mentions_protected_topic(s)]
    if not candidates:
        return ""
    return max(candidates, key=len)


def extract_for_question(
    question: QuestionTranscript,
    skill: SkillSpec,
    definition: InterviewDefinition,
    *,
    session_id: str = "",
) -> tuple[list[EvidenceItem], ExtractionReport]:
    """One evidence item per usable turn, then through the real validator.

    Routing it through `validate_evidence` rather than returning items directly
    means the stub is held to exactly the same gate a model is — a fixture that
    could bypass validation would be testing a path production never takes.
    """
    raw: list[dict[str, Any]] = []
    for turn in question.usable_turns():
        quote = _quote(turn.answer)
        if not quote:
            continue
        words = len(turn.answer.split())
        dimension = _dimension(turn.answer)
        raw.append({
            "turn_id": turn.turn_id,
            "candidate_quote": quote,
            "depth_dimension": dimension,
            "evidence_type": "supported" if words >= 12 else "partial",
            "evidence_strength": (
                "strong" if words >= 45 else "moderate" if words >= 18 else "weak"
            ),
            "supports_criterion": DIMENSION_SUPPORTS.get(dimension, "Depth"),
            "note": "Deterministic stub evidence — not a model judgement.",
        })
    return validate_evidence(raw, question, skill, ExtractionReport())


#: How the evidence lines are rendered into the judge's payload, so the stub can
#: read back what it is being asked about.
_LINE = re.compile(r"^- \[(\w+)/(\w+)/(\w+)/(\w+)\]", re.M)


def judge(payload: str, *, session_id: str = "") -> dict[str, Any]:
    """A criteria verdict derived from the evidence in the payload.

    Not an opinion — arithmetic over how much supported evidence there is, so a
    strong answer and a thin one produce visibly different rows without any
    model being involved.
    """
    lines = _LINE.findall(payload)
    supported = [l for l in lines if l[2] == "supported"]
    strong = [l for l in supported if l[3] == "strong"]
    deep = [l for l in supported if l[1] in ("edge_cases", "production_judgment")]

    base = 1 + min(2, len(supported)) + (1 if strong else 0) + (1 if deep else 0)
    base = max(1, min(5, base))
    clarity = max(1, min(5, 1 + min(3, len(lines))))

    return {
        "discussion_status": "discussed",
        "Accuracy": base,
        "Depth": base,
        "Clarity": clarity,
        "Problem-Solving": max(1, base - (0 if deep else 1)),
        "Communication": clarity,
        "remarks": (
            "The answers were assessed from the evidence recorded against this skill "
            "by the deterministic stub, which stands in for the model while no "
            "provider is configured."
        ),
        "dimensions_demonstrated": sorted({l[1] for l in supported}),
        "evidence_confidence": "medium",
    }
