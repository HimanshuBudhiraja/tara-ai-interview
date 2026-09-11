"""JSON Schemas for every structured thing the AI is allowed to return.

Two jobs, and they matter separately:

  1. They are sent to the provider as `response_format: json_schema`, so a model
     that supports constrained decoding cannot return the wrong shape.
  2. They are checked locally on the way back, because not every model on
     OpenRouter honours (1) — and a workload that trusts the provider's
     enforcement is a workload that breaks the day someone changes the model
     name in an env var.

`validate` is deliberately a small subset of JSON Schema — types, required,
enums, array items, integer bounds. Enough to catch a malformed generation,
small enough to carry no dependency.
"""
from .validate import SchemaError, validate  # noqa: F401
from .ai import (  # noqa: F401
    ANSWER_CLASSIFICATION,
    FOLLOWUP,
    INTERVIEW_DESIGN,
    INTERVIEW_DESIGN_V2,
    QUESTION_SET,
    QUESTION_SLOT,
    REPORT,
    SKILL_SCORING,
    EVIDENCE_EXTRACTION,
    EVIDENCE_EXTRACTION_ACCEPT,
    SKILL_ASSESSMENT,
    SKILL_ASSESSMENT_ACCEPT,
    SCHEMAS,
)
