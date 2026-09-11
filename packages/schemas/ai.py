"""The shapes each AI workload is allowed to return.

One schema per workload boundary in §13. They are the API between a model and
the rest of the product: change one and you have changed a contract, which is
why they live in `packages/` next to the domain types rather than inside the
service that happens to call them.
"""
from __future__ import annotations

import json
from typing import Any

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}


# --------------------------------------------------------------------------- #
#  Runtime workloads — these run while a candidate is waiting
# --------------------------------------------------------------------------- #
ANSWER_CLASSIFICATION: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "depth", "covered", "missing", "affect"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["answer", "clarify", "repeat", "skip", "meta", "silence"],
        },
        "depth": {"type": "string", "enum": ["substantive", "partial", "thin"]},
        "covered": _STR_LIST,
        "missing": _STR_LIST,
        # Read to choose an acknowledgement and for NOTHING else. It never
        # reaches scoring: the product does not judge how a person sounds.
        "affect": {
            "type": "string",
            "enum": ["neutral", "nervous", "frustrated", "upbeat", "flat"],
        },
        "quote": _STR,
    },
}

FOLLOWUP: dict[str, Any] = {
    "type": "object",
    "required": ["probe"],
    "properties": {"probe": _STR},
}


# --------------------------------------------------------------------------- #
#  Design-time workloads — these run before anyone is invited
# --------------------------------------------------------------------------- #
INTERVIEW_DESIGN: dict[str, Any] = {
    "type": "object",
    "required": ["outcomes", "skills", "tasks"],
    "properties": {
        "outcomes": {"type": "array", "items": _STR, "minItems": 1},
        "skills": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "priority"],
                "properties": {
                    "name": _STR,
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "proficiency_target": {"type": "integer", "minimum": 0, "maximum": 4},
                    "description": _STR,
                    "assessment_scope": _STR,
                    "question_bank": _STR,
                },
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description"],
                "properties": {
                    "description": _STR,
                    "outcome": _STR,
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "required_skills": _STR_LIST,
                },
            },
        },
        "interview_type": {"type": "string", "enum": ["short", "medium", "long"]},
        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
        "recommended_duration_min": {"type": "integer", "minimum": 5, "maximum": 180},
    },
}

#: The §9 designer contract: what a job description becomes before any question
#: exists. Deliberately separate from INTERVIEW_DESIGN (the older shape, still
#: used by the CSR extraction path) rather than mutating it — two callers with
#: different needs are two schemas, and overloading one would break the runtime
#: path that already works.
INTERVIEW_DESIGN_V2: dict[str, Any] = {
    "type": "object",
    "required": ["skills", "tasks", "interview_type", "difficulty",
                 "recommended_duration_min"],
    "properties": {
        "skills": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "required": ["name", "priority", "description", "assessment_scope"],
                "properties": {
                    "name": _STR,
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "description": _STR,
                    # What will actually be probed within this skill. The field
                    # that stops a skill list being a list of nouns.
                    "assessment_scope": _STR,
                },
            },
        },
        "tasks": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "required": ["name", "description", "priority", "skills_assessed"],
                "properties": {
                    "name": _STR,
                    "description": _STR,
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    # Skill NAMES, matched back to the skills above. Validated
                    # server-side: a task assessing a skill that was never
                    # returned is an orphan mapping, and orphan mappings are
                    # refused rather than quietly dropped.
                    "skills_assessed": {"type": "array", "items": _STR, "minItems": 1},
                },
            },
        },
        "interview_type": {"type": "string", "enum": ["short", "medium", "deep"]},
        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
        "recommended_duration_min": {"type": "integer", "minimum": 8, "maximum": 45},
        "rationale": _STR,
    },
}

QUESTION_SET: dict[str, Any] = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["question_text", "skill", "looking_for"],
                "properties": {
                    "question_text": _STR,
                    "skill": _STR,
                    "task": _STR,
                    "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                    "expected_signal": _STR,
                    "looking_for": {"type": "array", "items": _STR, "minItems": 1},
                    "evaluation_criteria": _STR_LIST,
                    "probe_eligible": {"type": "boolean"},
                    "probe_bank": _STR_LIST,
                    "clarify": _STR,
                    "time_budget_sec": {"type": "integer", "minimum": 20, "maximum": 600},
                },
            },
        }
    },
}


