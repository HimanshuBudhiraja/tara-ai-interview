"""The model evaluation harness.

Which model should power each of Tara's six AI workloads? This package exists to
measure that rather than assume it, on Tara's own test cases.

Two rules it is built around:

  * **It is isolated from production.** It calls the production prompts through
    the production gateway with a model override, and writes nothing a candidate
    or a recruiter can see. Running an evaluation cannot change an interview.
  * **It does not pick the winner.** It measures quality, structured-output
    validity, latency, token usage, estimated cost and guardrail pass rate, and
    reports them per workload. A human reads the table and decides.

Grading is deliberately programmatic rather than model-judged. Every check here
is one a person can re-derive from the recorded output — an exact intent match,
a schema violation, a guardrail verdict, a quote that does or does not appear
verbatim in what the candidate said. An LLM judge would let a second model's
opinion decide which model we ship.
"""
