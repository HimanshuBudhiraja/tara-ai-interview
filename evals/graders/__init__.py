"""Graders — one per workload, all programmatic.

Every check in this package is one a person can re-derive from the recorded
output: an exact intent match, a schema violation, a production guardrail
verdict, a quote that does or does not appear verbatim in what the candidate
said. Nothing here asks a model whether another model did well.

That is a deliberate limit, and it has a cost: "is this a good interview
question?" is not fully checkable this way, so the question-generator grader
measures the parts that are (does it target the intended skill, is it grounded
in the task, is it usable by the runtime, does it leak the rubric, does it stray
into protected characteristics) and leaves the last judgement to a person
reading the generated questions in the report. An LLM judge would hide that gap
rather than close it — and would let a second model's opinion decide which model
we ship.
"""
from .base import Check, Grade  # noqa: F401