# --------------------------------------------------------------------------- #
#  Post-interview workloads
# --------------------------------------------------------------------------- #
#: One blueprint slot's worth of questions. Deliberately narrow: the generator
#: is asked for the questions covering ONE skill/task/difficulty responsibility,
#: not for "the interview". A single response deciding the whole assessment is a
#: single response nobody can review slot by slot or regenerate in isolation.
QUESTION_SLOT: dict[str, Any] = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["prompt", "looking_for", "evaluation_criteria",
                             "probe_bank", "clarify"],
                "properties": {
                    "prompt": _STR,
                    "question_type": {
                        "type": "string",
                        "enum": ["behavioral", "situational", "technical", "task_based"],
                    },
                    "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                    "expected_signal": _STR,
                    # The cues the runtime classifier reads back as covered /
                    # missing. Bounded: an unlimited list is a list nobody can
                    # classify an answer against.
                    "looking_for": {
                        "type": "array", "items": _STR, "minItems": 3, "maxItems": 5,
                    },
                    "evaluation_criteria": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "required": ["label", "description"],
                            "properties": {
                                "label": _STR,
                                "description": _STR,
                                "importance": {
                                    "type": "string", "enum": ["high", "medium", "low"],
                                },
                            },
                        },
                    },
                    "probe_bank": {
                        "type": "array", "items": _STR, "minItems": 2, "maxItems": 4,
                    },
                    "clarify": _STR,
                    "secondary_skills": {"type": "array", "items": _STR},
                    "estimated_base_answer_sec": {
                        "type": "integer", "minimum": 20, "maximum": 400,
                    },
                },
            },
        }
    },
}

SKILL_SCORING: dict[str, Any] = {
    "type": "object",
    "required": ["skills"],
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["skill", "level", "confidence"],
                "properties": {
                    "skill": _STR,
                    "level": {"type": "number", "minimum": 0, "maximum": 5},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": _STR,
                    # Quotes, not paraphrase: a score a reviewer cannot trace
                    # back to something the candidate actually said is a score
                    # they have no way to disagree with.
                    "evidence_quotes": _STR_LIST,
                },
            },
        }
    },
}

#: One question's worth of extracted evidence. The extractor's ONLY job is to
#: say what the candidate said and what it evidences — it does not score, and it
#: is never shown the criteria scale.
EVIDENCE_EXTRACTION: dict[str, Any] = {
    "type": "object",
    "required": ["evidence"],
    "properties": {
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["turn_id", "candidate_quote", "depth_dimension",
                             "evidence_type", "evidence_strength", "supports_criterion"],
                "properties": {
                    "turn_id": _STR,
                    # Must appear VERBATIM in the transcript. Checked in code —
                    # a quotation the candidate never said, inside a document
                    # used to make a hiring decision, is the worst failure this
                    # engine can have.
                    "candidate_quote": _STR,
                    "depth_dimension": {
                        "type": "string",
                        "enum": ["conceptual_understanding", "practical_application",
                                 "reasoning", "trade_offs", "edge_cases",
                                 "production_judgment"],
                    },
                    "evidence_type": {
                        "type": "string",
                        "enum": ["supported", "partial", "contradicted", "missing",
                                 "unclear"],
                    },
                    "evidence_strength": {
                        "type": "string", "enum": ["strong", "moderate", "weak"],
                    },
                    "supports_criterion": {
                        "type": "string",
                        "enum": ["Accuracy", "Depth", "Clarity", "Problem-Solving",
                                 "Communication"],
                    },
                    "note": _STR,
                },
            },
        }
    },
}

def _relax_item_enums(schema: dict[str, Any]) -> dict[str, Any]:
    """The same schema with the ARRAY ITEMS' enums dropped. Shape only."""
    relaxed = json.loads(json.dumps(schema))
    for prop in relaxed["properties"]["evidence"]["items"]["properties"].values():
        prop.pop("enum", None)
    return relaxed


#: What we ASK the model for, and what we AGREE TO READ, are different documents.
#:
#: `EVIDENCE_EXTRACTION` is sent to the provider: every enum spelled out, because
#: the schema is one of the two places the model learns the vocabulary (the
#: prompt is the other, and it now carries all of them explicitly).
#:
#: `EVIDENCE_EXTRACTION_ACCEPT` is what the gateway validates the reply against.
#: It checks the envelope — an object with an `evidence` array whose items carry
#: the required string fields — and leaves the vocabularies to
#: `evidence.validate_evidence`, which examines one item at a time.
#:
#: The reason is a failure mode seen twice against a real model: an enum inside
#: an array is all-or-nothing, so ONE item naming a value from a neighbouring
#: list ("Reasoning" for a criterion, "missing" for a dimension) discarded every
#: other item extracted from that question. Per-item mistakes deserve per-item
#: consequences, and `validate_evidence` gives them exactly that — recovering
#: what is deterministically recoverable, rejecting the rest by name, and
#: recording both on the evaluation record.
EVIDENCE_EXTRACTION_ACCEPT: dict[str, Any] = _relax_item_enums(EVIDENCE_EXTRACTION)


#: One skill's assessment. The model judges the five criteria and writes the
#: remarks; every aggregate above this — totals, rating, recommendation — is
#: computed in code, never asked for.
SKILL_ASSESSMENT: dict[str, Any] = {
    "type": "object",
    "required": ["discussion_status", "Accuracy", "Depth", "Clarity",
                 "Problem-Solving", "Communication", "remarks"],
    "properties": {
        "discussion_status": {
            "type": "string", "enum": ["discussed", "mentioned", "not_discussed"],
        },
        "Accuracy": {"type": "integer", "minimum": 0, "maximum": 5},
        "Depth": {"type": "integer", "minimum": 0, "maximum": 5},
        "Clarity": {"type": "integer", "minimum": 0, "maximum": 5},
        "Problem-Solving": {"type": "integer", "minimum": 0, "maximum": 5},
        "Communication": {"type": "integer", "minimum": 0, "maximum": 5},
        # Qualitative only. A number inside the remarks is a second, unaudited
        # score that will eventually disagree with the first.
        "remarks": _STR,
        # `depth_demonstrated` is deliberately NOT here. It was an advisory
        # field the judge could use to argue a candidate's demonstrated depth
        # DOWN from what their evidence supported. A benchmark run measured what
        # it actually did: the judge answered it from `how_far_the_interview_
        # probed` rather than from the evidence, in 33 of 37 cases, and the
        # concession fired on 10 of 13 depth failures — always downward.
        #
        # The concern it was added for — an extractor over-tagging a dimension
        # the evidence does not really support — already has a control, applied
        # per item at extraction time where the model saw the quote:
        # `evidence_strength`. `evaluator.depth_demonstrated_from` ignores weak
        # and non-supported evidence. Two controls on one concern, one of them
        # measurably answering a different question, is one control too many.
        "dimensions_demonstrated": {"type": "array", "items": _STR},
        "dimensions_missing": {"type": "array", "items": _STR},
        "evidence_confidence": {
            "type": "string", "enum": ["high", "medium", "low", "insufficient"],
        },
    },
}

#: What we ACCEPT from the judge, as distinct from what we ASK for.
#:
#: Same reasoning as `EVIDENCE_EXTRACTION_ACCEPT`, and the same failure produced
#: it. A real `thin` candidate's evaluation failed outright because the judge
#: returned four criteria and omitted `Depth`: the whole run was lost to one
#: absent key, on the candidate whose evaluation is hardest to produce.
#:
#: `apply_constraints` was already built to cope — it reads each criterion with a
#: default and clamps every value into the contract's bounds — so the strictness
#: here was destroying responses the layer below could handle. Only
#: `discussion_status` and `remarks` stay required: without those there is no
#: judgement to constrain. `assess_skill` still refuses a response that dropped
#: more than one criterion, because that is not a judgement with a gap in it.
SKILL_ASSESSMENT_ACCEPT: dict[str, Any] = {
    **SKILL_ASSESSMENT,
    "required": ["discussion_status", "remarks"],
}


REPORT: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": _STR,
        "strengths": _STR_LIST,
        "gaps": _STR_LIST,
        "recommended_followups": _STR_LIST,
    },
}


SCHEMAS: dict[str, dict[str, Any]] = {
    "answer_classifier": ANSWER_CLASSIFICATION,
    "followup_generator": FOLLOWUP,
    "interview_designer": INTERVIEW_DESIGN,
    "interview_designer_v2": INTERVIEW_DESIGN_V2,
    "question_generator": QUESTION_SET,
    "question_slot": QUESTION_SLOT,
    "scoring": SKILL_SCORING,
    "evidence_extraction": EVIDENCE_EXTRACTION,
    "evidence_extraction_accept": EVIDENCE_EXTRACTION_ACCEPT,
    "skill_assessment": SKILL_ASSESSMENT,
    "report_generator": REPORT,
}
